"""Tests for Neo4j store."""

import pytest
from agent_memory.neo4j_store import Neo4jStore


class TestNeo4jStore:
    def test_store_creation(self, neo4j_uri, neo4j_auth):
        """Test Neo4jStore can be instantiated."""
        store = Neo4jStore(uri=neo4j_uri, auth=neo4j_auth)
        assert store is not None
        store.close()

    def test_store_context_manager(self, neo4j_uri, neo4j_auth):
        """Test Neo4jStore works as context manager."""
        with Neo4jStore(uri=neo4j_uri, auth=neo4j_auth) as store:
            assert store is not None


class TestNeo4jSchema:
    def test_init_schema(self, neo4j_uri, neo4j_auth, neo4j_available):
        """Test schema initialization creates constraints and indexes."""
        if not neo4j_available:
            pytest.skip("Neo4j not available")
        with Neo4jStore(uri=neo4j_uri, auth=neo4j_auth) as store:
            store.init_schema()
            # Verify constraints exist
            constraints = store.execute_query("SHOW CONSTRAINTS")
            constraint_names = [c.get("name", "") for c in constraints]
            assert any("trajectory_id" in n.lower() for n in constraint_names)
