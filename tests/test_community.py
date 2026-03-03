"""Tests for CommunityDetector."""

import pytest
from unittest.mock import MagicMock, patch
from collections import defaultdict

from agent_memory.community import CommunityDetector
from agent_memory.models import Community


class TestCommunityDetector:
    def test_creation(self):
        """Test CommunityDetector can be instantiated."""
        detector = CommunityDetector(store=None, embedder=None)
        assert detector is not None

    def test_detect_without_store(self):
        """Test detect_communities returns 0 without store."""
        detector = CommunityDetector(store=None, embedder=None)
        assert detector.detect_communities() == 0

    def test_assign_new_node_without_store(self):
        """Test assign_new_node returns None without store."""
        detector = CommunityDetector(store=None, embedder=None)
        assert detector.assign_new_node("f1") is None

    def test_fallback_label_propagation(self):
        """Test Python-side label propagation on a simple graph."""
        # Build a graph with two clear clusters:
        # Cluster 1: f1-f2-f3 (connected via errors)
        # Cluster 2: f4-f5 (connected via trajectory)
        edge_data = [
            {"src": "f1", "dst": "f2", "rel_type": "error"},
            {"src": "f2", "dst": "f3", "rel_type": "error"},
            {"src": "f4", "dst": "f5", "rel_type": "trajectory"},
        ]

        written_labels = {}

        class DummyStore:
            def execute_query(self, query, parameters=None):
                if "gds.version" in query:
                    raise Exception("GDS not available")
                if "CAUSED_ERROR" in query:
                    return edge_data
                return []

            def execute_write(self, query, parameters=None):
                if "SET f.community_id" in query and parameters:
                    written_labels[parameters.get("id")] = parameters.get("cid")

        detector = CommunityDetector(store=DummyStore(), embedder=None, min_community_size=2)
        assignments = detector._fallback_community_detection()

        assert len(assignments) == 5
        # f1, f2, f3 should share the same community
        assert assignments["f1"] == assignments["f2"]
        assert assignments["f2"] == assignments["f3"]
        # f4, f5 should share a different community
        assert assignments["f4"] == assignments["f5"]
        # Two clusters should be different
        assert assignments["f1"] != assignments["f4"]

    def test_build_communities_filters_small(self):
        """Communities smaller than min_community_size should be filtered out."""
        detector = CommunityDetector(store=None, embedder=None, min_community_size=3)

        assignments = {
            "f1": 0, "f2": 0, "f3": 0,  # size 3 - should pass
            "f4": 1, "f5": 1,             # size 2 - should be filtered
            "f6": 2,                       # size 1 - should be filtered
        }
        communities = detector._build_communities(assignments)

        assert len(communities) == 1
        assert communities[0].node_count == 3

    def test_community_model(self):
        """Test Community dataclass."""
        community = Community(
            id="comm_1",
            community_id=42,
            summary="Test community",
            node_count=5,
            error_types=["ImportError", "TypeError"],
        )
        d = community.to_dict()
        assert d["community_id"] == 42
        assert d["node_count"] == 5

        restored = Community.from_dict(d)
        assert restored.community_id == 42
        assert restored.summary == "Test community"

    def test_refresh_communities(self):
        """Test refresh detects new communities and cleans up orphans."""
        calls = []

        class DummyStore:
            def execute_query(self, query, parameters=None):
                calls.append(("query", query))
                if "gds.version" in query:
                    raise Exception("No GDS")
                if "CAUSED_ERROR" in query:
                    return [
                        {"src": "f1", "dst": "f2", "rel_type": "error"},
                        {"src": "f2", "dst": "f3", "rel_type": "error"},
                    ]
                return []

            def execute_write(self, query, parameters=None):
                calls.append(("write", query))

        detector = CommunityDetector(store=DummyStore(), embedder=None, min_community_size=2)
        count = detector.refresh_communities()

        assert count > 0
        # Should have run community detection and cleaned up orphans
        write_queries = [c[1] for c in calls if c[0] == "write"]
        assert any("DETACH DELETE" in q for q in write_queries)

    def test_check_gds_caches_result(self):
        """GDS check should only query once."""
        call_count = 0

        class DummyStore:
            def execute_query(self, query, parameters=None):
                nonlocal call_count
                call_count += 1
                raise Exception("No GDS")

        detector = CommunityDetector(store=DummyStore(), embedder=None)

        assert detector._check_gds() is False
        assert detector._check_gds() is False
        assert call_count == 1  # Only queried once
