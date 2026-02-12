"""Tests for MemoryWriter."""

import pytest

from agent_memory.writer import MemoryWriter, RawTrajectory


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

    @pytest.mark.parametrize(
        "steps,success,expected_final_type",
        [
            # No errors: final fragment should be successful_fix when overall run succeeds.
            (
                [
                    {"action": "search", "observation": "Looking for target file"},
                    {"action": "open", "observation": "Opened file.py"},
                    {"action": "edit", "observation": "Applied local change"},
                    {"action": "test", "observation": "All tests pass"},
                ],
                True,
                "successful_fix",
            ),
            # Error state never recovers: final fragment should stay failed_attempt.
            (
                [
                    {"action": "search", "observation": "Found file.py"},
                    {"action": "edit", "observation": "ImportError: cannot import name Foo"},
                    {"action": "edit", "observation": "TypeError: still broken"},
                    {"action": "test", "observation": "ValueError: still failing"},
                ],
                True,
                "failed_attempt",
            ),
            # Failed run with partial recovery should not end as successful_fix.
            (
                [
                    {"action": "search", "observation": "Found file.py"},
                    {"action": "edit", "observation": "ImportError: cannot import name Foo"},
                    {"action": "edit", "observation": "Applied import fix"},
                    {"action": "test", "observation": "Some tests did not pass"},
                ],
                False,
                "exploration",
            ),
        ],
    )
    def test_segment_into_fragments_edge_cases(self, steps, success, expected_final_type):
        """Test segmentation boundary conditions around error recovery and run success."""
        writer = MemoryWriter(store=None, embedder=None)
        raw = RawTrajectory(
            instance_id="edge__test-123",
            repo="test/repo",
            success=success,
            steps=steps,
        )

        fragments = writer._segment_into_fragments(raw)
        assert fragments
        assert fragments[-1].fragment_type == expected_final_type

        if not success:
            assert fragments[-1].fragment_type != "successful_fix"

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

    def test_has_error_matches_python_style_errors(self):
        """Test _has_error catches standard Python Error/Exception patterns."""
        writer = MemoryWriter(store=None, embedder=None)

        assert writer._has_error("ImportError: cannot import name Foo")
        assert writer._has_error("TypeError: expected int")
        assert writer._has_error("ValueError: invalid value")

    def test_write_trajectory_links_fragment_to_error_patterns(self):
        """Test write_trajectory creates Fragment->ErrorPattern relations."""
        calls = []

        class DummyStore:
            def create_trajectory(self, trajectory):
                calls.append(("trajectory", trajectory.id))

            def create_fragment(self, fragment, trajectory_id):
                calls.append(("fragment", fragment.id, trajectory_id))

            def create_error_pattern(self, pattern):
                calls.append(("error_pattern", pattern.error_type))

            def link_fragment_to_error_pattern(self, fragment_id, error_type):
                calls.append(("link", fragment_id, error_type))

        writer = MemoryWriter(store=DummyStore(), embedder=None)
        raw = RawTrajectory(
            instance_id="test__test-456",
            repo="test/repo",
            success=False,
            steps=[
                {"action": "edit", "observation": "ImportError: cannot import name Foo"},
                {"action": "edit", "observation": "ImportError: cannot import name Foo"},
                {"action": "edit", "observation": "TypeError: expected int, got str"},
            ],
        )

        writer.write_trajectory(raw)

        link_calls = [c for c in calls if c[0] == "link"]
        linked_error_types = {c[2] for c in link_calls}
        assert "ImportError" in linked_error_types
        assert "TypeError" in linked_error_types

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
