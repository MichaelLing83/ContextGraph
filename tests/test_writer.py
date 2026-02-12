"""Tests for MemoryWriter."""

import pytest
from agent_memory.writer import MemoryWriter, RawTrajectory
from agent_memory.models import Trajectory, Fragment


class TestMemoryWriter:
    def test_segment_into_fragments_basic(self):
        """Test basic trajectory segmentation."""
        writer = MemoryWriter(store=None, embedder=None)

        # Create a simple raw trajectory
        raw = RawTrajectory(
            instance_id="test__test-123",
            repo="test/repo",
            success=True,
            steps=[
                {"action": "search", "observation": "Found file.py"},
                {"action": "open", "observation": "Opened file.py"},
                {"action": "edit", "observation": "Error: SyntaxError"},
                {"action": "edit", "observation": "Error: SyntaxError"},
                {"action": "edit", "observation": "Success: file saved"},
                {"action": "test", "observation": "All tests pass"},
            ],
        )

        fragments = writer._segment_into_fragments(raw)

        assert len(fragments) >= 1

    def test_extract_error_patterns(self):
        """Test error pattern extraction."""
        writer = MemoryWriter(store=None, embedder=None)

        observations = [
            "ImportError: cannot import name 'Foo' from 'module'",
            "TypeError: expected int, got str",
            "ImportError: No module named 'bar'",
        ]

        patterns = writer._extract_error_patterns(observations)

        assert len(patterns) >= 2  # ImportError and TypeError
        import_errors = [p for p in patterns if p.error_type == "ImportError"]
        assert len(import_errors) >= 1

    def test_generate_summary(self):
        """Test summary generation."""
        writer = MemoryWriter(store=None, embedder=None)

        raw = RawTrajectory(
            instance_id="django__django-12345",
            repo="django/django",
            success=True,
            steps=[
                {"action": "search", "observation": "Found models.py"},
                {"action": "edit", "observation": "Fixed import"},
            ],
            problem_statement="Fix import error in models.py",
        )

        summary = writer._generate_summary(raw)
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_infer_task_type(self):
        """Test task type inference."""
        writer = MemoryWriter(store=None, embedder=None)

        # Bug fix
        raw_bug = RawTrajectory(
            instance_id="test-123",
            repo="test/repo",
            success=True,
            steps=[],
            problem_statement="Fix the bug where users can't login",
        )
        assert writer._infer_task_type(raw_bug) == "bug_fix"

        # Feature
        raw_feature = RawTrajectory(
            instance_id="test-456",
            repo="test/repo",
            success=True,
            steps=[],
            problem_statement="Add new authentication feature",
        )
        assert writer._infer_task_type(raw_feature) == "feature"


class TestRawTrajectory:
    def test_raw_trajectory_creation(self):
        """Test RawTrajectory creation."""
        raw = RawTrajectory(
            instance_id="test-123",
            repo="test/repo",
            success=True,
            steps=[{"action": "test", "observation": "pass"}],
        )
        assert raw.total_steps == 1
        assert raw.instance_id == "test-123"
