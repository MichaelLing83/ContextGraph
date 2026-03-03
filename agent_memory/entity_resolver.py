"""Entity Resolution Pipeline — Semantic deduplication of ErrorPatterns and Methodologies.

Two-stage approach:
  1. Candidate retrieval via cosine + BM25 on existing entities
  2. Threshold decision: if similarity > threshold, merge into existing entity

Prevents graph bloat by resolving near-duplicate nodes before creation.
"""

from typing import Tuple, List, Optional, TYPE_CHECKING
import math
import logging

from agent_memory.models import ErrorPattern, Methodology

if TYPE_CHECKING:
    from agent_memory.neo4j_store import Neo4jStore
    from agent_memory.embeddings import EmbeddingClient

logger = logging.getLogger(__name__)


class EntityResolver:
    """Resolve near-duplicate ErrorPatterns and Methodologies before creation."""

    def __init__(
        self,
        store: Optional["Neo4jStore"],
        embedder: Optional["EmbeddingClient"],
        error_threshold: float = 0.85,
        methodology_threshold: float = 0.85,
    ):
        self.store = store
        self.embedder = embedder
        self.error_threshold = error_threshold
        self.methodology_threshold = methodology_threshold

    def resolve_error_pattern(
        self, new_pattern: ErrorPattern
    ) -> Tuple[ErrorPattern, bool]:
        """Resolve an ErrorPattern against existing ones.

        Returns:
            Tuple of (pattern, is_new):
              - If merged: (existing_pattern with updated keywords/freq, False)
              - If new: (new_pattern unchanged, True)
        """
        if not self.store:
            return new_pattern, True

        # Stage 1: Find candidates by error_type (exact match) and keyword overlap
        candidates = self._find_error_candidates(new_pattern)

        if not candidates:
            return new_pattern, True

        # Stage 2: Score candidates and check threshold
        best_match = None
        best_score = 0.0

        new_kw_set = set(k.lower() for k in new_pattern.error_keywords)

        for candidate in candidates:
            # Compute keyword similarity
            cand_kw_set = set(k.lower() for k in candidate.error_keywords)
            if not new_kw_set and not cand_kw_set:
                keyword_sim = 1.0 if new_pattern.error_type == candidate.error_type else 0.0
            else:
                union = new_kw_set | cand_kw_set
                keyword_sim = len(new_kw_set & cand_kw_set) / max(len(union), 1)

            # Compute embedding similarity if available
            embedding_sim = 0.0
            if self.embedder and new_pattern.error_keywords:
                new_text = f"{new_pattern.error_type} {' '.join(new_pattern.error_keywords)}"
                cand_text = f"{candidate.error_type} {' '.join(candidate.error_keywords)}"
                new_emb = self.embedder.embed(new_text)
                cand_emb = self.embedder.embed(cand_text)
                embedding_sim = self._cosine_similarity(new_emb, cand_emb)

            # Combined score (weight embedding higher if available)
            if embedding_sim > 0:
                score = 0.6 * embedding_sim + 0.4 * keyword_sim
            else:
                score = keyword_sim

            if score > best_score:
                best_score = score
                best_match = candidate

        if best_match and best_score >= self.error_threshold:
            # Merge: add new keywords and increment frequency
            merged_keywords = list(best_match.error_keywords)
            for kw in new_pattern.error_keywords:
                if kw not in merged_keywords:
                    merged_keywords.append(kw)
            best_match.error_keywords = merged_keywords
            best_match.frequency += new_pattern.frequency
            logger.debug(
                "Resolved ErrorPattern '%s' -> existing '%s' (score=%.2f)",
                new_pattern.error_type, best_match.id, best_score,
            )
            return best_match, False

        return new_pattern, True

    def resolve_methodology(
        self, new_methodology: Methodology
    ) -> Tuple[Methodology, bool]:
        """Resolve a Methodology against existing ones.

        Returns:
            Tuple of (methodology, is_new):
              - If merged: (existing with updated counts, False)
              - If new: (new_methodology unchanged, True)
        """
        if not self.store:
            return new_methodology, True

        # Stage 1: Find candidates via full-text search on situation+strategy
        candidates = self._find_methodology_candidates(new_methodology)

        if not candidates:
            return new_methodology, True

        # Stage 2: Score candidates
        best_match = None
        best_score = 0.0

        for candidate in candidates:
            # Compute text similarity
            text_sim = self._text_similarity(
                new_methodology.situation + " " + new_methodology.strategy,
                candidate.situation + " " + candidate.strategy,
            )

            # Compute embedding similarity if available
            embedding_sim = 0.0
            if (new_methodology.embedding and candidate.embedding and
                    len(new_methodology.embedding) == len(candidate.embedding)):
                embedding_sim = self._cosine_similarity(
                    new_methodology.embedding, candidate.embedding
                )

            # Combined score
            if embedding_sim > 0:
                score = 0.6 * embedding_sim + 0.4 * text_sim
            else:
                score = text_sim

            if score > best_score:
                best_score = score
                best_match = candidate

        if best_match and best_score >= self.methodology_threshold:
            # Merge counts into existing
            best_match.success_count += new_methodology.success_count
            best_match.failure_count += new_methodology.failure_count
            best_match.confidence = best_match.success_rate
            # Merge source fragment IDs
            existing_ids = set(best_match.source_fragment_ids)
            for fid in new_methodology.source_fragment_ids:
                if fid not in existing_ids:
                    best_match.source_fragment_ids.append(fid)
            logger.debug(
                "Resolved Methodology '%s' -> existing '%s' (score=%.2f)",
                new_methodology.id, best_match.id, best_score,
            )
            return best_match, False

        return new_methodology, True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_error_candidates(self, pattern: ErrorPattern) -> List[ErrorPattern]:
        """Find existing ErrorPattern candidates for resolution."""
        if not self.store:
            return []

        # Query by error_type (exact match is most likely for same error class)
        query = """
        MATCH (e:ErrorPattern)
        WHERE e.error_type = $error_type
        RETURN e
        LIMIT 10
        """
        try:
            results = self.store.execute_query(query, {"error_type": pattern.error_type})
        except Exception as e:
            logger.debug("Error candidate search failed: %s", e)
            return []

        return [ErrorPattern.from_dict(r["e"]) for r in results if "e" in r]

    def _find_methodology_candidates(self, methodology: Methodology) -> List[Methodology]:
        """Find existing Methodology candidates for resolution."""
        if not self.store:
            return []

        # Use full-text index on situation+strategy
        safe_query = " ".join(methodology.situation.split()[:5])
        if not safe_query.strip():
            return []

        # Escape Lucene special chars
        from agent_memory.utils import escape_lucene
        safe_query = escape_lucene(safe_query)

        query = """
        CALL db.index.fulltext.queryNodes('methodology_situation', $query)
        YIELD node, score
        RETURN node AS m, score
        LIMIT 5
        """
        try:
            results = self.store.execute_query(query, {"query": safe_query})
        except Exception as e:
            logger.debug("Methodology candidate search failed: %s", e)
            return []

        return [Methodology.from_dict(r["m"]) for r in results if "m" in r]

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not a or not b or len(a) != len(b):
            return 0.0

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot / (norm_a * norm_b)

    def _text_similarity(self, text_a: str, text_b: str) -> float:
        """Compute Jaccard similarity on word sets."""
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())

        if not words_a and not words_b:
            return 1.0
        if not words_a or not words_b:
            return 0.0

        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union)
