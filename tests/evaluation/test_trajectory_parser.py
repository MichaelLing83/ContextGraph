"""Tests for SWE-agent trajectory parser."""

from agent_memory.evaluation.trajectory_parser import (
    parse_swe_agent_trajectory,
    SWEAgentStep,
)
from agent_memory.writer import RawTrajectory


class TestSWEAgentStep:
    def test_step_creation(self):
        step = SWEAgentStep(
            thought="Looking at the file",
            action="cat file.py",
            observation="def foo(): pass",
        )
        assert step.thought == "Looking at the file"
        assert step.action == "cat file.py"
        assert step.observation == "def foo(): pass"


class TestParseSWEAgentTrajectory:
    def test_parse_simple_trajectory(self, tmp_path):
        """Test parsing a simple SWE-agent trajectory file."""
        traj_file = tmp_path / "test__test-123.traj"
        traj_file.write_text('''{
            "environment": "swe_main",
            "trajectory": [
                {
                    "thought": "Let me explore the repo",
                    "action": "ls -la",
                    "observation": "file1.py file2.py",
                    "state": "{\\"working_dir\\": \\"/repo\\"}"
                },
                {
                    "thought": "Now edit the file",
                    "action": "edit file1.py",
                    "observation": "File edited successfully"
                }
            ],
            "info": {
                "exit_status": "submitted",
                "model_stats": {"instance_cost": 0.5}
            }
        }''')

        result = parse_swe_agent_trajectory(traj_file)

        assert isinstance(result, RawTrajectory)
        assert result.instance_id == "test__test-123"
        assert result.success is True
        assert len(result.steps) == 2
        assert result.steps[0]["action"] == "ls -la"
        assert result.steps[0]["observation"] == "file1.py file2.py"

    def test_parse_failed_trajectory(self, tmp_path):
        """Test parsing a failed trajectory (exit_status not submitted)."""
        traj_file = tmp_path / "django__django-12345.traj"
        traj_file.write_text('''{
            "environment": "swe_main",
            "trajectory": [
                {
                    "thought": "Try something",
                    "action": "edit",
                    "observation": "Error: SyntaxError"
                }
            ],
            "info": {
                "exit_status": "failed"
            }
        }''')

        result = parse_swe_agent_trajectory(traj_file)

        assert result.success is False
        assert result.repo == "django/django"
        assert result.instance_id == "django__django-12345"

    def test_extract_repo_from_instance_id(self, tmp_path):
        """Test repo extraction from various instance ID formats."""
        traj_file = tmp_path / "scikit-learn__scikit-learn-9876.traj"
        traj_file.write_text('''{
            "environment": "swe_main",
            "trajectory": [],
            "info": {"exit_status": "submitted"}
        }''')

        result = parse_swe_agent_trajectory(traj_file)

        assert result.repo == "scikit-learn/scikit-learn"

    def test_parse_top_level_exit_status(self, tmp_path):
        """Test parsing success when exit_status exists at top-level."""
        traj_file = tmp_path / "psf__requests-5432.traj"
        traj_file.write_text('''{
            "environment": "swe_main",
            "exit_status": "submitted",
            "trajectory": []
        }''')

        result = parse_swe_agent_trajectory(traj_file)

        assert result.success is True
