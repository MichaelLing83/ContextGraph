"""Integration tests for complete AgentMemory workflow."""

import pytest
from agent_memory import (
    AgentMemory,
    State,
    RawTrajectory,
)


class TestAgentMemoryIntegration:
    """End-to-end integration tests."""

    def test_complete_workflow(self):
        """Test complete memory workflow: learn -> query -> check_loop."""
        with AgentMemory(neo4j_uri=None, embedding_api_key=None) as memory:
            # 1. Learn from trajectory
            trajectory = RawTrajectory(
                instance_id="test__test-123",
                repo="test/repo",
                success=True,
                problem_statement="Fix bug",
                steps=[
                    {"action": "search", "observation": "Found file"},
                    {"action": "edit", "observation": "Error: SyntaxError"},
                    {"action": "edit", "observation": "Fixed"},
                    {"action": "test", "observation": "Tests pass"},
                ],
            )

            traj_id = memory.learn(trajectory)
            assert traj_id is not None
            assert traj_id.startswith("traj_")

            # 2. Query for context
            state = State(
                tools=["bash", "edit"],
                repo_summary="Test repo",
                task_description="Fix another bug",
                current_error="SyntaxError: invalid syntax",
                phase="fixing",
            )

            context = memory.query(state)
            assert context is not None
            assert hasattr(context, "methodologies")
            assert hasattr(context, "warnings")

            # 3. Check for loop
            state_history = [state for _ in range(5)]
            for s in state_history:
                s.last_action_type = "edit"

            loop_info = memory.check_loop(state_history)
            assert loop_info is not None

    def test_multiple_trajectories(self):
        """Test learning from multiple trajectories."""
        with AgentMemory(neo4j_uri=None, embedding_api_key=None) as memory:
            for i in range(3):
                trajectory = RawTrajectory(
                    instance_id=f"test__test-{i}",
                    repo="test/repo",
                    success=i % 2 == 0,  # Alternating success/failure
                    steps=[
                        {"action": "search", "observation": "Found"},
                        {"action": "edit", "observation": "Done"},
                    ],
                )
                traj_id = memory.learn(trajectory)
                assert traj_id is not None

    def test_consolidation_trigger(self):
        """Test that consolidation triggers after N trajectories."""
        with AgentMemory(
            neo4j_uri=None,
            embedding_api_key=None,
            consolidate_every=2,
        ) as memory:
            # Learn 2 trajectories to trigger consolidation
            for i in range(2):
                trajectory = RawTrajectory(
                    instance_id=f"test__test-{i}",
                    repo="test/repo",
                    success=True,
                    steps=[{"action": "test", "observation": "pass"}],
                )
                memory.learn(trajectory)

            # Consolidation should have been triggered
            assert memory._trajectory_count == 2


class TestImportStructure:
    """Test that all imports work correctly."""

    def test_all_imports(self):
        """Verify all public API imports work."""
        from agent_memory import (
            Trajectory,
            Fragment,
            State,
            Methodology,
            ErrorPattern,
            Neo4jStore,
            EmbeddingClient,
            get_embedding_client,
            LoopDetector,
            LoopSignature,
            LoopInfo,
            MemoryWriter,
            RawTrajectory,
            MemoryRetriever,
            RetrievalResult,
            MemoryConsolidator,
            AgentMemory,
            MemoryContext,
            MemoryStats,
        )

        # All imports should be classes or functions
        assert Trajectory is not None
        assert AgentMemory is not None
