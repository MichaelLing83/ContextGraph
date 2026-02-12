"""Analyze and compare evaluation results."""

from dataclasses import dataclass

from agent_memory.evaluation.metrics import EvaluationMetrics


@dataclass
class ComparisonReport:
    """Comparison between control and treatment groups."""

    # Improvements (positive = treatment better)
    pass_at_1_improvement: float  # Percentage point improvement
    pass_at_3_improvement: float
    pass_at_5_improvement: float

    # Token efficiency (positive = treatment uses fewer)
    token_reduction: float  # Percentage reduction

    # Efficiency (positive = treatment succeeds faster)
    efficiency_gain: float  # Reduction in avg attempts

    # Failure ratio improvement (positive = treatment has fewer failures)
    failure_ratio_reduction: float

    # Raw metrics
    control: EvaluationMetrics
    treatment: EvaluationMetrics

    def to_summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            "=" * 50,
            "EVALUATION COMPARISON REPORT",
            "=" * 50,
            "",
            f"Problems evaluated: {self.control.total_problems}",
            "",
            "PASS@K RATES:",
            f"  pass@1: {self.control.pass_at_1:.1%} → {self.treatment.pass_at_1:.1%} ({self._fmt_change(self.pass_at_1_improvement)})",
            f"  pass@3: {self.control.pass_at_3:.1%} → {self.treatment.pass_at_3:.1%} ({self._fmt_change(self.pass_at_3_improvement)})",
            f"  pass@5: {self.control.pass_at_5:.1%} → {self.treatment.pass_at_5:.1%} ({self._fmt_change(self.pass_at_5_improvement)})",
            "",
            "TOKEN CONSUMPTION:",
            f"  Control avg: {self.control.avg_tokens_per_problem:.0f} tokens",
            f"  Treatment avg: {self.treatment.avg_tokens_per_problem:.0f} tokens",
            f"  {self._fmt_token_change()}",
            "",
            "EFFICIENCY (attempts to first success):",
        ]

        if (
            self.control.avg_attempts_to_success is not None
            and self.treatment.avg_attempts_to_success is not None
        ):
            lines.extend([
                f"  Control avg: {self.control.avg_attempts_to_success:.1f} attempts",
                f"  Treatment avg: {self.treatment.avg_attempts_to_success:.1f} attempts",
                f"  {self._fmt_efficiency_change()}",
            ])
        else:
            lines.append("  (insufficient data)")

        lines.extend([
            "",
            "FAILURE RATIO (failed attempts / total attempts):",
            f"  Control: {self.control.failure_ratio:.1%}",
            f"  Treatment: {self.treatment.failure_ratio:.1%}",
            f"  {self._fmt_failure_change()}",
            "",
            "=" * 50,
        ])

        return "\n".join(lines)

    def _fmt_change(self, change: float) -> str:
        """Format change as +X.X% or -X.X%."""
        if change >= 0:
            return f"+{change:.1%}"
        else:
            return f"{change:.1%}"

    def _fmt_token_change(self) -> str:
        """Format token change with reduction/increase wording."""
        if self.token_reduction > 0:
            return f"Reduction: {self.token_reduction:.1%}"
        if self.token_reduction < 0:
            return f"Increase: {abs(self.token_reduction):.1%}"
        return "No change: 0.0%"

    def _fmt_efficiency_change(self) -> str:
        """Format attempt change with improvement/degradation wording."""
        if self.efficiency_gain > 0:
            return f"Improvement: {self.efficiency_gain:.1f} fewer attempts"
        if self.efficiency_gain < 0:
            return f"Degradation: {abs(self.efficiency_gain):.1f} more attempts"
        return "No change in attempts"

    def _fmt_failure_change(self) -> str:
        """Format failure ratio change."""
        if self.failure_ratio_reduction > 0:
            return f"Reduction: {self.failure_ratio_reduction:.1%}"
        if self.failure_ratio_reduction < 0:
            return f"Increase: {abs(self.failure_ratio_reduction):.1%}"
        return "No change: 0.0%"


def compare_results(
    control: EvaluationMetrics,
    treatment: EvaluationMetrics,
) -> ComparisonReport:
    """
    Compare control and treatment evaluation results.

    Args:
        control: Metrics from control group (no memory)
        treatment: Metrics from treatment group (with memory)

    Returns:
        ComparisonReport with improvement metrics
    """
    # Pass@k improvements (percentage points)
    pass_1_imp = treatment.pass_at_1 - control.pass_at_1
    pass_3_imp = treatment.pass_at_3 - control.pass_at_3
    pass_5_imp = treatment.pass_at_5 - control.pass_at_5

    # Token reduction (percentage)
    if control.avg_tokens_per_problem > 0:
        token_reduction = (control.avg_tokens_per_problem - treatment.avg_tokens_per_problem) / control.avg_tokens_per_problem
    else:
        token_reduction = 0.0

    # Efficiency gain (fewer attempts)
    if (
        control.avg_attempts_to_success is not None
        and treatment.avg_attempts_to_success is not None
    ):
        efficiency_gain = control.avg_attempts_to_success - treatment.avg_attempts_to_success
    else:
        efficiency_gain = 0.0

    # Failure ratio reduction (positive = treatment has fewer failures)
    failure_ratio_reduction = control.failure_ratio - treatment.failure_ratio

    return ComparisonReport(
        pass_at_1_improvement=pass_1_imp,
        pass_at_3_improvement=pass_3_imp,
        pass_at_5_improvement=pass_5_imp,
        token_reduction=token_reduction,
        efficiency_gain=efficiency_gain,
        failure_ratio_reduction=failure_ratio_reduction,
        control=control,
        treatment=treatment,
    )
