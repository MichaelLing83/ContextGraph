"""Tests for Neo4j store."""

import pytest
import uuid

from agent_memory.neo4j_store import Neo4jStore
from agent_memory.models import Trajectory, Fragment, ErrorPattern


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

    def test_trajectory_round_trip(self, neo4j_uri, neo4j_auth, neo4j_available):
        """Round-trip Trajectory through create_trajectory/get_trajectory."""
        if not neo4j_available:
            pytest.skip("Neo4j not available")

        traj_id = f"traj_test_{uuid.uuid4().hex[:12]}"
        trajectory = Trajectory(
            id=traj_id,
            instance_id="test__roundtrip-1",
            repo="test/repo",
            task_type="bug_fix",
            success=True,
            total_steps=3,
            summary="Round-trip trajectory",
        )

        with Neo4jStore(uri=neo4j_uri, auth=neo4j_auth) as store:
            store.init_schema()
            try:
                store.create_trajectory(trajectory)
                loaded = store.get_trajectory(traj_id)
                assert loaded is not None
                assert loaded.id == trajectory.id
                assert loaded.instance_id == trajectory.instance_id
                assert loaded.repo == trajectory.repo
                assert loaded.summary == trajectory.summary
            finally:
                store.execute_write("MATCH (t:Trajectory {id: $id}) DETACH DELETE t", {"id": traj_id})

    def test_error_pattern_dedup_and_frequency(self, neo4j_uri, neo4j_auth, neo4j_available):
        """Creating same error type twice should aggregate frequency and merge keywords."""
        if not neo4j_available:
            pytest.skip("Neo4j not available")

        error_type = f"UnitTestError_{uuid.uuid4().hex[:12]}"
        p1 = ErrorPattern(
            id=f"err_{uuid.uuid4().hex[:12]}",
            error_type=error_type,
            error_keywords=["timeout", "connection"],
            context="unit-test",
            frequency=2,
        )
        p2 = ErrorPattern(
            id=f"err_{uuid.uuid4().hex[:12]}",
            error_type=error_type,
            error_keywords=["timeout", "socket"],
            context="unit-test",
            frequency=3,
        )

        with Neo4jStore(uri=neo4j_uri, auth=neo4j_auth) as store:
            store.init_schema()
            try:
                store.create_error_pattern(p1)
                store.create_error_pattern(p2)

                results = store.execute_query(
                    "MATCH (e:ErrorPattern {error_type: $error_type}) RETURN e",
                    {"error_type": error_type},
                )
                assert results
                node = results[0]["e"]
                assert node["frequency"] == 5
                assert set(node["error_keywords"]) == {"timeout", "connection", "socket"}
            finally:
                store.execute_write(
                    "MATCH (e:ErrorPattern {error_type: $error_type}) DETACH DELETE e",
                    {"error_type": error_type},
                )

    def test_fragment_error_pattern_relationship(self, neo4j_uri, neo4j_auth, neo4j_available):
        """Link Fragment to ErrorPattern and verify CAUSED_ERROR relation exists."""
        if not neo4j_available:
            pytest.skip("Neo4j not available")

        traj_id = f"traj_rel_{uuid.uuid4().hex[:12]}"
        fragment_id = f"frag_rel_{uuid.uuid4().hex[:12]}"
        error_type = f"RelationError_{uuid.uuid4().hex[:12]}"

        trajectory = Trajectory(
            id=traj_id,
            instance_id="test__relation-1",
            repo="test/repo",
            task_type="bug_fix",
            success=False,
            total_steps=1,
            summary="Relation test trajectory",
        )
        fragment = Fragment(
            id=fragment_id,
            step_range=(0, 0),
            fragment_type="failed_attempt",
            description="Failed fragment",
            action_sequence=["edit"],
            outcome="failed",
        )
        pattern = ErrorPattern(
            id=f"err_rel_{uuid.uuid4().hex[:12]}",
            error_type=error_type,
            error_keywords=["relation", "test"],
            context="unit-test",
            frequency=1,
        )

        with Neo4jStore(uri=neo4j_uri, auth=neo4j_auth) as store:
            store.init_schema()
            try:
                store.create_trajectory(trajectory)
                store.create_fragment(fragment, traj_id)
                store.create_error_pattern(pattern)
                store.link_fragment_to_error_pattern(fragment_id, error_type)

                rel = store.execute_query(
                    """
                    MATCH (f:Fragment {id: $fragment_id})-[:CAUSED_ERROR]->(e:ErrorPattern {error_type: $error_type})
                    RETURN count(*) AS rel_count
                    """,
                    {"fragment_id": fragment_id, "error_type": error_type},
                )
                assert rel and rel[0]["rel_count"] == 1
            finally:
                store.execute_write("MATCH (t:Trajectory {id: $id}) DETACH DELETE t", {"id": traj_id})
                store.execute_write(
                    "MATCH (e:ErrorPattern {error_type: $error_type}) DETACH DELETE e",
                    {"error_type": error_type},
                )
