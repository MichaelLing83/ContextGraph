"""
Statistical Analysis for Agent Memory A/B Testing.

Provides statistical tests, confidence intervals, and significance
testing for comparing control vs treatment groups.
"""

import json
import math
from dataclasses import dataclass, asdict
from typing import List, Tuple
import random
import statistics

from .config import get_config
from .metrics import TrajectoryMetrics


@dataclass
class StatisticalResult:
    """Result of a statistical test."""
    test_name: str
    statistic: float
    p_value: float
    significant: bool  # At Bonferroni-adjusted alpha
    effect_size: float
    confidence_interval: Tuple[float, float]
    interpretation: str


@dataclass
class GroupStatistics:
    """Descriptive statistics for a group."""
    n: int
    mean: float
    std: float
    median: float
    ci_lower: float
    ci_upper: float


@dataclass
class AnalysisReport:
    """Complete analysis report."""
    # Descriptive statistics
    control_pass_rate: float
    treatment_pass_rate: float
    control_tokens: GroupStatistics
    treatment_tokens: GroupStatistics
    control_loops: GroupStatistics
    treatment_loops: GroupStatistics

    # Statistical tests
    pass_rate_test: StatisticalResult
    token_test: StatisticalResult
    loop_test: StatisticalResult

    # Overall
    bonferroni_alpha: float
    any_significant: bool
    recommendations: List[str]


# ============ Statistical Functions ============

def chi_square_test(
    success_a: int, total_a: int,
    success_b: int, total_b: int
) -> Tuple[float, float]:
    """
    Chi-square test for comparing two proportions.

    Returns (chi_square_statistic, p_value)
    """
    # Create contingency table
    # [[success_a, fail_a], [success_b, fail_b]]
    fail_a = total_a - success_a
    fail_b = total_b - success_b

    # Calculate expected values
    total = total_a + total_b
    total_success = success_a + success_b
    total_fail = fail_a + fail_b

    exp_success_a = (total_a * total_success) / total
    exp_fail_a = (total_a * total_fail) / total
    exp_success_b = (total_b * total_success) / total
    exp_fail_b = (total_b * total_fail) / total

    # Chi-square statistic
    chi_sq = 0
    for observed, expected in [
        (success_a, exp_success_a),
        (fail_a, exp_fail_a),
        (success_b, exp_success_b),
        (fail_b, exp_fail_b)
    ]:
        if expected > 0:
            chi_sq += (observed - expected) ** 2 / expected

    # P-value from chi-square distribution (df=1)
    # Using approximation for simplicity
    p_value = chi_square_cdf(chi_sq, 1)

    return chi_sq, 1 - p_value


def chi_square_cdf(x: float, df: int) -> float:
    """
    Approximate chi-square CDF using normal approximation.

    For df=1, this is reasonably accurate.
    """
    if x <= 0:
        return 0

    # Wilson-Hilferty transformation
    z = (pow(x / df, 1/3) - (1 - 2 / (9 * df))) / math.sqrt(2 / (9 * df))

    # Standard normal CDF approximation
    return normal_cdf(z)


def normal_cdf(z: float) -> float:
    """Approximate standard normal CDF."""
    # Abramowitz and Stegun approximation
    a1, a2, a3, a4, a5 = (
        0.254829592, -0.284496736, 1.421413741,
        -1.453152027, 1.061405429
    )
    p = 0.3275911

    sign = 1 if z >= 0 else -1
    z = abs(z) / math.sqrt(2)

    t = 1.0 / (1.0 + p * z)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-z * z)

    return 0.5 * (1.0 + sign * y)


def mann_whitney_u(
    group_a: List[float],
    group_b: List[float]
) -> Tuple[float, float]:
    """
    Mann-Whitney U test for comparing two distributions.

    Returns (U_statistic, p_value)
    """
    n_a = len(group_a)
    n_b = len(group_b)

    if n_a == 0 or n_b == 0:
        return 0, 1.0

    # Combine and rank
    combined = [(x, 'a') for x in group_a] + [(x, 'b') for x in group_b]
    combined.sort(key=lambda t: t[0])

    # Assign ranks (handling ties)
    ranks = {}
    i = 0
    while i < len(combined):
        # Find all items with same value
        j = i
        while j < len(combined) and combined[j][0] == combined[i][0]:
            j += 1

        # Average rank for tied values
        avg_rank = (i + j + 1) / 2  # 1-indexed

        for k in range(i, j):
            if combined[k][1] not in ranks:
                ranks[combined[k][1]] = []
            ranks[combined[k][1]].append(avg_rank)

        i = j

    # Sum of ranks for group A
    rank_sum_a = sum(ranks.get('a', [0]))

    # U statistic
    U_a = rank_sum_a - (n_a * (n_a + 1)) / 2
    U_b = n_a * n_b - U_a

    U = min(U_a, U_b)

    # Normal approximation for p-value (for n > 20)
    if n_a + n_b >= 20:
        mean_U = n_a * n_b / 2
        std_U = math.sqrt(n_a * n_b * (n_a + n_b + 1) / 12)

        if std_U > 0:
            z = (U - mean_U) / std_U
            p_value = 2 * (1 - normal_cdf(abs(z)))  # Two-tailed
        else:
            p_value = 1.0
    else:
        # For small samples, approximate
        p_value = 0.5  # Not reliable for small samples

    return U, p_value


