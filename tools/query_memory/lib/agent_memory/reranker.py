"""Reranking Pipeline — RRF + MMR + Node Distance boost.

Zep-inspired three-stage reranking:
  1. RRF (Reciprocal Rank Fusion) — merge ranked lists from cosine/BM25/BFS/community
  2. MMR (Maximal Marginal Relevance) — diversify results to avoid redundancy
  3. Node Distance Reranker — boost BFS hop-1 results
"""

from typing import List, Dict, Optional
import logging

import numpy as np

logger = logging.getLogger(__name__)


# Import ScoredResult from retriever (avoid circular by using TYPE_CHECKING at module level)
from agent_memory.retriever import ScoredResult


class RerankerPipeline:
    """Three-stage reranking pipeline for triple-search results."""

    def __init__(
        self,
        rrf_k: int = 60,
        mmr_lambda: float = 0.7,
        hop1_boost: float = 1.2,
        hop2_boost: float = 1.1,
    ):
        self.rrf_k = rrf_k
        self.mmr_lambda = mmr_lambda
        self.hop1_boost = hop1_boost
        self.hop2_boost = hop2_boost

    def rerank(
        self,
        cosine_results: List[ScoredResult],
        bm25_results: List[ScoredResult],
        bfs_results: List[ScoredResult],
        community_results: Optional[List[ScoredResult]] = None,
        query_embedding: Optional[List[float]] = None,
        top_k: int = 5,
    ) -> List[ScoredResult]:
        """Run the full reranking pipeline.

        1. RRF fusion across all channels
        2. Node distance boost for BFS results
        3. MMR diversity selection

        Returns top_k reranked ScoredResults.
        """
        community_results = community_results or []

        # Stage 1: RRF fusion
        rrf_scores = self._reciprocal_rank_fusion(
            [cosine_results, bm25_results, bfs_results, community_results]
        )

        if not rrf_scores:
            return []

        # Stage 2: Node distance boost
        rrf_scores = self._node_distance_boost(rrf_scores, bfs_results)

        # Build lookup for ScoredResult objects
        result_lookup: Dict[str, ScoredResult] = {}
        for sr in cosine_results + bm25_results + bfs_results + community_results:
            if sr.node_id not in result_lookup or sr.score > result_lookup[sr.node_id].score:
                result_lookup[sr.node_id] = sr

        # Create ordered list of (node_id, rrf_score)
        sorted_by_rrf = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

        # Stage 3: MMR diversity
        if query_embedding:
            # Collect embeddings for candidates
            candidate_embeddings: Dict[str, Optional[List[float]]] = {}
            for node_id, _ in sorted_by_rrf:
                sr = result_lookup.get(node_id)
                if sr:
                    emb = sr.node_data.get("f", {}).get("embedding")
                    candidate_embeddings[node_id] = emb

            selected_ids = self._mmr_select(
                query_embedding=query_embedding,
                candidate_ids=[nid for nid, _ in sorted_by_rrf],
                candidate_scores={nid: score for nid, score in sorted_by_rrf},
                candidate_embeddings=candidate_embeddings,
                top_k=top_k,
            )
        else:
            selected_ids = [nid for nid, _ in sorted_by_rrf[:top_k]]

        # Build final results
        final = []
        for node_id in selected_ids:
            sr = result_lookup.get(node_id)
            if sr:
                # Update score with RRF score
                updated = ScoredResult(
                    node_id=sr.node_id,
                    node_type=sr.node_type,
                    score=rrf_scores.get(node_id, sr.score),
                    source=sr.source,
                    node_data=sr.node_data,
                    hop_distance=sr.hop_distance,
                )
                final.append(updated)

        return final

    def _reciprocal_rank_fusion(
        self, ranked_lists: List[List[ScoredResult]]
    ) -> Dict[str, float]:
        """Reciprocal Rank Fusion across multiple ranked lists.

        score(d) = sum over lists L: 1 / (k + rank_L(d))

        where k is a constant (default 60) that prevents over-weighting
        top-ranked results.
        """
        scores: Dict[str, float] = {}

        for ranked_list in ranked_lists:
            if not ranked_list:
                continue
            # Sort by score descending to get ranks
            sorted_list = sorted(ranked_list, key=lambda x: x.score, reverse=True)
            for rank, sr in enumerate(sorted_list, start=1):
                rrf_score = 1.0 / (self.rrf_k + rank)
                scores[sr.node_id] = scores.get(sr.node_id, 0.0) + rrf_score

        return scores

    def _node_distance_boost(
        self,
        rrf_scores: Dict[str, float],
        bfs_results: List[ScoredResult],
    ) -> Dict[str, float]:
        """Boost scores based on BFS hop distance.

        hop-1 results get hop1_boost multiplier, hop-2 get hop2_boost.
        """
        hop_lookup: Dict[str, int] = {}
        for sr in bfs_results:
            if sr.node_id not in hop_lookup:
                hop_lookup[sr.node_id] = sr.hop_distance

        boosted = dict(rrf_scores)
        for node_id, hop_dist in hop_lookup.items():
            if node_id in boosted:
                if hop_dist <= 1:
                    boosted[node_id] *= self.hop1_boost
                elif hop_dist == 2:
                    boosted[node_id] *= self.hop2_boost

        return boosted

    def _mmr_select(
        self,
        query_embedding: List[float],
        candidate_ids: List[str],
        candidate_scores: Dict[str, float],
        candidate_embeddings: Dict[str, Optional[List[float]]],
        top_k: int,
    ) -> List[str]:
        """Maximal Marginal Relevance selection.

        MMR = argmax [ lambda * sim(q, d) - (1-lambda) * max(sim(d, d_sel)) ]

        Balances relevance (lambda) with diversity (1-lambda).
        """
        if not candidate_ids:
            return []

        query_vec = np.array(query_embedding, dtype=np.float64)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return candidate_ids[:top_k]
        query_vec = query_vec / query_norm

        # Pre-compute normalized embeddings (skip dimension mismatches)
        query_dim = len(query_embedding)
        candidate_vecs: Dict[str, Optional[np.ndarray]] = {}
        for cid in candidate_ids:
            emb = candidate_embeddings.get(cid)
            if emb is not None and len(emb) == query_dim:
                vec = np.array(emb, dtype=np.float64)
                norm = np.linalg.norm(vec)
                candidate_vecs[cid] = vec / norm if norm > 0 else None
            else:
                candidate_vecs[cid] = None

        selected: List[str] = []
        remaining = set(candidate_ids)

        for _ in range(min(top_k, len(candidate_ids))):
            best_id = None
            best_mmr = float("-inf")

            for cid in remaining:
                vec = candidate_vecs.get(cid)
                if vec is None:
                    # No embedding: use RRF score as relevance, 0 as similarity
                    relevance = candidate_scores.get(cid, 0.0)
                    max_sim_selected = 0.0
                else:
                    # Cosine similarity with query
                    relevance = float(np.dot(query_vec, vec))

                    # Max similarity with already-selected
                    max_sim_selected = 0.0
                    for sel_id in selected:
                        sel_vec = candidate_vecs.get(sel_id)
                        if sel_vec is not None:
                            sim = float(np.dot(vec, sel_vec))
                            max_sim_selected = max(max_sim_selected, sim)

                mmr_score = (
                    self.mmr_lambda * relevance
                    - (1 - self.mmr_lambda) * max_sim_selected
                )

                if mmr_score > best_mmr:
                    best_mmr = mmr_score
                    best_id = cid

            if best_id is not None:
                selected.append(best_id)
                remaining.discard(best_id)
            else:
                break

        return selected
