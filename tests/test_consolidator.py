"""Tests for MemoryConsolidator."""

import pytest
from agent_memory.consolidator import MemoryConsolidator


class TestMemoryConsolidator:
    def test_consolidator_creation(self):
        """Test consolidator can be instantiated."""
        consolidator = MemoryConsolidator(store=None, embedder=None)
        assert consolidator is not None

    def test_group_fragments_by_error(self):
        """Test fragment grouping by error type."""
        consolidator = MemoryConsolidator(store=None, embedder=None)

        fragments_data = [
            {"error_type": "ImportError", "description": "Fixed import"},
            {"error_type": "ImportError", "description": "Another import fix"},
            {"error_type": "TypeError", "description": "Fixed type"},
        ]

        groups = consolidator._group_by_error_type(fragments_data)

        assert "ImportError" in groups
        assert len(groups["ImportError"]) == 2
        assert "TypeError" in groups
        assert len(groups["TypeError"]) == 1

    def test_consolidate_without_store(self):
        """Test consolidate returns empty stats without store."""
        consolidator = MemoryConsolidator(store=None, embedder=None)
        stats = consolidator.consolidate()

        assert stats["methodologies_created"] == 0
        assert stats["nodes_merged"] == 0
        assert stats["nodes_cleaned"] == 0

    def test_should_consolidate(self):
        """Test consolidation threshold logic."""
        consolidator = MemoryConsolidator(store=None, embedder=None, consolidation_interval=16)

        assert not consolidator.should_consolidate(10)
        assert consolidator.should_consolidate(16)
        assert consolidator.should_consolidate(32)

    def test_calculate_similarity(self):
        """Test similarity calculation between fragments."""
        consolidator = MemoryConsolidator(store=None, embedder=None)

        # Same embeddings should have similarity 1.0
        emb1 = [1.0, 0.0, 0.0]
        emb2 = [1.0, 0.0, 0.0]
        assert consolidator._calculate_similarity(emb1, emb2) == pytest.approx(1.0, rel=0.01)

        # Orthogonal embeddings should have similarity 0.0
        emb3 = [0.0, 1.0, 0.0]
        assert consolidator._calculate_similarity(emb1, emb3) == pytest.approx(0.0, rel=0.01)
