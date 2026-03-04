"""Tests for results analyzer."""

import pytest
from agent_memory.evaluation.metrics import ProblemResult, EvaluationMetrics, calculate_metrics
from agent_memory.evaluation.analyzer import (
    compare_results,
    ComparisonReport,
)


class TestCompareResults:
    def test_compare_basic(self):
        """Test basic comparison of control vs treatment."""
        control_results = [
            ProblemResult("p1", [False, True], [100, 100]),
            ProblemResult("p2", [False, False, False], [100, 100, 100]),
        ]
        treatment_results = [
            ProblemResult("p1", [True], [80]),
            ProblemResult("p2", [False, True], [100, 90]),
        ]

        control = calculate_metrics(control_results)
        treatment = calculate_metrics(treatment_results)

        report = compare_results(control, treatment)

        assert isinstance(report, ComparisonReport)
        assert report.pass_at_1_improvement > 0  # Treatment better
        assert report.token_reduction > 0  # Treatment uses fewer tokens

    def test_compare_efficiency(self):
        """Test efficiency comparison."""
        control_results = [
            ProblemResult("p1", [False, False, True], [100, 100, 100]),
        ]
        treatment_results = [
            ProblemResult("p1", [True], [100]),
        ]

        control = calculate_metrics(control_results)
        treatment = calculate_metrics(treatment_results)

        report = compare_results(control, treatment)

        # Treatment succeeds faster (1 attempt vs 3)
        assert report.efficiency_gain > 0

    def test_report_summary(self):
        """Test report summary generation."""
        control = EvaluationMetrics(
            pass_at_1=0.5,
            pass_at_3=0.7,
            pass_at_5=0.8,
            total_problems=10,
            avg_tokens_per_problem=1000,
            avg_attempts_to_success=2.5,
        )
        treatment = EvaluationMetrics(
            pass_at_1=0.6,
            pass_at_3=0.8,
            pass_at_5=0.9,
            total_problems=10,
            avg_tokens_per_problem=900,
            avg_attempts_to_success=1.8,
        )

        report = compare_results(control, treatment)
        summary = report.to_summary()

        assert "pass@1" in summary.lower()
        assert "improvement" in summary.lower() or "%" in summary

    def test_compare_pass_hat_improvements(self):
        """Test pass^k improvement values in ComparisonReport."""
        control_results = [
            ProblemResult("p1", [True, True, True, True, True], [100] * 5),
            ProblemResult("p2", [False, False, False, False, False], [100] * 5),
        ]
        treatment_results = [
            ProblemResult("p1", [True, True, True, True, True], [100] * 5),
            ProblemResult("p2", [True, True, True, True, True], [100] * 5),
        ]

        control = calculate_metrics(control_results)
        treatment = calculate_metrics(treatment_results)

        report = compare_results(control, treatment)

        # Control: pass^1 = 0.5, Treatment: pass^1 = 1.0 → improvement = 0.5
        assert report.pass_hat_1_improvement == pytest.approx(0.5)
        # Control: pass^3 = 0.5, Treatment: pass^3 = 1.0 → improvement = 0.5
        assert report.pass_hat_3_improvement == pytest.approx(0.5)
        # Control: pass^5 = 0.5, Treatment: pass^5 = 1.0 → improvement = 0.5
        assert report.pass_hat_5_improvement == pytest.approx(0.5)

    def test_report_summary_contains_pass_hat(self):
        """Test that to_summary() includes PASS^K section."""
        control = EvaluationMetrics(
            pass_at_1=0.5,
            pass_at_3=0.7,
            pass_at_5=0.8,
            total_problems=10,
            avg_tokens_per_problem=1000,
            avg_attempts_to_success=2.5,
            pass_hat_1=0.4,
            pass_hat_3=0.3,
            pass_hat_5=0.2,
        )
        treatment = EvaluationMetrics(
            pass_at_1=0.6,
            pass_at_3=0.8,
            pass_at_5=0.9,
            total_problems=10,
            avg_tokens_per_problem=900,
            avg_attempts_to_success=1.8,
            pass_hat_1=0.5,
            pass_hat_3=0.4,
            pass_hat_5=0.3,
        )

        report = compare_results(control, treatment)
        summary = report.to_summary()

        assert "PASS^K" in summary
        assert "pass^1" in summary
        assert "pass^3" in summary
        assert "pass^5" in summary

    def test_report_summary_negative_changes(self):
        """Test summary wording for negative token/efficiency changes."""
        control = EvaluationMetrics(
            pass_at_1=0.5,
            pass_at_3=0.7,
            pass_at_5=0.8,
            total_problems=10,
            avg_tokens_per_problem=1000,
            avg_attempts_to_success=2.0,
        )
        treatment = EvaluationMetrics(
            pass_at_1=0.5,
            pass_at_3=0.7,
            pass_at_5=0.8,
            total_problems=10,
            avg_tokens_per_problem=1100,
            avg_attempts_to_success=2.5,
        )

        report = compare_results(control, treatment)
        summary = report.to_summary()

        assert "increase" in summary.lower()
        assert "degradation" in summary.lower()