def bootstrap_ci(
    data: List[float],
    statistic_fn,
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    seed: int = 42
) -> Tuple[float, float]:
    """
    Bootstrap confidence interval for a statistic.

    Args:
        data: Sample data
        statistic_fn: Function to compute statistic (e.g., statistics.mean)
        n_bootstrap: Number of bootstrap samples
        confidence: Confidence level (e.g., 0.95 for 95%)
        seed: Random seed

    Returns:
        (lower_bound, upper_bound)
    """
    if len(data) == 0:
        return (0, 0)

    rng = random.Random(seed)
    bootstrap_stats = []

    for _ in range(n_bootstrap):
        sample = rng.choices(data, k=len(data))
        try:
            stat = statistic_fn(sample)
            bootstrap_stats.append(stat)
        except Exception:
            continue

    if not bootstrap_stats:
        return (0, 0)

    bootstrap_stats.sort()
    n_valid = len(bootstrap_stats)
    alpha = 1 - confidence
    lower_idx = max(0, min(n_valid - 1, int(n_valid * alpha / 2)))
    upper_idx = max(0, min(n_valid - 1, int(n_valid * (1 - alpha / 2)) - 1))

    return (
        bootstrap_stats[lower_idx],
        bootstrap_stats[upper_idx]
    )


def bootstrap_diff_ci(
    group_a: List[float],
    group_b: List[float],
    n_bootstrap: int = 10000,
    confidence: float = 0.95,
    seed: int = 42
) -> Tuple[float, float]:
    """
    Bootstrap confidence interval for difference between two group means.

    Resamples each group independently to preserve group membership.

    Args:
        group_a: First group data (control)
        group_b: Second group data (treatment)
        n_bootstrap: Number of bootstrap samples
        confidence: Confidence level
        seed: Random seed

    Returns:
        (lower_bound, upper_bound) for mean(group_b) - mean(group_a)
    """
    if not group_a or not group_b:
        return (0, 0)

    rng = random.Random(seed)
    diff_samples = []

    for _ in range(n_bootstrap):
        sample_a = rng.choices(group_a, k=len(group_a))
        sample_b = rng.choices(group_b, k=len(group_b))
        diff = statistics.mean(sample_b) - statistics.mean(sample_a)
        diff_samples.append(diff)

    diff_samples.sort()
    alpha = 1 - confidence
    lower_idx = max(0, int(n_bootstrap * alpha / 2))
    upper_idx = min(n_bootstrap - 1, int(n_bootstrap * (1 - alpha / 2)) - 1)

    return (diff_samples[lower_idx], diff_samples[upper_idx])


def compute_effect_size(
    mean_a: float, mean_b: float,
    std_a: float, std_b: float,
    n_a: int, n_b: int
) -> float:
    """
    Compute Cohen's d effect size.

    Returns the standardized difference between means.
    """
    # Pooled standard deviation
    if n_a + n_b <= 2:
        return 0

    pooled_var = (
        ((n_a - 1) * std_a ** 2 + (n_b - 1) * std_b ** 2) /
        (n_a + n_b - 2)
    )
    pooled_std = math.sqrt(pooled_var) if pooled_var > 0 else 1

    return (mean_b - mean_a) / pooled_std if pooled_std > 0 else 0


def interpret_effect_size(d: float) -> str:
    """Interpret Cohen's d effect size."""
    d_abs = abs(d)
    if d_abs < 0.2:
        return "negligible"
    elif d_abs < 0.5:
        return "small"
    elif d_abs < 0.8:
        return "medium"
    else:
        return "large"


# ============ Analysis Functions ============

