"""Tests for evaluation metrics."""

import pytest
from agent_memory.evaluation.metrics import (
    ProblemResult,
    calculate_metrics,
)


class TestProblemResult:
    def test_pass_at_1_success(self):
        """Test pass@1 when first attempt succeeds."""
        result = ProblemResult(
            problem_id="test-1",
            attempts=[True, False, False, False, False],
            tokens=[100, 0, 0, 0, 0],
        )
        assert result.pass_at_1 is True
        assert result.first_success_attempt == 1

    def test_pass_at_1_failure(self):
        """Test pass@1 when first attempt fails."""
        result = ProblemResult(
            problem_id="test-2",
            attempts=[False, True, False, False, False],
            tokens=[100, 150, 0, 0, 0],
        )
        assert result.pass_at_1 is False
        assert result.pass_at_3 is True
        assert result.first_success_attempt == 2

    def test_pass_at_k_all_fail(self):
        """Test pass@k when all attempts fail."""
        result = ProblemResult(
            problem_id="test-3",
            attempts=[False, False, False, False, False],
            tokens=[100, 100, 100, 100, 100],
        )
        assert result.pass_at_1 is False
        assert result.pass_at_3 is False
        assert result.pass_at_5 is False
        assert result.first_success_attempt is None

    def test_total_tokens(self):
        """Test total token calculation."""
        result = ProblemResult(
            problem_id="test-4",
            attempts=[True],
            tokens=[500],
        )
        assert result.total_tokens == 500

    def test_mismatched_attempts_and_tokens_raises(self):
        """Test validation for attempts/tokens length mismatch."""
        with pytest.raises(ValueError):
            ProblemResult(
                problem_id="test-invalid",
                attempts=[True, False],
                tokens=[100],
            )


class TestCalculateMetrics:
    def test_calculate_metrics(self):
        """Test metric calculation from multiple results."""
        results = [
            ProblemResult("p1", [True, False, False, False, False], [100, 0, 0, 0, 0]),
            ProblemResult("p2", [False, True, False, False, False], [100, 100, 0, 0, 0]),
            ProblemResult("p3", [False, False, False, False, False], [100, 100, 100, 100, 100]),
            ProblemResult("p4", [False, False, False, True, False], [100, 100, 100, 100, 0]),
        ]

        metrics = calculate_metrics(results)

        assert metrics.pass_at_1 == 0.25  # 1/4
        assert metrics.pass_at_3 == 0.5   # 2/4
        assert metrics.pass_at_5 == 0.75  # 3/4
        assert metrics.total_problems == 4
        assert metrics.avg_tokens_per_problem == 300  # (100+200+500+400)/4 = 1200/4

    def test_calculate_efficiency(self):
        """Test efficiency metric (avg attempts to first success)."""
        results = [
            ProblemResult("p1", [True], [100]),           # 1 attempt
            ProblemResult("p2", [False, False, True], [100, 100, 100]),  # 3 attempts
            ProblemResult("p3", [False, False, False, False, False], [100]*5),  # No success
        ]

        metrics = calculate_metrics(results)

        # Only count successful ones: (1 + 3) / 2 = 2.0
        assert metrics.avg_attempts_to_success == 2.0
