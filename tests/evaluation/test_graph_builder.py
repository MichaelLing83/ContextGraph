"""Tests for graph builder."""

from agent_memory.evaluation.graph_builder import build_graph
from agent_memory import AgentMemory


class TestBuildGraph:
    def test_build_graph_from_trajectories(self, tmp_path):
        """Test building graph from trajectory files."""
        # Create a sample trajectory file
        traj_file = tmp_path / "test__test-123.traj"
        traj_file.write_text('''{
            "environment": "swe_main",
            "trajectory": [
                {"thought": "Explore", "action": "ls", "observation": "file.py"},
                {"thought": "Edit", "action": "edit file.py", "observation": "Done"}
            ],
            "info": {"exit_status": "submitted"}
        }''')

        # Build graph in mock mode
        memory = AgentMemory(neo4j_uri=None, embedding_api_key=None)
        result = build_graph([traj_file], memory)

        assert result.trajectories_loaded == 1
        assert result.fragments_created >= 1
        memory.close()

    def test_build_graph_multiple_trajectories(self, tmp_path):
        """Test building graph from multiple trajectories."""
        for i in range(3):
            traj_file = tmp_path / f"test__test-{i}.traj"
            traj_file.write_text(f'''{{
                "environment": "swe_main",
                "trajectory": [
                    {{"thought": "Step", "action": "action{i}", "observation": "obs"}}
                ],
                "info": {{"exit_status": "submitted"}}
            }}''')

        memory = AgentMemory(neo4j_uri=None, embedding_api_key=None)
        result = build_graph(list(tmp_path.glob("*.traj")), memory)

        assert result.trajectories_loaded == 3
        memory.close()

    def test_build_graph_with_consolidation(self, tmp_path):
        """Test that consolidation is triggered after building."""
        traj_file = tmp_path / "test__test-1.traj"
        traj_file.write_text('''{
            "environment": "swe_main",
            "trajectory": [
                {"thought": "Fix", "action": "edit", "observation": "Fixed"}
            ],
            "info": {"exit_status": "submitted"}
        }''')

        memory = AgentMemory(neo4j_uri=None, embedding_api_key=None)
        result = build_graph([traj_file], memory, consolidate=True)

        assert result.consolidation_run is True
        memory.close()