def analyze_pass_rates(
    control_metrics: List[TrajectoryMetrics],
    treatment_metrics: List[TrajectoryMetrics],
    alpha: float = 0.05
) -> StatisticalResult:
    """Analyze difference in pass rates between groups."""
    n_control = len(control_metrics)
    n_treatment = len(treatment_metrics)

    success_control = sum(1 for m in control_metrics if m.success)
    success_treatment = sum(1 for m in treatment_metrics if m.success)

    rate_control = success_control / n_control if n_control > 0 else 0
    rate_treatment = success_treatment / n_treatment if n_treatment > 0 else 0

    # Chi-square test
    chi_sq, p_value = chi_square_test(
        success_control, n_control,
        success_treatment, n_treatment
    )

    # Effect size (difference in proportions)
    effect_size = rate_treatment - rate_control

    # Bootstrap CI for difference (resample groups independently)
    control_success = [1 if m.success else 0 for m in control_metrics]
    treatment_success = [1 if m.success else 0 for m in treatment_metrics]

    ci_lower, ci_upper = bootstrap_diff_ci(
        control_success, treatment_success,
        n_bootstrap=10000, confidence=0.95, seed=42
    )

    return StatisticalResult(
        test_name="Chi-square test for pass rates",
        statistic=chi_sq,
        p_value=p_value,
        significant=p_value < alpha,
        effect_size=effect_size,
        confidence_interval=(ci_lower, ci_upper),
        interpretation=f"Pass rate {'increased' if effect_size > 0 else 'decreased'} "
                       f"by {abs(effect_size):.1%}"
    )


def analyze_tokens(
    control_metrics: List[TrajectoryMetrics],
    treatment_metrics: List[TrajectoryMetrics],
    alpha: float = 0.05
) -> StatisticalResult:
    """Analyze difference in token usage between groups."""
    control_tokens = [m.total_tokens_estimate for m in control_metrics]
    treatment_tokens = [m.total_tokens_estimate for m in treatment_metrics]

    if not control_tokens or not treatment_tokens:
        return StatisticalResult(
            test_name="Mann-Whitney U test for tokens",
            statistic=0, p_value=1, significant=False,
            effect_size=0, confidence_interval=(0, 0),
            interpretation="Insufficient data"
        )

    # Mann-Whitney U test
    U, p_value = mann_whitney_u(control_tokens, treatment_tokens)

    # Effect size
    mean_control = statistics.mean(control_tokens)
    mean_treatment = statistics.mean(treatment_tokens)
    std_control = statistics.stdev(control_tokens) if len(control_tokens) > 1 else 0
    std_treatment = statistics.stdev(treatment_tokens) if len(treatment_tokens) > 1 else 0

    effect_size = compute_effect_size(
        mean_control, mean_treatment,
        std_control, std_treatment,
        len(control_tokens), len(treatment_tokens)
    )

    # Bootstrap CI for difference in means (resample groups independently)
    ci_lower, ci_upper = bootstrap_diff_ci(
        control_tokens, treatment_tokens,
        n_bootstrap=10000, confidence=0.95, seed=42
    )

    reduction_pct = ((mean_control - mean_treatment) / mean_control * 100
                     if mean_control > 0 else 0)

    return StatisticalResult(
        test_name="Mann-Whitney U test for token usage",
        statistic=U,
        p_value=p_value,
        significant=p_value < alpha,
        effect_size=effect_size,
        confidence_interval=(ci_lower, ci_upper),
        interpretation=f"Token usage {'reduced' if reduction_pct > 0 else 'increased'} "
                       f"by {abs(reduction_pct):.1f}% ({interpret_effect_size(effect_size)} effect)"
    )


def analyze_loops(
    control_metrics: List[TrajectoryMetrics],
    treatment_metrics: List[TrajectoryMetrics],
    alpha: float = 0.05
) -> StatisticalResult:
    """Analyze difference in loop rates between groups."""
    control_loops = [1 if m.loops_detected > 0 else 0 for m in control_metrics]
    treatment_loops = [1 if m.loops_detected > 0 else 0 for m in treatment_metrics]

    n_control = len(control_loops)
    n_treatment = len(treatment_loops)

    loops_control = sum(control_loops)
    loops_treatment = sum(treatment_loops)

    rate_control = loops_control / n_control if n_control > 0 else 0
    rate_treatment = loops_treatment / n_treatment if n_treatment > 0 else 0

    # Chi-square test
    chi_sq, p_value = chi_square_test(
        loops_control, n_control,
        loops_treatment, n_treatment
    )

    effect_size = rate_treatment - rate_control
    reduction_pct = ((rate_control - rate_treatment) / rate_control * 100
                     if rate_control > 0 else 0)

    return StatisticalResult(
        test_name="Chi-square test for loop rates",
        statistic=chi_sq,
        p_value=p_value,
        significant=p_value < alpha,
        effect_size=effect_size,
        confidence_interval=(0, 0),  # Not computed for simplicity
        interpretation=f"Loop rate {'reduced' if reduction_pct > 0 else 'increased'} "
                       f"by {abs(reduction_pct):.1f}%"
    )


