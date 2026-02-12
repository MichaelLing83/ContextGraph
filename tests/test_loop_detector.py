"""Tests for loop detection based on error consistency."""

import pytest
from agent_memory.loop_detector import LoopSignature, LoopDetector, LoopInfo
from agent_memory.models import State


class TestLoopSignature:
    def test_signature_creation(self):
        sig = LoopSignature(
            action_type="edit",
            error_category="ImportError",
            error_keywords=["cannot import", "module"],
        )
        assert sig.action_type == "edit"
        assert sig.error_category == "ImportError"

    def test_signature_matches_same_error(self):
        sig1 = LoopSignature(
            action_type="edit",
            error_category="ImportError",
            error_keywords=["cannot import", "module"],
        )
        sig2 = LoopSignature(
            action_type="edit",
            error_category="ImportError",
            error_keywords=["cannot import", "name"],
        )
        assert sig1.matches(sig2)  # Same action + same error + overlapping keywords

    def test_signature_no_match_different_error(self):
        sig1 = LoopSignature(
            action_type="edit",
            error_category="ImportError",
            error_keywords=["cannot import"],
        )
        sig2 = LoopSignature(
            action_type="edit",
            error_category="TypeError",
            error_keywords=["cannot import"],
        )
        assert not sig1.matches(sig2)  # Different error type

    def test_signature_no_match_no_keyword_overlap(self):
        sig1 = LoopSignature(
            action_type="edit",
            error_category="ImportError",
            error_keywords=["cannot import"],
        )
        sig2 = LoopSignature(
            action_type="edit",
            error_category="ImportError",
            error_keywords=["syntax error"],
        )
        assert not sig1.matches(sig2)  # No keyword overlap


class TestLoopDetector:
    def test_detects_simple_loop(self):
        """Test detection of simple repeated errors."""
        detector = LoopDetector(min_repeat=3)

        # Create states with same error repeated
        states = []
        for i in range(5):
            state = State(
                tools=["bash", "edit"],
                repo_summary="Test repo",
                task_description="Fix bug",
                current_error="ImportError: cannot import name 'Foo' from module",
                phase="fixing",
            )
            state.last_action_type = "edit"  # Add action type
            states.append(state)

        loop_info = detector.detect(states)

        assert loop_info is not None
        assert loop_info.is_stuck is True
        assert loop_info.loop_length >= 3

    def test_no_loop_different_errors(self):
        """Test no loop detected when errors are different."""
        detector = LoopDetector(min_repeat=3)

        errors = [
            "ImportError: cannot import X",
            "TypeError: expected int got str",
            "ValueError: invalid value",
            "KeyError: 'missing_key'",
        ]

        states = []
        for i, error in enumerate(errors):
            state = State(
                tools=["bash"],
                repo_summary="Test",
                task_description="Test",
                current_error=error,
                phase="fixing",
            )
            state.last_action_type = "edit"
            states.append(state)

        loop_info = detector.detect(states)
        assert loop_info is None

    def test_is_same_predicament(self):
        """Test is_same_predicament method."""
        detector = LoopDetector()

        state1 = State(
            tools=["bash"],
            repo_summary="Test",
            task_description="Test",
            current_error="ImportError: cannot import 'Foo'",
            phase="fixing",
        )
        state1.last_action_type = "edit"

        state2 = State(
            tools=["bash"],
            repo_summary="Test",
            task_description="Test",
            current_error="ImportError: cannot import 'Bar'",
            phase="fixing",
        )
        state2.last_action_type = "edit"

        assert detector.is_same_predicament(state1, state2)

        # Different error type
        state3 = State(
            tools=["bash"],
            repo_summary="Test",
            task_description="Test",
            current_error="TypeError: invalid type",
            phase="fixing",
        )
        state3.last_action_type = "edit"

        assert not detector.is_same_predicament(state1, state3)

    def test_no_loop_short_history(self):
        """Test no loop with insufficient history."""
        detector = LoopDetector(min_repeat=3)

        states = []
        for i in range(2):
            state = State(
                tools=["bash"],
                repo_summary="Test",
                task_description="Test",
                current_error="ImportError: x",
                phase="fixing",
            )
            state.last_action_type = "edit"
            states.append(state)

        loop_info = detector.detect(states)
        assert loop_info is None
