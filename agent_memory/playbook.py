"""Playbook parsing, retrieval, and formatting.

Playbooks are collections of rules organized by section (e.g. STRATEGIES AND HARD RULES,
COMMON MISTAKES AND CORRECT STRATEGIES). Each rule has a prefix-NNNNN id like [shr-00001].

This module provides:
- parse_playbook(): Parse playbook text into PlaybookEntry objects
- PlaybookRetriever: Three-channel (cosine + BM25 + PPR) retrieval with RRF merge
  Inspired by HippoRAG (Gutierrez 2024) — uses Personalized PageRank over the
  context graph for multi-hop retrieval from error patterns to canonical rules.
- format_playbook(): Format entries back into playbook text grouped by section
"""

import re
import logging
from typing import List, Tuple, Optional, Dict
from collections import defaultdict

from agent_memory.models import PlaybookEntry, CanonicalRule, PLAYBOOK_SECTIONS
from agent_memory.utils import escape_lucene

logger = logging.getLogger(__name__)

# Reverse lookup: section name -> prefix
_SECTION_TO_PREFIX = {v: k for k, v in PLAYBOOK_SECTIONS.items()}

# Pattern for entry ids like [shr-00001]
_ENTRY_ID_RE = re.compile(r"^\[([a-z]+)-(\d+)\]\s+(.+)", re.DOTALL)

# Pattern for section headers like ## STRATEGIES AND HARD RULES
_SECTION_HEADER_RE = re.compile(r"^##\s+(.+)$")


def parse_playbook(content: str) -> List[PlaybookEntry]:
    """Parse playbook text into PlaybookEntry objects.

    Handles:
    - ## section headers (matched against PLAYBOOK_SECTIONS values)
    - [prefix-NNNNN] rule text (may be multi-line until next entry or section)
    """
    entries: List[PlaybookEntry] = []
    current_section = "OTHERS"
    current_prefix = "misc"

    # Accumulator for multi-line entries
    current_id: Optional[str] = None
    current_text_lines: List[str] = []

    def _flush_entry():
        nonlocal current_id, current_text_lines
        if current_id and current_text_lines:
            text = " ".join(current_text_lines).strip()
            # Extract prefix from id
            prefix = current_id.split("-")[0]
            entries.append(PlaybookEntry(
                id=current_id,
                prefix=prefix,
                section=current_section,
                text=text,
            ))
        current_id = None
        current_text_lines = []

    for line in content.splitlines():
        stripped = line.strip()

        # Check for section header
        header_match = _SECTION_HEADER_RE.match(stripped)
        if header_match:
            _flush_entry()
            header_text = header_match.group(1).strip()
            # Find matching section
            if header_text in _SECTION_TO_PREFIX:
                current_section = header_text
                current_prefix = _SECTION_TO_PREFIX[header_text]
            else:
                current_section = header_text
                current_prefix = "misc"
            continue

        # Check for entry id
        entry_match = _ENTRY_ID_RE.match(stripped)
        if entry_match:
            _flush_entry()
            prefix = entry_match.group(1)
            num = entry_match.group(2)
            current_id = f"{prefix}-{num}"
            current_text_lines = [entry_match.group(3).strip()]
            continue

        # Continuation line for current entry
        if current_id and stripped:
            current_text_lines.append(stripped)

    # Flush last entry
    _flush_entry()

    return entries