def compute_group_statistics(
    values: List[float],
    confidence: float = 0.95
) -> GroupStatistics:
    """Compute descriptive statistics for a group."""
    if not values:
        return GroupStatistics(0, 0, 0, 0, 0, 0)

    n = len(values)
    mean = statistics.mean(values)
    std = statistics.stdev(values) if n > 1 else 0
    median = statistics.median(values)

    ci_lower, ci_upper = bootstrap_ci(values, statistics.mean)

    return GroupStatistics(
        n=n,
        mean=mean,
        std=std,
        median=median,
        ci_lower=ci_lower,
        ci_upper=ci_upper
    )


def run_full_analysis(
    control_metrics: List[TrajectoryMetrics],
    treatment_metrics: List[TrajectoryMetrics],
    n_comparisons: int = 3  # pass rate, tokens, loops
) -> AnalysisReport:
    """
    Run complete statistical analysis with Bonferroni correction.
    """
    # Bonferroni-adjusted alpha
    base_alpha = 0.05
    bonferroni_alpha = base_alpha / n_comparisons

    # Descriptive statistics
    control_pass = sum(1 for m in control_metrics if m.success) / len(control_metrics) if control_metrics else 0
    treatment_pass = sum(1 for m in treatment_metrics if m.success) / len(treatment_metrics) if treatment_metrics else 0

    control_token_values = [m.total_tokens_estimate for m in control_metrics]
    treatment_token_values = [m.total_tokens_estimate for m in treatment_metrics]

    control_loop_values = [m.loops_detected for m in control_metrics]
    treatment_loop_values = [m.loops_detected for m in treatment_metrics]

    # Statistical tests
    pass_rate_test = analyze_pass_rates(control_metrics, treatment_metrics, bonferroni_alpha)
    token_test = analyze_tokens(control_metrics, treatment_metrics, bonferroni_alpha)
    loop_test = analyze_loops(control_metrics, treatment_metrics, bonferroni_alpha)

    # Check if any significant
    any_significant = (
        pass_rate_test.significant or
        token_test.significant or
        loop_test.significant
    )

    # Generate recommendations
    recommendations = []
    if pass_rate_test.significant and pass_rate_test.effect_size > 0:
        recommendations.append("Agent Memory significantly improves pass rate")
    if token_test.significant and token_test.effect_size < 0:
        recommendations.append("Agent Memory significantly reduces token usage")
    if loop_test.significant and loop_test.effect_size < 0:
        recommendations.append("Agent Memory significantly reduces loop incidence")

    if not any_significant:
        recommendations.append("No statistically significant differences detected")
        recommendations.append("Consider increasing sample size for more power")

    return AnalysisReport(
        control_pass_rate=control_pass,
        treatment_pass_rate=treatment_pass,
        control_tokens=compute_group_statistics(control_token_values),
        treatment_tokens=compute_group_statistics(treatment_token_values),
        control_loops=compute_group_statistics(control_loop_values),
        treatment_loops=compute_group_statistics(treatment_loop_values),
        pass_rate_test=pass_rate_test,
        token_test=token_test,
        loop_test=loop_test,
        bonferroni_alpha=bonferroni_alpha,
        any_significant=any_significant,
        recommendations=recommendations
    )


