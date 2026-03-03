"""Tests for RerankerPipeline."""

import pytest

from agent_memory.reranker import RerankerPipeline
from agent_memory.retriever import ScoredResult


def _make_scored(node_id, score, source, embedding=None, hop_distance=0):
    node_data = {"f": {"id": node_id, "step_range": [0, 1],
                        "fragment_type": "error_recovery",
                        "description": f"desc_{node_id}",
                        "action_sequence": [], "outcome": "success"}}
    if embedding is not None:
        node_data["f"]["embedding"] = embedding
    return ScoredResult(
        node_id=node_id,
        node_type="Fragment",
        score=score,
        source=source,
        node_data=node_data,
        hop_distance=hop_distance,
    )


class TestRerankerPipeline:
    def test_rrf_overlapping_lists(self):
        """RRF should boost items that appear in multiple lists."""
        reranker = RerankerPipeline(rrf_k=60)

        cosine = [_make_scored("f1", 0.9, "cosine"), _make_scored("f2", 0.7, "cosine")]
        bm25 = [_make_scored("f1", 5.0, "bm25"), _make_scored("f3", 3.0, "bm25")]
        bfs = [_make_scored("f4", 0.5, "bfs")]

        result = reranker.rerank(cosine, bm25, bfs, top_k=3)

        # f1 appears in both cosine and bm25, should rank highest
        assert result[0].node_id == "f1"
        assert len(result) == 3

    def test_rrf_disjoint_lists(self):
        """RRF should handle completely disjoint lists."""
        reranker = RerankerPipeline(rrf_k=60)

        cosine = [_make_scored("f1", 0.9, "cosine")]
        bm25 = [_make_scored("f2", 5.0, "bm25")]
        bfs = [_make_scored("f3", 0.5, "bfs")]

        result = reranker.rerank(cosine, bm25, bfs, top_k=3)

        assert len(result) == 3
        ids = {r.node_id for r in result}
        assert ids == {"f1", "f2", "f3"}

    def test_rrf_empty_lists(self):
        """RRF should handle empty lists gracefully."""
        reranker = RerankerPipeline()
        result = reranker.rerank([], [], [], top_k=5)
        assert result == []

    def test_node_distance_boost(self):
        """BFS hop-1 results should get boosted."""
        reranker = RerankerPipeline(hop1_boost=1.5, hop2_boost=1.1)

        cosine = [_make_scored("f1", 0.9, "cosine")]
        bm25 = []
        bfs = [_make_scored("f2", 0.33, "bfs", hop_distance=1),
               _make_scored("f3", 0.33, "bfs", hop_distance=2)]

        result = reranker.rerank(cosine, bm25, bfs, top_k=3)
        assert len(result) <= 3
        # f2 should be boosted more than f3
        scores_by_id = {r.node_id: r.score for r in result}
        if "f2" in scores_by_id and "f3" in scores_by_id:
            assert scores_by_id["f2"] >= scores_by_id["f3"]

    def test_mmr_diversity(self):
        """MMR should select diverse results when embeddings are similar."""
        # Use very low lambda to strongly prefer diversity
        reranker = RerankerPipeline(mmr_lambda=0.3)

        # f1 and f2 have nearly identical embeddings, f3 is orthogonal
        emb_a = [1.0, 0.0, 0.0]
        emb_b = [0.999, 0.001, 0.0]  # Almost identical to a
        emb_c = [0.0, 1.0, 0.0]      # Orthogonal to a

        cosine = [
            _make_scored("f1", 0.95, "cosine", embedding=emb_a),
            _make_scored("f2", 0.90, "cosine", embedding=emb_b),
            _make_scored("f3", 0.80, "cosine", embedding=emb_c),
        ]

        result = reranker.rerank(
            cosine, [], [], top_k=3,
            query_embedding=[1.0, 0.0, 0.0],
        )

        ids = [r.node_id for r in result]
        assert len(ids) == 3
        # f1 should be first (highest cosine with query)
        assert ids[0] == "f1"
        # With strong diversity preference (lambda=0.3), f3 (orthogonal) should
        # be preferred over f2 (nearly identical to f1 already selected)
        assert ids.index("f3") < ids.index("f2")

    def test_mmr_no_embedding_fallback(self):
        """MMR without embeddings should fall back to score ordering."""
        reranker = RerankerPipeline()

        cosine = [
            _make_scored("f1", 0.9, "cosine"),
            _make_scored("f2", 0.8, "cosine"),
        ]

        result = reranker.rerank(cosine, [], [], top_k=2)
        assert len(result) == 2

    def test_community_results_integration(self):
        """Community results should participate in RRF."""
        reranker = RerankerPipeline()

        cosine = [_make_scored("f1", 0.9, "cosine")]
        community = [_make_scored("f2", 0.7, "community")]

        result = reranker.rerank(cosine, [], [], community_results=community, top_k=2)
        ids = {r.node_id for r in result}
        assert "f1" in ids
        assert "f2" in ids

    def test_reciprocal_rank_fusion_scores(self):
        """Verify RRF score calculation."""
        reranker = RerankerPipeline(rrf_k=60)

        scores = reranker._reciprocal_rank_fusion([
            [_make_scored("f1", 0.9, "cosine"), _make_scored("f2", 0.5, "cosine")],
            [_make_scored("f1", 3.0, "bm25")],
        ])

        # f1 appears in both lists: rank 1 in both
        # RRF score = 1/(60+1) + 1/(60+1) = 2/61
        expected_f1 = 1 / 61 + 1 / 61
        assert abs(scores["f1"] - expected_f1) < 1e-6

        # f2 appears in one list: rank 2
        expected_f2 = 1 / 62
        assert abs(scores["f2"] - expected_f2) < 1e-6
