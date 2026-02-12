"""Tests for results analyzer."""

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