def print_analysis_report(report: AnalysisReport):
    """Print formatted analysis report."""
    print("\n" + "=" * 70)
    print("STATISTICAL ANALYSIS REPORT")
    print("=" * 70)

    print("\n--- Pass Rate Analysis ---")
    print(f"Control: {report.control_pass_rate:.1%}")
    print(f"Treatment: {report.treatment_pass_rate:.1%}")
    print(f"Difference: {report.treatment_pass_rate - report.control_pass_rate:+.1%}")
    print(f"\n{report.pass_rate_test.test_name}")
    print(f"  Statistic: {report.pass_rate_test.statistic:.4f}")
    print(f"  P-value: {report.pass_rate_test.p_value:.4f}")
    print(f"  Significant (α={report.bonferroni_alpha:.4f}): {report.pass_rate_test.significant}")
    print(f"  {report.pass_rate_test.interpretation}")

    print("\n--- Token Usage Analysis ---")
    print(f"Control: mean={report.control_tokens.mean:.0f}, std={report.control_tokens.std:.0f}")
    print(f"Treatment: mean={report.treatment_tokens.mean:.0f}, std={report.treatment_tokens.std:.0f}")
    print(f"\n{report.token_test.test_name}")
    print(f"  Statistic: {report.token_test.statistic:.4f}")
    print(f"  P-value: {report.token_test.p_value:.4f}")
    print(f"  Significant: {report.token_test.significant}")
    print(f"  Effect size (Cohen's d): {report.token_test.effect_size:.3f}")
    print(f"  {report.token_test.interpretation}")

    print("\n--- Loop Rate Analysis ---")
    print(f"Control: mean={report.control_loops.mean:.2f}")
    print(f"Treatment: mean={report.treatment_loops.mean:.2f}")
    print(f"\n{report.loop_test.test_name}")
    print(f"  Statistic: {report.loop_test.statistic:.4f}")
    print(f"  P-value: {report.loop_test.p_value:.4f}")
    print(f"  Significant: {report.loop_test.significant}")
    print(f"  {report.loop_test.interpretation}")

    print("\n--- Overall Conclusions ---")
    print(f"Bonferroni-adjusted α: {report.bonferroni_alpha:.4f}")
    print(f"Any significant differences: {report.any_significant}")
    print("\nRecommendations:")
    for rec in report.recommendations:
        print(f"  • {rec}")


def main():
    """Run analysis on simulation results."""
    config = get_config()

    # Load simulation results
    results_file = config.paths.results_dir / "simulation_trajectories.json"
    if not results_file.exists():
        print("Error: No simulation results found. Run run_simulation.py first.")
        return

    with open(results_file, 'r') as f:
        trajectories = json.load(f)

    print(f"Loaded {len(trajectories)} trajectory results")

    # Convert to TrajectoryMetrics
    metrics = []
    for t in trajectories:
        m = TrajectoryMetrics(
            instance_id=t["instance_id"],
            success=t["success"],
            total_steps=t["total_steps"],
            total_tokens_estimate=t["total_tokens_estimate"],
            loops_detected=t["loops_detected"],
            loop_steps_wasted=t.get("loop_steps_wasted", 0),
            total_interventions=t.get("total_interventions", 0),
            estimated_steps_saved=t.get("estimated_steps_saved", 0),
            estimated_success_with_memory=t.get("estimated_success_with_memory"),
        )
        metrics.append(m)

    # For simulation analysis, we compare:
    # - Control: Actual outcomes (no memory)
    # - Treatment: Estimated outcomes (with memory)

    # Create "control" metrics (actual outcomes)
    control_metrics = metrics.copy()

    # Create "treatment" metrics (estimated with memory)
    # Build lookup for trajectories with loop-specific interventions
    has_loop_intervention = {}
    for t in trajectories:
        interventions = t.get("interventions", [])
        has_loop_intervention[t["instance_id"]] = any(
            i.get("intervention_type") == "loop_warning" for i in interventions
        )

    treatment_metrics = []
    for m in metrics:
        # Only zero loops if a loop-specific intervention occurred
        had_loop_intervention = has_loop_intervention.get(m.instance_id, False)
        t = TrajectoryMetrics(
            instance_id=m.instance_id,
            success=m.estimated_success_with_memory if m.estimated_success_with_memory is not None else m.success,
            total_steps=max(1, m.total_steps - m.estimated_steps_saved),
            total_tokens_estimate=max(500, m.total_tokens_estimate - m.estimated_steps_saved * 500),
            loops_detected=0 if had_loop_intervention else m.loops_detected,
            loop_steps_wasted=0 if had_loop_intervention else m.loop_steps_wasted,
            total_interventions=m.total_interventions,
        )
        treatment_metrics.append(t)

    # Run analysis
    report = run_full_analysis(control_metrics, treatment_metrics)

    # Print report
    print_analysis_report(report)

    # Save report
    config.paths.results_dir.mkdir(parents=True, exist_ok=True)
    output_file = config.paths.results_dir / "statistical_analysis.json"
    with open(output_file, 'w') as f:
        json.dump({
            "control_pass_rate": report.control_pass_rate,
            "treatment_pass_rate": report.treatment_pass_rate,
            "control_tokens": asdict(report.control_tokens),
            "treatment_tokens": asdict(report.treatment_tokens),
            "pass_rate_test": asdict(report.pass_rate_test),
            "token_test": asdict(report.token_test),
            "loop_test": asdict(report.loop_test),
            "bonferroni_alpha": report.bonferroni_alpha,
            "any_significant": report.any_significant,
            "recommendations": report.recommendations,
        }, f, indent=2)

    print(f"\nAnalysis saved to: {output_file}")


if __name__ == "__main__":
    main()