class PlaybookRetriever:
    """Three-channel retrieval: cosine + BM25 + PPR (HippoRAG-style).

    Searches CanonicalRule nodes (deduplicated strategies) using:
    1. Cosine similarity on canonical_rule_embedding
    2. BM25 fulltext on canonical_rule_text
    3. Personalized PageRank from error-type seed nodes (HippoRAG §2.3)

    Falls back to PlaybookEntry search if no CanonicalRule nodes exist.
    """

    # Minimum RRF score threshold — entries below this are filtered out.
    # RRF scores are typically in the 0.0-0.1 range; 0.02 removes noise
    # that appears in every query regardless of relevance.
    MIN_RRF_SCORE = 0.02

    def __init__(self, store, embedder, query_rewriter=None):
        self.store = store
        self.embedder = embedder
        self.query_rewriter = query_rewriter
        self._graph_cache: Optional[Dict] = None
        self._node_specificity: Dict[str, float] = {}
        self._use_canonical: Optional[bool] = None  # lazy detect

    def ingest(self, entries: List[PlaybookEntry]) -> int:
        """Embed and store entries in Neo4j. Returns count ingested."""
        if not entries:
            return 0

        # Embed entries that don't have embeddings yet
        texts_to_embed = []
        indices = []
        for i, entry in enumerate(entries):
            if entry.embedding is None:
                texts_to_embed.append(entry.text)
                indices.append(i)

        if texts_to_embed and self.embedder:
            embeddings = self.embedder.embed_batch(texts_to_embed)
            for idx, emb in zip(indices, embeddings):
                entries[idx].embedding = emb

        if self.store:
            return self.store.batch_create_playbook_entries(entries)
        return len(entries)

    def ingest_file(self, path: str) -> int:
        """Parse a playbook file and ingest its entries. Returns count."""
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        entries = parse_playbook(content)
        return self.ingest(entries)

    def _has_canonical_rules(self) -> bool:
        """Check if CanonicalRule nodes exist in the graph."""
        if self._use_canonical is not None:
            return self._use_canonical
        if not self.store:
            self._use_canonical = False
            return False
        try:
            results = self.store.execute_query(
                "MATCH (cr:CanonicalRule) RETURN count(cr) AS cnt LIMIT 1"
            )
            self._use_canonical = results[0]["cnt"] > 0 if results else False
        except Exception:
            self._use_canonical = False
        return self._use_canonical

    def retrieve(
        self,
        query_text: str,
        query_embedding: Optional[List[float]] = None,
        top_k: int = 10,
        diversity: float = 0.3,
        error_type: Optional[str] = None,
    ) -> List[PlaybookEntry]:
        """Three-channel search with RRF merge and MMR diversification.

        Args:
            query_text: The query string.
            query_embedding: Pre-computed embedding (optional).
            top_k: Number of entries to return.
            diversity: MMR diversity weight (0=pure relevance, 1=max diversity).
                Clamped to [0.0, 1.0].
            error_type: Error type for PPR seed nodes (e.g. 'ImportError').
        """
        if not self.store:
            return []

        # Clamp diversity to valid range
        diversity = max(0.0, min(1.0, diversity))

        # Apply query rewriting
        if self.query_rewriter:
            rewritten_query = self.query_rewriter.rewrite(query_text)
            if rewritten_query != query_text:
                query_text = rewritten_query
                if self.embedder:
                    query_embedding = self.embedder.embed(query_text)
            elif query_embedding is None and self.embedder:
                query_embedding = self.embedder.embed(query_text)
        elif query_embedding is None and self.embedder:
            query_embedding = self.embedder.embed(query_text)

        # Over-fetch candidates for RRF merge + MMR re-ranking
        k_per_channel = top_k * 5

        use_canonical = self._has_canonical_rules()

        if use_canonical:
            cosine_results = self._search_cosine_canonical(
                query_embedding, k_per_channel
            ) if query_embedding else []
            bm25_results = self._search_bm25_canonical(query_text, k_per_channel)
            ppr_results = self._search_ppr(
                error_type, query_text, k_per_channel
            ) if error_type else []
        else:
            # Fallback to PlaybookEntry search
            cosine_results = self._search_cosine(
                query_embedding, k_per_channel
            ) if query_embedding else []
            bm25_results = self._search_bm25(query_text, k_per_channel)
            ppr_results = []

        # RRF merge all channels
        merged = self._rrf_merge_multi(
            [cosine_results, bm25_results, ppr_results], k=60
        )

        candidate_ids = [entry_id for entry_id, _ in merged[:top_k * 3]]
        if not candidate_ids:
            return []

        # Only fetch embeddings when MMR will actually run
        need_embeddings = (
            query_embedding is not None and diversity > 0
        )

        if use_canonical:
            if need_embeddings:
                candidates = self._fetch_canonical_rules_as_entries(candidate_ids)
            else:
                candidates = self._fetch_canonical_rules_as_entries_plain(
                    candidate_ids
                )
        else:
            if need_embeddings:
                candidates = self._fetch_entries_with_embeddings(candidate_ids)
            else:
                candidates = self._fetch_entries_by_ids(candidate_ids)
        if not candidates:
            return []

        # Build RRF score lookup
        rrf_scores = {entry_id: score for entry_id, score in merged}

        # MMR re-ranking for diversity with backfill
        if need_embeddings and len(candidates) > top_k:
            selected = self._mmr_rerank(
                candidates, query_embedding, rrf_scores,
                top_k=top_k, lambda_param=1.0 - diversity,
            )
            # Backfill with non-embedded candidates if MMR returned < top_k
            if len(selected) < top_k:
                selected_ids = {e.id for e in selected}
                for c in candidates:
                    if len(selected) >= top_k:
                        break
                    if c.id not in selected_ids:
                        selected.append(c)
                        selected_ids.add(c.id)
        else:
            selected = candidates[:top_k]

        # Annotate every selected entry with its RRF score, then filter.
        for entry in selected:
            entry._rrf_score = rrf_scores.get(entry.id, 0.0)

        # Filter by absolute threshold — but guarantee a minimum result count.
        # We want to keep as many above-threshold entries as possible (the
        # noise floor at 0.02 is meaningful when the candidate set is large
        # enough to contain cross-channel noise), while in low-data regimes
        # still returning the MMR top_k the caller asked for. Strategy:
        # take all above-threshold first; then pad with the highest-scoring
        # below-threshold entries until we hit `min(top_k, len(selected))`.
        above = [e for e in selected if e._rrf_score >= self.MIN_RRF_SCORE]
        target = min(top_k, len(selected))
        if len(above) >= target:
            filtered = above[:target]
        else:
            # When padding under target, prefer the highest-scoring
            # below-threshold entries; the iteration order of `selected`
            # comes from MMR which optimizes diversity, not RRF rank.
            below = sorted(
                (e for e in selected if e._rrf_score < self.MIN_RRF_SCORE),
                key=lambda e: e._rrf_score,
                reverse=True,
            )
            filtered = above + below[: target - len(above)]

        logger.debug(
            "RRF threshold %.3f: %d/%d entries passed (returned %d)",
            self.MIN_RRF_SCORE, len(above), len(selected), len(filtered),
        )

        return filtered

    # === Canonical Rule search channels ===

    def _search_cosine_canonical(
        self, embedding: List[float], top_k: int
    ) -> List[Tuple[str, float]]:
        """Vector index query on canonical_rule_embedding."""
        query = """
        CALL db.index.vector.queryNodes('canonical_rule_embedding', $k, $embedding)
        YIELD node, score
        RETURN node.id AS id, score
        """
        try:
            results = self.store.execute_query(query, {
                "k": top_k,
                "embedding": embedding,
            })
            return [(r["id"], r["score"]) for r in results]
        except Exception as e:
            logger.debug("Canonical cosine search failed: %s", e)
            return []

    def _search_bm25_canonical(
        self, query_text: str, top_k: int
    ) -> List[Tuple[str, float]]:
        """Fulltext index query on canonical_rule_text."""
        escaped = escape_lucene(query_text)
        if not escaped.strip():
            return []
        query = """
        CALL db.index.fulltext.queryNodes('canonical_rule_text', $query)
        YIELD node, score
        RETURN node.id AS id, score
        LIMIT $k
        """
        try:
            results = self.store.execute_query(query, {
                "query": escaped,
                "k": top_k,
            })
            return [(r["id"], r["score"]) for r in results]
        except Exception as e:
            logger.debug("Canonical BM25 search failed: %s", e)
            return []

    def _search_ppr(
        self,
        error_type: Optional[str],
        query_text: str,
        top_k: int,
    ) -> List[Tuple[str, float]]:
        """HippoRAG-style PPR retrieval over the context graph.

        1. Find seed nodes (ErrorPattern matching error_type)
        2. Run PPR from seeds with damping=0.5
        3. Apply node specificity weighting: score *= 1/degree
        4. Filter to CanonicalRule nodes
        5. Return ranked list
        """
        graph_data = self._get_graph_cache()
        if not graph_data:
            return []

        adjacency = graph_data["adjacency"]
        node_labels = graph_data["node_labels"]

        seeds = self._find_seed_nodes(error_type, query_text, node_labels)
        if not seeds:
            return []

        ppr_scores = self._personalized_pagerank(
            adjacency, seeds, damping=0.5, iterations=20
        )

        # Apply node specificity: score *= 1/degree (rarer nodes weighted higher)
        specificity = self._get_node_specificity(graph_data)
        for node_id in ppr_scores:
            ppr_scores[node_id] *= specificity.get(node_id, 1.0)

        # Filter to CanonicalRule nodes only
        rule_scores = [
            (nid, score) for nid, score in ppr_scores.items()
            if node_labels.get(nid) == "CanonicalRule"
        ]
        rule_scores.sort(key=lambda x: -x[1])
        return rule_scores[:top_k]

    def _find_seed_nodes(
        self,
        error_type: Optional[str],
        query_text: str,
        node_labels: Dict[str, str],
    ) -> List[str]:
        """Find PPR seed nodes from error type and query text."""
        seeds = []
        if not error_type:
            return seeds

        # Find ErrorPattern nodes matching the error type
        if self.store:
            try:
                results = self.store.execute_query(
                    "MATCH (e:ErrorPattern {error_type: $et}) RETURN e.id AS id",
                    {"et": error_type},
                )
                seeds.extend(r["id"] for r in results if r["id"])
            except Exception as e:
                logger.debug("Seed node lookup failed: %s", e)

        return seeds

    def _personalized_pagerank(
        self,
        adjacency: Dict[str, List[str]],
        seeds: List[str],
        damping: float = 0.5,
        iterations: int = 20,
    ) -> Dict[str, float]:
        """PPR via power iteration (HippoRAG §2.3).

        Args:
            adjacency: Undirected adjacency list.
            seeds: Seed node IDs with equal initial probability.
            damping: Damping factor (0.5 per HippoRAG recommendation).
            iterations: Number of power iteration steps.

        Returns:
            Dict of node_id -> PPR probability.
        """
        if not seeds or not adjacency:
            return {}

        # Filter seeds to nodes present in the graph
        valid_seeds = [s for s in seeds if s in adjacency]
        if not valid_seeds:
            return {}

        all_nodes = list(adjacency.keys())
        node_to_idx = {n: i for i, n in enumerate(all_nodes)}
        n = len(all_nodes)

        # Personalization vector: uniform over seed nodes
        import numpy as np
        personalization = np.zeros(n, dtype=np.float64)
        for s in valid_seeds:
            if s in node_to_idx:
                personalization[node_to_idx[s]] = 1.0 / len(valid_seeds)

        # Power iteration
        scores = personalization.copy()
        for _ in range(iterations):
            new_scores = np.zeros(n, dtype=np.float64)
            for i, node in enumerate(all_nodes):
                neighbors = adjacency.get(node, [])
                if not neighbors:
                    continue
                share = scores[i] / len(neighbors)
                for neighbor in neighbors:
                    j = node_to_idx.get(neighbor)
                    if j is not None:
                        new_scores[j] += share
            scores = (1 - damping) * personalization + damping * new_scores

        return {all_nodes[i]: float(scores[i]) for i in range(n) if scores[i] > 0}

    def _get_graph_cache(self) -> Optional[Dict]:
        """Load and cache the graph adjacency list from Neo4j."""
        if self._graph_cache is not None:
            return self._graph_cache
        if not self.store:
            return None
        try:
            self._graph_cache = self.store.export_graph_for_ppr()
            if not self._graph_cache.get("adjacency"):
                self._graph_cache = None
                return None
            logger.debug(
                "Graph cache loaded: %d nodes, %d edges",
                len(self._graph_cache["adjacency"]),
                sum(len(v) for v in self._graph_cache["adjacency"].values()) // 2,
            )
            return self._graph_cache
        except Exception as e:
            logger.debug("Graph cache load failed: %s", e)
            return None

    def _get_node_specificity(self, graph_data: Dict) -> Dict[str, float]:
        """Compute node specificity: 1/degree (cached)."""
        if self._node_specificity:
            return self._node_specificity
        degrees = graph_data.get("node_degrees", {})
        self._node_specificity = {
            nid: 1.0 / max(deg, 1) for nid, deg in degrees.items()
        }
        return self._node_specificity

    def invalidate_cache(self) -> None:
        """Invalidate graph cache (call after graph modifications)."""
        self._graph_cache = None
        self._node_specificity = {}
        self._use_canonical = None

    # === Legacy PlaybookEntry search channels (fallback) ===

    def _search_cosine(
        self, embedding: List[float], top_k: int
    ) -> List[Tuple[str, float]]:
        """Vector index query on playbook_embedding."""
        query = """
        CALL db.index.vector.queryNodes('playbook_embedding', $k, $embedding)
        YIELD node, score
        RETURN node.id AS id, score
        """
        try:
            results = self.store.execute_query(query, {
                "k": top_k,
                "embedding": embedding,
            })
            return [(r["id"], r["score"]) for r in results]
        except Exception as e:
            logger.debug("Cosine search failed: %s", e)
            return []

    def _search_bm25(
        self, query_text: str, top_k: int
    ) -> List[Tuple[str, float]]:
        """Fulltext index query on playbook_text."""
        escaped = escape_lucene(query_text)
        if not escaped.strip():
            return []
        query = """
        CALL db.index.fulltext.queryNodes('playbook_text', $query)
        YIELD node, score
        RETURN node.id AS id, score
        LIMIT $k
        """
        try:
            results = self.store.execute_query(query, {
                "query": escaped,
                "k": top_k,
            })
            return [(r["id"], r["score"]) for r in results]
        except Exception as e:
            logger.debug("BM25 search failed: %s", e)
            return []

    # === Merge and rerank ===

    def _rrf_merge(
        self,
        cosine_results: List[Tuple[str, float]],
        bm25_results: List[Tuple[str, float]],
        k: int = 60,
    ) -> List[Tuple[str, float]]:
        """Reciprocal Rank Fusion merge of two ranked lists (legacy)."""
        return self._rrf_merge_multi([cosine_results, bm25_results], k=k)

    def _rrf_merge_multi(
        self,
        ranked_lists: List[List[Tuple[str, float]]],
        k: int = 60,
    ) -> List[Tuple[str, float]]:
        """Reciprocal Rank Fusion merge of multiple ranked lists."""
        scores: Dict[str, float] = defaultdict(float)

        for ranked_list in ranked_lists:
            for rank, (entry_id, _) in enumerate(ranked_list):
                scores[entry_id] += 1.0 / (k + rank + 1)

        merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return merged

    # === Fetch methods ===

    def _fetch_entries_by_ids(self, ids: List[str]) -> List[PlaybookEntry]:
        """Fetch PlaybookEntry nodes by their ids, preserving order."""
        query = """
        UNWIND $ids AS eid
        MATCH (p:PlaybookEntry {id: eid})
        RETURN p.id AS id, p.prefix AS prefix, p.section AS section, p.text AS text
        """
        try:
            results = self.store.execute_query(query, {"ids": ids})
        except Exception as e:
            logger.debug("Fetch entries failed: %s", e)
            return []

        lookup = {r["id"]: r for r in results}
        entries = []
        for eid in ids:
            if eid in lookup:
                r = lookup[eid]
                entries.append(PlaybookEntry(
                    id=r["id"],
                    prefix=r["prefix"],
                    section=r["section"],
                    text=r["text"],
                ))
        return entries

    def _fetch_entries_with_embeddings(
        self, ids: List[str]
    ) -> List[PlaybookEntry]:
        """Fetch PlaybookEntry nodes with embeddings, preserving order."""
        query = """
        UNWIND $ids AS eid
        MATCH (p:PlaybookEntry {id: eid})
        RETURN p.id AS id, p.prefix AS prefix, p.section AS section,
               p.text AS text, p.embedding AS embedding
        """
        try:
            results = self.store.execute_query(query, {"ids": ids})
        except Exception as e:
            logger.debug("Fetch entries with embeddings failed: %s", e)
            return []

        lookup = {r["id"]: r for r in results}
        entries = []
        for eid in ids:
            if eid in lookup:
                r = lookup[eid]
                entries.append(PlaybookEntry(
                    id=r["id"],
                    prefix=r["prefix"],
                    section=r["section"],
                    text=r["text"],
                    embedding=r.get("embedding"),
                ))
        return entries

    def _fetch_canonical_rules_as_entries(
        self, ids: List[str]
    ) -> List[PlaybookEntry]:
        """Fetch CanonicalRule nodes with embeddings, convert to PlaybookEntry."""
        query = """
        UNWIND $ids AS rid
        MATCH (cr:CanonicalRule {id: rid})
        RETURN cr.id AS id, cr.prefix AS prefix, cr.section AS section,
               cr.rule_text AS text, cr.embedding AS embedding
        """
        try:
            results = self.store.execute_query(query, {"ids": ids})
        except Exception as e:
            logger.debug("Fetch canonical rules failed: %s", e)
            return []

        lookup = {r["id"]: r for r in results}
        entries = []
        for rid in ids:
            if rid in lookup:
                r = lookup[rid]
                entries.append(PlaybookEntry(
                    id=r["id"],
                    prefix=r.get("prefix", "misc"),
                    section=r.get("section", "OTHERS"),
                    text=r["text"],
                    embedding=r.get("embedding"),
                ))
        return entries

    def _fetch_canonical_rules_as_entries_plain(
        self, ids: List[str]
    ) -> List[PlaybookEntry]:
        """Fetch CanonicalRule nodes without embeddings (lighter payload)."""
        query = """
        UNWIND $ids AS rid
        MATCH (cr:CanonicalRule {id: rid})
        RETURN cr.id AS id, cr.prefix AS prefix, cr.section AS section,
               cr.rule_text AS text
        """
        try:
            results = self.store.execute_query(query, {"ids": ids})
        except Exception as e:
            logger.debug("Fetch canonical rules (plain) failed: %s", e)
            return []

        lookup = {r["id"]: r for r in results}
        entries = []
        for rid in ids:
            if rid in lookup:
                r = lookup[rid]
                entries.append(PlaybookEntry(
                    id=r["id"],
                    prefix=r.get("prefix", "misc"),
                    section=r.get("section", "OTHERS"),
                    text=r["text"],
                ))
        return entries

    def _mmr_rerank(
        self,
        candidates: List[PlaybookEntry],
        query_embedding: List[float],
        rrf_scores: Dict[str, float],
        top_k: int = 10,
        lambda_param: float = 0.7,
    ) -> List[PlaybookEntry]:
        """Maximal Marginal Relevance re-ranking for diversity.

        Balances relevance (RRF score) with diversity (low similarity to
        already-selected entries).

        Args:
            candidates: Candidate entries with embeddings.
            query_embedding: Query vector.
            rrf_scores: Pre-computed RRF relevance scores by entry id.
            top_k: Number of entries to select.
            lambda_param: Trade-off (1.0=pure relevance, 0.0=max diversity).
        """
        import numpy as np

        q_vec = np.array(query_embedding, dtype=np.float32)
        q_norm = np.linalg.norm(q_vec)
        if q_norm == 0:
            return candidates[:top_k]

        q_dim = len(query_embedding)

        # Pre-compute candidate vectors and cosine similarities to query
        cand_vecs = []
        cand_query_sims = []
        valid_candidates = []
        for c in candidates:
            if c.embedding is None:
                continue
            # Skip candidates with mismatched embedding dimensions
            if len(c.embedding) != q_dim:
                continue
            vec = np.array(c.embedding, dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm == 0:
                continue
            cand_vecs.append(vec / norm)
            cand_query_sims.append(float(np.dot(q_vec, vec) / (q_norm * norm)))
            valid_candidates.append(c)

        if not valid_candidates:
            return candidates[:top_k]

        # Normalize RRF scores to [0, 1] for combining with cosine
        max_rrf = max((rrf_scores.get(c.id, 0) for c in valid_candidates), default=1)
        if max_rrf == 0:
            max_rrf = 1

        selected: List[int] = []
        remaining = set(range(len(valid_candidates)))

        for _ in range(min(top_k, len(valid_candidates))):
            best_idx = -1
            best_score = -float("inf")

            for idx in remaining:
                # Relevance: combine normalized RRF score and query cosine
                rrf_norm = rrf_scores.get(valid_candidates[idx].id, 0) / max_rrf
                relevance = 0.5 * rrf_norm + 0.5 * cand_query_sims[idx]

                # Max similarity to already selected
                max_sim = 0.0
                for sel_idx in selected:
                    sim = float(np.dot(cand_vecs[idx], cand_vecs[sel_idx]))
                    if sim > max_sim:
                        max_sim = sim

                score = lambda_param * relevance - (1 - lambda_param) * max_sim
                if score > best_score:
                    best_score = score
                    best_idx = idx

            if best_idx >= 0:
                selected.append(best_idx)
                remaining.discard(best_idx)

        return [valid_candidates[i] for i in selected]


def format_playbook(entries: List[PlaybookEntry], wrap: bool = False) -> str:
    """Format entries as playbook text grouped by section.

    Output follows the canonical section order from PLAYBOOK_SECTIONS.
    Sections with no entries are omitted.

    Args:
        entries: PlaybookEntry objects to format.
        wrap: If True, wrap output in <memory_playbook> tags with
              an instruction header for LLM consumption.
    """
    if not entries:
        return ""

    # Group entries by section
    by_section: Dict[str, List[PlaybookEntry]] = defaultdict(list)
    for entry in entries:
        by_section[entry.section].append(entry)

    parts = []
    # Output sections in canonical order
    for prefix, section_name in PLAYBOOK_SECTIONS.items():
        if section_name not in by_section:
            continue
        parts.append(f"## {section_name}")
        for entry in by_section[section_name]:
            parts.append(f"[{entry.id}] {entry.text}")
        parts.append("")  # blank line between sections

    # Any sections not in PLAYBOOK_SECTIONS (custom headers)
    known_sections = set(PLAYBOOK_SECTIONS.values())
    for section_name, section_entries in by_section.items():
        if section_name in known_sections:
            continue
        parts.append(f"## {section_name}")
        for entry in section_entries:
            parts.append(f"[{entry.id}] {entry.text}")
        parts.append("")

    body = "\n".join(parts).rstrip("\n")

    if wrap:
        return (
            "<memory_playbook>\n"
            "The following rules were learned from solving similar coding "
            "problems in the past.\nApply relevant rules to your current task.\n\n"
            f"{body}\n"
            "</memory_playbook>"
        )
    return body
