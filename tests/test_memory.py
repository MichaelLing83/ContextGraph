"""Tests for AgentMemory unified API."""

import pytest
from agent_memory.memory import AgentMemory, MemoryContext, MemoryStats
from agent_memory.models import State


class TestAgentMemory:
    def test_memory_creation(self):
        """Test AgentMemory can be instantiated with mock components."""
        memory = AgentMemory(
            neo4j_uri=None,  # Use mock store
            embedding_api_key=None,  # Use mock embedder
        )
        assert memory is not None
        memory.close()

    def test_memory_context_manager(self):
        """Test AgentMemory works as context manager."""
        with AgentMemory(neo4j_uri=None, embedding_api_key=None) as memory:
            assert memory is not None

    def test_query_returns_context(self):
        """Test query method returns MemoryContext."""
        with AgentMemory(neo4j_uri=None, embedding_api_key=None) as memory:
            state = State(
                tools=["bash", "edit"],
                repo_summary="Django project",
                task_description="Fix import bug",
                current_error="ImportError: No module named 'foo'",
                phase="fixing",
            )

            context = memory.query(state)

            assert isinstance(context, MemoryContext)
            assert hasattr(context, "methodologies")
            assert hasattr(context, "warnings")

    def test_check_loop_with_history(self):
        """Test loop detection."""
        with AgentMemory(neo4j_uri=None, embedding_api_key=None) as memory:
            # Create repetitive state history
            states = []
            for i in range(5):
                state = State(
                    tools=["bash"],
                    repo_summary="Test",
                    task_description="Test",
                    current_error="ImportError: cannot import X",
                    phase="fixing",
                )
                state.last_action_type = "edit"
                states.append(state)

            loop_info = memory.check_loop(states)
            # Should detect loop with repeated same error
            assert loop_info is not None
            assert loop_info.is_stuck

    def test_get_stats(self):
        """Test stats retrieval."""
        with AgentMemory(neo4j_uri=None, embedding_api_key=None) as memory:
            stats = memory.get_stats()
            assert isinstance(stats, MemoryStats)
            assert stats.total_trajectories == 0  # No store


class TestMemoryContext:
    def test_has_suggestions(self):
        """Test has_suggestions method."""
        empty = MemoryContext()
        assert not empty.has_suggestions()

        from agent_memory.models import Methodology
        with_meth = MemoryContext(methodologies=[
            Methodology(id="m1", situation="test", strategy="test", confidence=0.8)
        ])
        assert with_meth.has_suggestions()
