"""Memory Retriever - triple-search (cosine, BM25, BFS) with reranking.

Zep-inspired architecture:
  1. phi_cos  — vector similarity via Neo4j vector index
  2. phi_bm25 — BM25 full-text search via Neo4j full-text index
  3. phi_bfs  — breadth-first graph traversal from seed nodes
  4. phi_community — community-level retrieval (added in Phase 7)

Falls back to keyword-only retrieval when vector indexes are absent.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Set, TYPE_CHECKING
import re
import time
import logging

from agent_memory.models import State, Methodology, Fragment, Strategy

if TYPE_CHECKING:
    from agent_memory.neo4j_store import Neo4jStore
    from agent_memory.embeddings import EmbeddingClient
    from agent_memory.query_rewriter import QueryRewriter

logger = logging.getLogger(__name__)

# Common stop words to skip when extracting keywords
_STOP_WORDS: Set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "just", "because", "but", "and", "or", "if",
    "while", "about", "up", "that", "this", "it", "its", "i", "me",
    "my", "we", "our", "you", "your", "he", "she", "they", "them",
    "what", "which", "who", "whom", "these", "those",
    "file", "error", "test", "tests", "line", "code",
}


@dataclass
class ScoredResult:
    """A scored node returned from a search channel."""

    node_id: str
    node_type: str          # "Fragment", "Trajectory", "Community"
    score: float
    source: str             # "cosine", "bm25", "bfs", "community"
    node_data: Dict[str, Any] = field(default_factory=dict)
    hop_distance: int = 0   # for BFS results


@dataclass
class EnrichedFragment:
    """Fragment with trajectory context for better tool output."""

    fragment: Fragment
    repo: str = ""
    instance_id: str = ""
    trajectory_summary: str = ""
    error_type: str = ""
    error_keywords: List[str] = field(default_factory=list)
    action_summary: str = ""
    relevance_score: float = 0.0


@dataclass
class RetrievalResult:
    """Result from memory retrieval."""

    methodologies: List[Methodology] = field(default_factory=list)
    similar_fragments: List[Fragment] = field(default_factory=list)
    enriched_fragments: List[EnrichedFragment] = field(default_factory=list)
    error_solutions: List[Dict[str, Any]] = field(default_factory=list)
    strategies: List[Strategy] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (
            not self.methodologies and
            not self.similar_fragments and
            not self.enriched_fragments and
            not self.error_solutions and
            not self.strategies
        )


class MemoryRetriever:
    """Retrieve relevant memories using triple-search + reranking.

    When vector indexes are available, uses:
      1. Cosine similarity (phi_cos)
      2. BM25 full-text search (phi_bm25)
      3. BFS graph traversal (phi_bfs)
      4. Community-level search (phi_community)

    Falls back to legacy keyword-only retrieval otherwise.
    """

    def __init__(
        self,
        store: Optional["Neo4jStore"],
        embedder: Optional["EmbeddingClient"],
        query_rewriter: "Optional[QueryRewriter]" = None,
    ):
        self.store = store
        self.embedder = embedder
        self.query_rewriter = query_rewriter
        self._vector_index_checked = False
        self._has_vector_indexes = False
        self._vector_index_check_time = 0.0
        self._vector_index_ttl = 300.0  # Re-check every 5 minutes
        # Reuse a single RerankerPipeline instance across calls
        from agent_memory.reranker import RerankerPipeline
        self._reranker = RerankerPipeline()

    # ------------------------------------------------------------------
    # Main retrieve entry point
    # ------------------------------------------------------------------

    def retrieve(self, current_state: State, top_k: int = 5) -> RetrievalResult:
        """Retrieve relevant memories for current state.

        Uses triple-search (cosine + BM25 + BFS) when vector indexes are
        available, otherwise falls back to keyword-only retrieval.
        """
        result = RetrievalResult()

        if not self.store:
            return result

        # Check if we can use advanced retrieval
        if self._check_vector_index():
            result = self._retrieve_triple_search(current_state, top_k)
        else:
            result = self._retrieve_legacy(current_state, top_k)

        # Add warnings for potential failure patterns
        result.warnings = self._get_warnings(current_state)

        return result

    # ------------------------------------------------------------------
    # Triple-search retrieval (Zep-inspired)
    # ------------------------------------------------------------------

    def _retrieve_triple_search(
        self, current_state: State, top_k: int
    ) -> RetrievalResult:
        """Advanced triple-search retrieval using cosine, BM25, and BFS."""
        result = RetrievalResult()

        # Build query text from state
        query_text = self._build_query_text(current_state)

        # Rewrite query for better methodology matching
        if self.query_rewriter:
            query_text = self.query_rewriter.rewrite(query_text)

        # Embedding must correspond to the (possibly rewritten) query text.
        if self.query_rewriter:
            # Rewriter active: always re-embed from rewritten text
            query_embedding = self.embedder.embed(query_text) if self.embedder else None
        else:
            query_embedding = current_state.embedding
            if not query_embedding and self.embedder:
                query_embedding = self.embedder.embed(query_text)

        # Channel 1: Cosine similarity search
        cosine_results = self._search_cosine(query_embedding, top_k=20)

        # Channel 2: BM25 full-text search
        bm25_results = self._search_bm25(query_text, top_k=20)

        # Channel 3: BFS from seed nodes (top cosine + BM25 results)
        seed_ids = set()
        for sr in (cosine_results[:5] + bm25_results[:5]):
            seed_ids.add(sr.node_id)
        bfs_results = self._search_bfs(list(seed_ids), top_k=20)

        # Channel 4: Community search (if communities exist)
        community_results = self._search_community(query_embedding, top_k=10)

        # Collect all ScoredResults for reranking
        all_scored = cosine_results + bm25_results + bfs_results + community_results

        # Use reranker to merge and diversify results
        reranked = self._reranker.rerank(
            cosine_results=cosine_results,
            bm25_results=bm25_results,
            bfs_results=bfs_results,
            community_results=community_results,
            query_embedding=query_embedding,
            top_k=top_k,
        )
        # Convert reranked ScoredResults to EnrichedFragments
        result.enriched_fragments = self._scored_to_enriched(reranked)

        # Populate similar_fragments for backward compat
        result.similar_fragments = [
            ef.fragment for ef in result.enriched_fragments
        ]

        # Channel 5: Strategy search (LLM-extracted rules)
        result.strategies = self._search_strategies(
            query_embedding, query_text, top_k=5
        )

        return result

    def _search_cosine(
        self, query_embedding: Optional[List[float]], top_k: int = 20
    ) -> List[ScoredResult]:
        """Vector similarity search using Neo4j vector index."""
        if not self.store or not query_embedding:
            return []

        query = """
        CALL db.index.vector.queryNodes('fragment_embedding', $k, $embedding)
        YIELD node, score
        WHERE node.embedding IS NOT NULL
        // Exclude fragments whose HAS_FRAGMENT edge has been invalidated
        AND NOT EXISTS {
            MATCH (:Trajectory)-[r:HAS_FRAGMENT]->(node) WHERE r.t_invalid IS NOT NULL
        }
        OPTIONAL MATCH (t:Trajectory)-[:HAS_FRAGMENT]->(node)
        RETURN node{.*, __node_id: node.id} AS f,
               score,
               t.instance_id AS instance_id,
               t.repo AS repo,
               coalesce(t.summary, '') AS summary
        LIMIT $k
        """
        try:
            results = self.store.execute_query(query, {
                "k": top_k,
                "embedding": query_embedding,
            })
        except Exception as e:
            logger.debug("Cosine search failed: %s", e)
            return []

        scored = []
        for r in results:
            f_data = r.get("f", {})
            node_id = f_data.get("__node_id", f_data.get("id", ""))
            scored.append(ScoredResult(
                node_id=node_id,
                node_type="Fragment",
                score=r.get("score", 0.0),
                source="cosine",
                node_data={
                    "f": {k: v for k, v in f_data.items() if k != "__node_id"},
                    "instance_id": r.get("instance_id", ""),
                    "repo": r.get("repo", ""),
                    "summary": r.get("summary", ""),
                },
            ))
        return scored

    def _search_bm25(self, query_text: str, top_k: int = 20) -> List[ScoredResult]:
        """BM25 full-text search on Fragment descriptions."""
        if not self.store or not query_text:
            return []

        # Escape special Lucene characters for BM25 query
        safe_query = self._escape_lucene(query_text)
        if not safe_query.strip():
            return []

        query = """
        CALL db.index.fulltext.queryNodes('fragment_description', $query)
        YIELD node, score
        OPTIONAL MATCH (t:Trajectory)-[:HAS_FRAGMENT]->(node)
        RETURN node{.*, __node_id: node.id} AS f,
               score,
               t.instance_id AS instance_id,
               t.repo AS repo,
               coalesce(t.summary, '') AS summary
        LIMIT $k
        """
        try:
            results = self.store.execute_query(query, {
                "query": safe_query,
                "k": top_k,
            })
        except Exception as e:
            logger.debug("BM25 search failed: %s", e)
            return []

        scored = []
        for r in results:
            f_data = r.get("f", {})
            node_id = f_data.get("__node_id", f_data.get("id", ""))
            scored.append(ScoredResult(
                node_id=node_id,
                node_type="Fragment",
                score=r.get("score", 0.0),
                source="bm25",
                node_data={
                    "f": {k: v for k, v in f_data.items() if k != "__node_id"},
                    "instance_id": r.get("instance_id", ""),
                    "repo": r.get("repo", ""),
                    "summary": r.get("summary", ""),
                },
            ))
        return scored

    def _search_bfs(
        self,
        seed_node_ids: List[str],
        top_k: int = 20,
    ) -> List[ScoredResult]:
        """BFS graph traversal from seed nodes (2-hop).

        Traverses:
          Fragment→CAUSED_ERROR→ErrorPattern←CAUSED_ERROR←Fragment (sibling errors)
          Fragment←HAS_FRAGMENT←Trajectory→HAS_FRAGMENT→Fragment (sibling fragments)

        Score = 1.0 / (1 + hop_distance)
        """
        if not self.store or not seed_node_ids:
            return []

        query = """
        UNWIND $seed_ids AS seed_id
        MATCH (seed:Fragment {id: seed_id})
        CALL {
            WITH seed
            // Path 1: sibling fragments via shared ErrorPattern
            OPTIONAL MATCH (seed)-[:CAUSED_ERROR]->(e:ErrorPattern)<-[:CAUSED_ERROR]-(sibling:Fragment)
            WHERE sibling.id <> seed.id
            RETURN sibling AS neighbor, 2 AS hops, 'error_sibling' AS path_type

            UNION

            WITH seed
            // Path 2: sibling fragments via shared Trajectory
            OPTIONAL MATCH (t:Trajectory)-[:HAS_FRAGMENT]->(seed)
            WITH seed, t
            WHERE t IS NOT NULL
            MATCH (t)-[:HAS_FRAGMENT]->(sibling:Fragment)
            WHERE sibling.id <> seed.id
            RETURN sibling AS neighbor, 2 AS hops, 'trajectory_sibling' AS path_type
        }
        WITH neighbor, hops, path_type
        WHERE neighbor IS NOT NULL
        OPTIONAL MATCH (t2:Trajectory)-[:HAS_FRAGMENT]->(neighbor)
        RETURN DISTINCT neighbor{.*, __node_id: neighbor.id} AS f,
               hops,
               path_type,
               t2.instance_id AS instance_id,
               t2.repo AS repo,
               coalesce(t2.summary, '') AS summary
        LIMIT $k
        """
        try:
            results = self.store.execute_query(query, {
                "seed_ids": seed_node_ids[:10],  # cap seeds
                "k": top_k,
            })
        except Exception as e:
            logger.debug("BFS search failed: %s", e)
            return []

        scored = []
        seen_ids = set()
        for r in results:
            f_data = r.get("f", {})
            node_id = f_data.get("__node_id", f_data.get("id", ""))
            if node_id in seen_ids or node_id in set(seed_node_ids):
                continue
            seen_ids.add(node_id)

            hops = r.get("hops", 2)
            score = 1.0 / (1 + hops)

            scored.append(ScoredResult(
                node_id=node_id,
                node_type="Fragment",
                score=score,
                source="bfs",
                hop_distance=hops,
                node_data={
                    "f": {k: v for k, v in f_data.items() if k != "__node_id"},
                    "instance_id": r.get("instance_id", ""),
                    "repo": r.get("repo", ""),
                    "summary": r.get("summary", ""),
                },
            ))
        return scored

    def _search_community(
        self, query_embedding: Optional[List[float]], top_k: int = 10
    ) -> List[ScoredResult]:
        """Community-level search: find relevant communities, then their members."""
        if not self.store or not query_embedding:
            return []

        n_communities = min(top_k, 5)
        query = """
        CALL db.index.vector.queryNodes('community_embedding', $n_communities, $embedding)
        YIELD node AS community, score
        MATCH (member:Fragment)-[:IN_COMMUNITY]->(community)
        RETURN member{.*, __node_id: member.id} AS f,
               score * 0.8 AS score,
               community.summary AS community_summary
        LIMIT $member_limit
        """
        try:
            results = self.store.execute_query(query, {
                "n_communities": n_communities,
                "embedding": query_embedding,
                "member_limit": top_k,
            })
        except Exception as e:
            logger.debug("Community search failed (expected if no communities): %s", e)
            return []

        scored = []
        for r in results:
            f_data = r.get("f", {})
            node_id = f_data.get("__node_id", f_data.get("id", ""))
            scored.append(ScoredResult(
                node_id=node_id,
                node_type="Fragment",
                score=r.get("score", 0.0),
                source="community",
                node_data={
                    "f": {k: v for k, v in f_data.items() if k != "__node_id"},
                    "community_summary": r.get("community_summary", ""),
                },
            ))
        return scored

    # ------------------------------------------------------------------
    # Strategy search (LLM-extracted rules)
    # ------------------------------------------------------------------

    def _search_strategies(
        self,
        query_embedding: Optional[List[float]],
        query_text: str,
        top_k: int = 5,
    ) -> List[Strategy]:
        """Search Strategy nodes by vector similarity + BM25.

        Returns Strategy objects (not ScoredResults) since strategies
        are a separate output channel from fragment-based results.
        Uses RRF (reciprocal rank fusion) to combine cosine and BM25
        rankings, weighted by retrieval relevance rather than just confidence.
        """
        if not self.store:
            return []

        # Collect ranked lists from each channel
        cosine_ranked: List[tuple] = []  # (id, score, strategy_data)
        bm25_ranked: List[tuple] = []

        # Cosine on strategy_embedding
        if query_embedding and self._check_strategy_index():
            try:
                cosine_query = """
                CALL db.index.vector.queryNodes('strategy_embedding', $k, $embedding)
                YIELD node, score
                RETURN node.id AS id, node.rule_text AS rule_text,
                       node.category AS category,
                       node.source_trajectory_id AS source_trajectory_id,
                       node.source_repo AS source_repo,
                       node.confidence AS confidence,
                       score
                LIMIT $k
                """
                results = self.store.execute_query(cosine_query, {
                    "k": top_k * 2,
                    "embedding": query_embedding,
                })
                for r in results:
                    sid = r.get("id", "")
                    if sid:
                        cosine_ranked.append((sid, r.get("score", 0), r))
            except Exception as e:
                logger.debug("Strategy cosine search failed: %s", e)

        # BM25 on strategy_text
        if query_text:
            safe_query = self._escape_lucene(query_text)
            if safe_query.strip():
                try:
                    bm25_query = """
                    CALL db.index.fulltext.queryNodes('strategy_text', $query)
                    YIELD node, score
                    RETURN node.id AS id, node.rule_text AS rule_text,
                           node.category AS category,
                           node.source_trajectory_id AS source_trajectory_id,
                           node.source_repo AS source_repo,
                           node.confidence AS confidence,
                           score
                    LIMIT $k
                    """
                    results = self.store.execute_query(bm25_query, {
                        "query": safe_query,
                        "k": top_k * 2,
                    })
                    for r in results:
                        sid = r.get("id", "")
                        if sid:
                            bm25_ranked.append((sid, r.get("score", 0), r))
                except Exception as e:
                    logger.debug("Strategy BM25 search failed: %s", e)

        # RRF merge: combine ranks from both channels
        rrf_scores: Dict[str, float] = {}
        strategy_data: Dict[str, dict] = {}
        rrf_k = 60  # RRF constant

        for rank, (sid, _, data) in enumerate(cosine_ranked):
            rrf_scores[sid] = rrf_scores.get(sid, 0) + 1.0 / (rrf_k + rank + 1)
            if sid not in strategy_data:
                strategy_data[sid] = data

        for rank, (sid, _, data) in enumerate(bm25_ranked):
            rrf_scores[sid] = rrf_scores.get(sid, 0) + 1.0 / (rrf_k + rank + 1)
            if sid not in strategy_data:
                strategy_data[sid] = data

        # Sort by RRF score (retrieval relevance), break ties with confidence
        sorted_ids = sorted(
            rrf_scores.keys(),
            key=lambda sid: (
                rrf_scores[sid],
                strategy_data[sid].get("confidence", 0.5),
            ),
            reverse=True,
        )

        strategies = []
        for sid in sorted_ids[:top_k]:
            r = strategy_data[sid]
            strategies.append(Strategy(
                id=sid,
                rule_text=r.get("rule_text", ""),
                category=r.get("category", "debugging"),
                source_trajectory_id=r.get("source_trajectory_id", ""),
                source_repo=r.get("source_repo", ""),
                confidence=r.get("confidence", 0.5),
            ))

        return strategies

    def _check_strategy_index(self) -> bool:
        """Check if strategy vector index exists in Neo4j."""
        if not self.store:
            return False
        try:
            results = self.store.execute_query(
                "SHOW INDEXES YIELD name WHERE name = 'strategy_embedding' RETURN name"
            )
            return len(results) > 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Legacy retrieval (backward compat)
    # ------------------------------------------------------------------

    def _retrieve_legacy(self, current_state: State, top_k: int) -> RetrievalResult:
        """Legacy keyword-only retrieval (original implementation)."""
        result = RetrievalResult()

        # 1. Error-based retrieval (keyword-ranked)
        if current_state.current_error:
            error_results = self.by_error(current_state.current_error)
            result.enriched_fragments.extend(error_results)

        # 2. Task-based retrieval (keyword matching)
        task_results = self.by_task(
            task_description=current_state.task_description,
            repo_summary=current_state.repo_summary,
        )
        result.enriched_fragments.extend(task_results)

        # Dedupe and keep top_k
        result.enriched_fragments = self._dedupe_enriched(
            result.enriched_fragments, top_k
        )

        # Also populate similar_fragments for backward compat
        result.similar_fragments = [
            ef.fragment for ef in result.enriched_fragments
        ]

        # Strategy search (BM25-only works without vector indexes)
        query_text = self._build_query_text(current_state)
        query_embedding = current_state.embedding
        result.strategies = self._search_strategies(
            query_embedding, query_text, top_k=5
        )

        return result

    def by_error(self, error_message: str) -> List[EnrichedFragment]:
        """Retrieve fragments from successful trajectories that dealt with the same error type.

        Matches by error_type, then ranks by keyword overlap between the actual
        error message and the ErrorPattern's error_keywords.
        """
        if not self.store:
            return []

        error_type = self._extract_error_type(error_message)
        if not error_type:
            return []

        query = """
        MATCH (t:Trajectory)-[:HAS_FRAGMENT]->(f:Fragment)-[:CAUSED_ERROR]->(e:ErrorPattern)
        WHERE e.error_type = $error_type AND t.success = true
        RETURN f, t.instance_id AS instance_id, t.repo AS repo,
               coalesce(t.summary, '') AS summary,
               e.error_keywords AS keywords, e.error_type AS etype,
               f.action_sequence AS actions
        LIMIT 20
        """

        try:
            results = self.store.execute_query(query, {"error_type": error_type})
        except Exception as e:
            logger.warning("Error-based retrieval failed: %s", e)
            return []

        if not results:
            return []

        # Extract keywords from the query error message for ranking
        query_keywords = self._extract_keywords(error_message)

        enriched = []
        for r in results:
            if "f" not in r:
                continue

            frag = self._dict_to_fragment(r["f"])
            kw_list = r.get("keywords") or []
            if isinstance(kw_list, str):
                kw_list = [k.strip() for k in kw_list.split(",")]

            # Score by keyword overlap
            frag_keywords = {k.lower() for k in kw_list if k}
            overlap = query_keywords & frag_keywords
            score = len(overlap) / max(len(query_keywords), 1)

            actions = r.get("actions") or frag.action_sequence
            enriched.append(EnrichedFragment(
                fragment=frag,
                repo=r.get("repo", ""),
                instance_id=r.get("instance_id", ""),
                trajectory_summary=r.get("summary", ""),
                error_type=r.get("etype", error_type),
                error_keywords=kw_list,
                action_summary=self._summarize_actions(actions),
                relevance_score=score,
            ))

        # Sort by relevance (highest first)
        enriched.sort(key=lambda x: x.relevance_score, reverse=True)
        return enriched[:5]

    def by_task(
        self,
        task_description: str,
        repo_summary: str,
    ) -> List[EnrichedFragment]:
        """Retrieve fragments from trajectories whose summary shares keywords with the task."""
        if not self.store:
            return []

        # Extract meaningful keywords from the task description
        task_keywords = self._extract_keywords(task_description)
        if not task_keywords:
            return []

        # Build a keyword-OR match against trajectory summary
        # Use CONTAINS for each keyword and count matches
        keyword_list = list(task_keywords)[:10]  # cap to avoid huge queries

        # Build WHERE clauses for keyword matching
        where_parts = []
        params: Dict[str, Any] = {}
        for i, kw in enumerate(keyword_list):
            param = f"kw{i}"
            where_parts.append(
                f"CASE WHEN toLower(coalesce(t.summary, '')) CONTAINS ${param} THEN 1 ELSE 0 END"
            )
            params[param] = kw.lower()

        score_expr = " + ".join(where_parts)

        query = f"""
        MATCH (t:Trajectory)-[:HAS_FRAGMENT]->(f:Fragment)
        WHERE t.success = true
        WITH f, t, ({score_expr}) AS kw_score
        WHERE kw_score > 0
        RETURN f, t.instance_id AS instance_id, t.repo AS repo,
               coalesce(t.summary, '') AS summary, kw_score,
               f.action_sequence AS actions
        ORDER BY kw_score DESC
        LIMIT 10
        """

        try:
            results = self.store.execute_query(query, params)
        except Exception as e:
            logger.warning("Task-based retrieval failed: %s", e)
            return []

        enriched = []
        for r in results:
            if "f" not in r:
                continue

            frag = self._dict_to_fragment(r["f"])
            kw_score = r.get("kw_score", 0)
            actions = r.get("actions") or frag.action_sequence

            enriched.append(EnrichedFragment(
                fragment=frag,
                repo=r.get("repo", ""),
                instance_id=r.get("instance_id", ""),
                trajectory_summary=r.get("summary", ""),
                action_summary=self._summarize_actions(actions),
                relevance_score=kw_score / max(len(keyword_list), 1),
            ))

        return enriched

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_vector_index(self) -> bool:
        """Lazily check if vector indexes exist in Neo4j (TTL-cached)."""
        now = time.monotonic()
        if self._vector_index_checked and (now - self._vector_index_check_time) < self._vector_index_ttl:
            return self._has_vector_indexes

        self._vector_index_checked = True
        self._vector_index_check_time = now

        if not self.store:
            return False

        try:
            results = self.store.execute_query(
                "SHOW INDEXES YIELD name WHERE name = 'fragment_embedding' RETURN name"
            )
            self._has_vector_indexes = len(results) > 0
        except Exception:
            self._has_vector_indexes = False

        logger.debug("Vector index available: %s", self._has_vector_indexes)
        return self._has_vector_indexes

    def _build_query_text(self, state: State) -> str:
        """Build a query string from agent state for BM25 search."""
        parts = []
        if state.current_error:
            parts.append(state.current_error)
        if state.task_description:
            parts.append(state.task_description)
        if state.repo_summary:
            parts.append(state.repo_summary[:100])
        return " ".join(parts)

    def _escape_lucene(self, text: str) -> str:
        """Escape Lucene special characters for BM25 queries."""
        from agent_memory.utils import escape_lucene
        return escape_lucene(text)

    def _simple_merge(
        self, results: List[ScoredResult], top_k: int
    ) -> List[ScoredResult]:
        """Simple merge: deduplicate by node_id, keep highest score."""
        best: Dict[str, ScoredResult] = {}
        for sr in results:
            if sr.node_id not in best or sr.score > best[sr.node_id].score:
                best[sr.node_id] = sr
        sorted_results = sorted(best.values(), key=lambda x: x.score, reverse=True)
        return sorted_results[:top_k]

    def _scored_to_enriched(
        self, scored_results: List[ScoredResult]
    ) -> List[EnrichedFragment]:
        """Convert ScoredResults to EnrichedFragments."""
        enriched = []
        for sr in scored_results:
            if sr.node_type != "Fragment":
                continue

            f_data = sr.node_data.get("f", {})
            if not f_data:
                continue

            try:
                frag = self._dict_to_fragment(f_data)
            except (KeyError, ValueError) as e:
                logger.debug("Failed to convert scored result to fragment: %s", e)
                continue

            enriched.append(EnrichedFragment(
                fragment=frag,
                repo=sr.node_data.get("repo", ""),
                instance_id=sr.node_data.get("instance_id", ""),
                trajectory_summary=sr.node_data.get("summary", ""),
                action_summary=self._summarize_actions(
                    f_data.get("action_sequence", [])
                ),
                relevance_score=sr.score,
            ))
        return enriched

    def _extract_error_type(self, error_message: str) -> Optional[str]:
        """Extract error type from message."""
        match = re.search(r'(\w+Error|\w+Exception)', error_message)
        return match.group(1) if match else None

    def _extract_keywords(self, text: str) -> Set[str]:
        """Extract meaningful keywords from text, skipping stop words."""
        if not text:
            return set()
        # Tokenize: split on non-alphanumeric, keep words >= 3 chars
        words = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', text.lower())
        return {w for w in words if len(w) >= 3 and w not in _STOP_WORDS}

    def _summarize_actions(self, actions: Any) -> str:
        """Summarize action_sequence into a human-readable string.

        Extracts edit operations and file paths, condenses repeated actions.
        """
        if not actions:
            return ""

        if isinstance(actions, str):
            # Try to parse as list-like string
            actions = [a.strip() for a in actions.split(",")]

        if not isinstance(actions, list):
            return str(actions)

        edits = []
        other_actions = []
        bash_count = 0

        for action in actions:
            action_str = str(action).strip()
            if not action_str:
                continue

            # Extract edit actions with file info
            edit_match = re.match(
                r'edit\s+(\S+?)(?::(\d+))?(?::(\d+))?$', action_str, re.IGNORECASE
            )
            if edit_match:
                filepath = edit_match.group(1)
                # Get just the filename
                filename = filepath.rsplit("/", 1)[-1] if "/" in filepath else filepath
                edits.append(filename)
                continue

            if action_str.lower() in ("bash", "bash_command"):
                bash_count += 1
                continue

            other_actions.append(action_str)

        parts = []
        if edits:
            # Dedupe while preserving order
            seen = set()
            unique_edits = []
            for e in edits:
                if e not in seen:
                    seen.add(e)
                    unique_edits.append(e)
            parts.append(f"Edited: {', '.join(unique_edits[:5])}")
        if bash_count:
            parts.append(f"{bash_count} bash commands")
        if other_actions:
            parts.append(f"Other: {', '.join(other_actions[:3])}")

        return "; ".join(parts) if parts else f"{len(actions)} actions"

    def _dedupe_enriched(
        self,
        fragments: List[EnrichedFragment],
        top_k: int,
    ) -> List[EnrichedFragment]:
        """Deduplicate enriched fragments by fragment id, keeping highest score."""
        seen_ids: Dict[str, EnrichedFragment] = {}
        for ef in fragments:
            fid = ef.fragment.id
            if fid not in seen_ids or ef.relevance_score > seen_ids[fid].relevance_score:
                seen_ids[fid] = ef
        # Sort by score descending
        result = sorted(seen_ids.values(), key=lambda x: x.relevance_score, reverse=True)
        return result[:top_k]

    def _dedupe_fragments(
        self,
        fragments: List[Fragment],
        top_k: int,
    ) -> List[Fragment]:
        """Deduplicate fragments."""
        seen_ids: set = set()
        unique = []
        for f in fragments:
            if f.id not in seen_ids:
                seen_ids.add(f.id)
                unique.append(f)
        return unique[:top_k]

    def _get_warnings(self, state: State) -> List[str]:
        """Get warnings for potential failure patterns."""
        warnings = []

        if state.current_error:
            error_type = self._extract_error_type(state.current_error)
            if error_type and self.store:
                try:
                    results = self.store.execute_query(
                        "MATCH (e:ErrorPattern {error_type: $et}) RETURN e.frequency AS freq",
                        {"et": error_type},
                    )
                    for r in results:
                        freq = r.get("freq", 0)
                        if freq and freq > 100:
                            warnings.append(
                                f"{error_type} appeared in {freq} past trajectories. "
                                "Consider checking import paths and argument types carefully."
                            )
                except Exception:
                    pass

        return warnings

    def _dict_to_methodology(self, d: Dict[str, Any]) -> Methodology:
        """Convert dict to Methodology."""
        return Methodology.from_dict(d)

    def _dict_to_fragment(self, d: Dict[str, Any]) -> Fragment:
        """Convert dict to Fragment."""
        return Fragment.from_dict(d)
