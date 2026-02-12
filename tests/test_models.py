"""Tests for data models."""

import pytest
from agent_memory.models import Trajectory, Fragment, State, Methodology, ErrorPattern


class TestTrajectory:
    def test_trajectory_creation(self):
        traj = Trajectory(
            id="traj_001",
            instance_id="django__django-12345",
            repo="django/django",
            task_type="bug_fix",
            success=True,
            total_steps=25,
            summary="Fixed import error in models.py",
        )
        assert traj.id == "traj_001"
        assert traj.instance_id == "django__django-12345"
        assert traj.success is True

    def test_trajectory_to_dict(self):
        traj = Trajectory(
            id="traj_001",
            instance_id="test",
            repo="test/repo",
            task_type="bug_fix",
            success=True,
            total_steps=10,
            summary="Test summary",
        )
        d = traj.to_dict()
        assert d["id"] == "traj_001"
        assert "embedding" in d


class TestFragment:
    def test_fragment_creation(self):
        frag = Fragment(
            id="frag_001",
            step_range=(5, 12),
            fragment_type="error_recovery",
            description="Recovered from ImportError by fixing module path",
            action_sequence=["search", "open", "edit"],
            outcome="success",
        )
        assert frag.id == "frag_001"
        assert frag.step_range == (5, 12)
        assert frag.fragment_type == "error_recovery"

    def test_fragment_types_valid(self):
        valid_types = ["error_recovery", "exploration", "successful_fix", "failed_attempt", "loop"]
        for ft in valid_types:
            frag = Fragment(
                id=f"frag_{ft}",
                step_range=(0, 1),
                fragment_type=ft,
                description="test",
                action_sequence=[],
                outcome="test",
            )
            assert frag.fragment_type == ft


class TestState:
    def test_state_creation(self):
        state = State(
            tools=["bash", "search", "edit", "view"],
            repo_summary="Django web framework, Python 3.8+",
            task_description="Fix failing import in models.py",
            current_error="ImportError: cannot import name 'Model'",
            phase="fixing",
        )
        assert state.tools == ["bash", "search", "edit", "view"]
        assert state.phase == "fixing"
        assert "ImportError" in state.current_error

    def test_state_to_situation_string(self):
        state = State(
            tools=["bash", "edit"],
            repo_summary="Python web app",
            task_description="Fix bug",
            current_error="TypeError: NoneType",
            phase="locating",
        )
        situation = state.to_situation_string()
        assert "TypeError" in situation
        assert "locating" in situation

    def test_state_valid_phases(self):
        valid_phases = ["understanding", "locating", "fixing", "testing"]
        for phase in valid_phases:
            state = State(
                tools=["bash"],
                repo_summary="test",
                task_description="test",
                current_error="",
                phase=phase,
            )
            assert state.phase == phase

    def test_state_invalid_phase_raises(self):
        with pytest.raises(ValueError):
            State(
                tools=["bash"],
                repo_summary="test",
                task_description="test",
                current_error="",
                phase="invalid_phase",
            )


class TestMethodology:
    def test_methodology_creation(self):
        method = Methodology(
            id="meth_001",
            situation="When encountering ImportError in Python package",
            strategy="1. Check if module exists 2. Verify import path 3. Check __init__.py",
            confidence=0.85,
            success_count=17,
            failure_count=3,
        )
        assert method.id == "meth_001"
        assert method.confidence == 0.85
        assert method.success_rate == 17 / (17 + 3)

    def test_methodology_update_stats(self):
        method = Methodology(
            id="meth_002",
            situation="Test situation",
            strategy="Test strategy",
            confidence=0.5,
            success_count=5,
            failure_count=5,
        )
        method.record_outcome(success=True)
        assert method.success_count == 6
        assert method.failure_count == 5

        method.record_outcome(success=False)
        assert method.failure_count == 6

    def test_methodology_to_dict(self):
        method = Methodology(
            id="meth_003",
            situation="Test",
            strategy="Strategy",
            confidence=0.8,
        )
        d = method.to_dict()
        assert d["id"] == "meth_003"
        assert "source_fragment_ids" in d


class TestErrorPattern:
    def test_error_pattern_creation(self):
        pattern = ErrorPattern(
            id="err_001",
            error_type="ImportError",
            error_keywords=["cannot import", "module", "not found"],
            context="Python import statement",
            frequency=42,
        )
        assert pattern.id == "err_001"
        assert pattern.error_type == "ImportError"
        assert pattern.frequency == 42

    def test_error_pattern_matches(self):
        pattern = ErrorPattern(
            id="err_001",
            error_type="ImportError",
            error_keywords=["cannot import", "module"],
            context="Python",
            frequency=10,
        )
        # Should match same error type with overlapping keywords
        assert pattern.matches_error("ImportError", "cannot import name 'Foo' from module")
        # Should not match different error type
        assert not pattern.matches_error("TypeError", "cannot import name 'Foo'")
        # Should not match if no keyword overlap
        assert not pattern.matches_error("ImportError", "syntax error in file")

    def test_error_pattern_to_dict(self):
        pattern = ErrorPattern(
            id="err_002",
            error_type="ValueError",
            error_keywords=["invalid", "value"],
            context="Test",
            frequency=5,
        )
        d = pattern.to_dict()
        assert d["id"] == "err_002"
        assert d["error_type"] == "ValueError"
