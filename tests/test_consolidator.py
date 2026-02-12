"""Tests for MemoryConsolidator."""

import pytest
from unittest.mock import MagicMock

from agent_memory.consolidator import MemoryConsolidator
from agent_memory.models import Fragment


class TestMemoryConsolidator:
    @staticmethod
    def _make_fragment(
        fragment_id: str,
        description: str,
        actions,
        embedding=None,
        fragment_type: str = "error_recovery",
    ) -> Fragment:
        return Fragment(
            id=fragment_id,
            step_range=(0, 1),
            fragment_type=fragment_type,
            description=description,
            action_sequence=list(actions),
            outcome="recovered",
            embedding=embedding,
        )

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

    def test_group_similar_fragments_with_embeddings(self):
        """Fragments with embeddings should follow embedding-based grouping path."""
        consolidator = MemoryConsolidator(store=None, embedder=None)
        f1 = self._make_fragment("f1", "timeout import fix", ["edit"], embedding=[1.0, 0.0, 0.0])
        f2 = self._make_fragment("f2", "timeout import patch", ["edit"], embedding=[0.99, 0.01, 0.0])
        f3 = self._make_fragment("f3", "database migration", ["edit"], embedding=[0.0, 1.0, 0.0])

        groups = consolidator._group_similar_fragments([f1, f2, f3], similarity_threshold=0.8)
        grouped_ids = sorted(sorted(f.id for f in group) for group in groups)
        assert ["f1", "f2"] in grouped_ids
        assert ["f3"] in grouped_ids

    def test_group_similar_fragments_with_keywords(self):
        """Fragments without embeddings should fall back to keyword grouping."""
        consolidator = MemoryConsolidator(store=None, embedder=None)
        f1 = self._make_fragment("f1", "timeout connection retry", ["edit"], embedding=None)
        f2 = self._make_fragment("f2", "timeout connection", ["edit"], embedding=None)
        f3 = self._make_fragment("f3", "database migration index", ["edit"], embedding=None)
        f4 = self._make_fragment("f4", "database migration", ["edit"], embedding=None)

        groups = consolidator._group_similar_fragments([f1, f2, f3, f4], similarity_threshold=0.8)
        grouped_ids = {tuple(sorted(f.id for f in group)) for group in groups}
        assert ("f1", "f2") in grouped_ids
        assert ("f3", "f4") in grouped_ids

    def test_abstract_methodologies_threshold_and_linking(self):
        """Methodology is only created for groups with >=3 examples and linked to errors."""
        f1 = self._make_fragment("f1", "import timeout fix", ["edit"], embedding=[1.0, 0.0, 0.0])
        f2 = self._make_fragment("f2", "import timeout patch", ["edit"], embedding=[0.99, 0.01, 0.0])
        f3 = self._make_fragment("f3", "import timeout workaround", ["edit"], embedding=[0.98, 0.02, 0.0])
        f4 = self._make_fragment("f4", "db migration issue", ["test"], embedding=[0.0, 1.0, 0.0])

        store = MagicMock()
        store.execute_query.return_value = [{"f": f.to_dict()} for f in [f1, f2, f3, f4]]

        consolidator = MemoryConsolidator(store=store, embedder=None)
        consolidator._link_methodology_to_errors = MagicMock()

        new_methods = consolidator._abstract_methodologies(batch_size=10)

        assert len(new_methods) == 1
        assert "phase:fixing" in new_methods[0].situation
        assert store.create_methodology.call_count == 1
        consolidator._link_methodology_to_errors.assert_called_once()
        link_args = consolidator._link_methodology_to_errors.call_args[0]
        assert link_args[0] == new_methods[0]
        assert len(link_args[1]) == 3

    def test_merge_similar_nodes_preserves_resolved_by_edges(self):
        """Merge query should preserve ErrorPattern->Methodology links before deleting m2."""
        store = MagicMock()
        store.execute_query.return_value = [{"id1": "m1", "id2": "m2"}]
        consolidator = MemoryConsolidator(store=store, embedder=None)

        merged = consolidator._merge_similar_nodes()

        assert merged == 1
        merge_query = store.execute_write.call_args[0][0]
        assert "MERGE (e)-[:RESOLVED_BY]->(m1)" in merge_query

    def test_consolidate_uses_batch_size_for_methodology_sampling(self):
        """consolidate(batch_size=...) should drive _abstract_methodologies limit."""
        store = MagicMock()
        # _abstract_methodologies query, _merge_similar_nodes query, _cleanup query
        store.execute_query.side_effect = [[], [], [{"cnt": 0}]]
        consolidator = MemoryConsolidator(store=store, embedder=None)

        consolidator.consolidate(batch_size=7)

        first_params = store.execute_query.call_args_list[0][0][1]
        assert first_params["limit"] == 7
