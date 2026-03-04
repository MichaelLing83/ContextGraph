"""Playbook parsing, retrieval, and formatting.

Playbooks are collections of rules organized by section (e.g. STRATEGIES AND HARD RULES,
COMMON MISTAKES AND CORRECT STRATEGIES). Each rule has a prefix-NNNNN id like [shr-00001].

This module provides:
- parse_playbook(): Parse playbook text into PlaybookEntry objects
- PlaybookRetriever: Dual-channel (cosine + BM25) retrieval with RRF merge
- format_playbook(): Format entries back into playbook text grouped by section
"""

import re
import logging
from typing import List, Tuple, Optional, Dict
from collections import defaultdict

from agent_memory.models import PlaybookEntry, PLAYBOOK_SECTIONS
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
    """Retrieve relevant playbook entries using dual-channel search (cosine + BM25)."""

    def __init__(self, store, embedder):
        self.store = store
        self.embedder = embedder

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

    def retrieve(
        self,
        query_text: str,
        query_embedding: Optional[List[float]] = None,
        top_k: int = 10,
        diversity: float = 0.3,
    ) -> List[PlaybookEntry]:
        """Dual-channel search with MMR diversification.

        Args:
            query_text: The query string.
            query_embedding: Pre-computed embedding (optional).
            top_k: Number of entries to return.
            diversity: MMR diversity weight (0=pure relevance, 1=max diversity).
        """
        if not self.store:
            return []

        # Generate embedding if not provided
        if query_embedding is None and self.embedder:
            query_embedding = self.embedder.embed(query_text)

        # Over-fetch candidates for RRF merge + MMR re-ranking
        k_per_channel = top_k * 5

        cosine_results = self._search_cosine(query_embedding, k_per_channel) if query_embedding else []
        bm25_results = self._search_bm25(query_text, k_per_channel)

        # RRF merge
        merged = self._rrf_merge(cosine_results, bm25_results, k=60)

        # Fetch top candidates with embeddings for MMR
        candidate_ids = [entry_id for entry_id, _ in merged[:top_k * 3]]
        if not candidate_ids:
            return []

        candidates = self._fetch_entries_with_embeddings(candidate_ids)
        if not candidates:
            return []

        # Build RRF score lookup
        rrf_scores = {entry_id: score for entry_id, score in merged}

        # MMR re-ranking for diversity
        if query_embedding and diversity > 0 and len(candidates) > top_k:
            selected = self._mmr_rerank(
                candidates, query_embedding, rrf_scores,
                top_k=top_k, lambda_param=1.0 - diversity,
            )
        else:
            selected = candidates[:top_k]

        return selected

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

    def _rrf_merge(
        self,
        cosine_results: List[Tuple[str, float]],
        bm25_results: List[Tuple[str, float]],
        k: int = 60,
    ) -> List[Tuple[str, float]]:
        """Reciprocal Rank Fusion merge of two ranked lists."""
        scores: Dict[str, float] = defaultdict(float)

        for rank, (entry_id, _) in enumerate(cosine_results):
            scores[entry_id] += 1.0 / (k + rank + 1)

        for rank, (entry_id, _) in enumerate(bm25_results):
            scores[entry_id] += 1.0 / (k + rank + 1)

        # Sort by RRF score descending
        merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return merged

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

        # Build lookup and preserve original order
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

        # Pre-compute candidate vectors and cosine similarities to query
        cand_vecs = []
        cand_query_sims = []
        valid_candidates = []
        for c in candidates:
            if c.embedding is None:
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
