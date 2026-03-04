"""Tests for pass^k (pass_hat) consistency metric."""

import pytest
from agent_memory.evaluation.metrics import (
    ProblemResult,
    EvaluationMetrics,
    calculate_metrics,
)


class TestPassAt:
    """Tests for the generic pass_at(k) method."""

    def test_pass_at_1(self):
        r = ProblemResult("p1", [True, False], [100, 100])
        assert r.pass_at(1) is True

    def test_pass_at_2_first_fails(self):
        r = ProblemResult("p1", [False, True, False], [100, 100, 100])
        assert r.pass_at(1) is False
        assert r.pass_at(2) is True

    def test_pass_at_k_all_fail(self):
        r = ProblemResult("p1", [False, False, False], [100, 100, 100])
        assert r.pass_at(3) is False

    def test_pass_at_k_larger_than_attempts(self):
        r = ProblemResult("p1", [False, True], [100, 100])
        # pass_at(5) checks first 5, but only 2 exist — still finds the True
        assert r.pass_at(5) is True

    def test_pass_at_empty(self):
        r = ProblemResult("p1", [], [])
        assert r.pass_at(1) is False

    def test_pass_at_k_zero_raises(self):
        r = ProblemResult("p1", [True], [100])
        with pytest.raises(ValueError, match="k must be >= 1"):
            r.pass_at(0)

    def test_pass_at_k_negative_raises(self):
        r = ProblemResult("p1", [True], [100])
        with pytest.raises(ValueError, match="k must be >= 1"):
            r.pass_at(-1)


class TestPassHatAt:
    """Tests for the pass^k (pass_hat_at) consistency metric."""

    def test_all_succeed(self):
        r = ProblemResult("p1", [True, True, True], [100, 100, 100])
        assert r.pass_hat_at(1) is True
        assert r.pass_hat_at(2) is True
        assert r.pass_hat_at(3) is True

    def test_first_fails(self):
        r = ProblemResult("p1", [False, True, True], [100, 100, 100])
        assert r.pass_hat_at(1) is False
        assert r.pass_hat_at(2) is False
        assert r.pass_hat_at(3) is False

    def test_second_fails(self):
        r = ProblemResult("p1", [True, False, True], [100, 100, 100])
        assert r.pass_hat_at(1) is True
        assert r.pass_hat_at(2) is False
        assert r.pass_hat_at(3) is False

    def test_k_larger_than_attempts(self):
        """pass^k requires exactly k attempts; fewer means False."""
        r = ProblemResult("p1", [True, True], [100, 100])
        assert r.pass_hat_at(2) is True
        assert r.pass_hat_at(3) is False  # only 2 attempts, need 3

    def test_empty_attempts(self):
        r = ProblemResult("p1", [], [])
        assert r.pass_hat_at(1) is False

    def test_single_failure(self):
        r = ProblemResult("p1", [False], [100])
        assert r.pass_hat_at(1) is False

    def test_single_success(self):
        r = ProblemResult("p1", [True], [100])
        assert r.pass_hat_at(1) is True

    def test_pass_hat_at_k_zero_raises(self):
        r = ProblemResult("p1", [True], [100])
        with pytest.raises(ValueError, match="k must be >= 1"):
            r.pass_hat_at(0)

    def test_pass_hat_at_k_negative_raises(self):
        r = ProblemResult("p1", [True], [100])
        with pytest.raises(ValueError, match="k must be >= 1"):
            r.pass_hat_at(-1)


class TestCalculateMetricsPassHat:
    """Tests for pass^k fields in calculate_metrics()."""

    def test_pass_hat_fields(self):
        results = [
            ProblemResult("p1", [True, True, True, True, True], [100] * 5),
            ProblemResult("p2", [True, False, True, True, True], [100] * 5),
            ProblemResult("p3", [False, False, False, False, False], [100] * 5),
            ProblemResult("p4", [True, True, True, False, True], [100] * 5),
        ]
        metrics = calculate_metrics(results)

        # pass^1: p1=T, p2=T, p3=F, p4=T → 3/4 = 0.75
        assert metrics.pass_hat_1 == 0.75
        # pass^3: p1=T, p2=F(2nd fails), p3=F, p4=T → 2/4 = 0.5
        assert metrics.pass_hat_3 == 0.5
        # pass^5: p1=T, p2=F, p3=F, p4=F(4th fails) → 1/4 = 0.25
        assert metrics.pass_hat_5 == 0.25

    def test_pass_hat_equals_pass_at_for_k1(self):
        """pass^1 == pass@1 by definition."""
        results = [
            ProblemResult("p1", [True, False], [100, 100]),
            ProblemResult("p2", [False, True], [100, 100]),
        ]
        metrics = calculate_metrics(results)
        assert metrics.pass_hat_1 == metrics.pass_at_1

    def test_pass_hat_empty_results(self):
        metrics = calculate_metrics([])
        assert metrics.pass_hat_1 == 0.0
        assert metrics.pass_hat_3 == 0.0
        assert metrics.pass_hat_5 == 0.0

    def test_pass_hat_fewer_than_k_attempts(self):
        """Problems with fewer than k attempts get pass^k = False."""
        results = [
            ProblemResult("p1", [True], [100]),  # only 1 attempt
        ]
        metrics = calculate_metrics(results)
        assert metrics.pass_hat_1 == 1.0
        assert metrics.pass_hat_3 == 0.0  # need 3 attempts, have 1
        assert metrics.pass_hat_5 == 0.0  # need 5 attempts, have 1
