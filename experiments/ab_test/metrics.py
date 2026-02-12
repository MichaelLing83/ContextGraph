"""
Metrics collection for Agent Memory A/B Testing Experiment.

Defines metrics classes and aggregation functions for tracking
experiment outcomes.
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from enum import Enum
import statistics


class InterventionType(str, Enum):
    """Types of memory interventions."""
    LOOP_WARNING = "loop_warning"
    METHODOLOGY_MATCH = "methodology_match"
    ERROR_PATTERN = "error_pattern"
    NONE = "none"


@dataclass
class InterventionPoint:
    """Record of a single intervention point."""
    step_number: int
    intervention_type: InterventionType
    confidence: float
    details: str
    potential_steps_saved: int = 0


@dataclass
class TrajectoryMetrics:
    """Metrics for a single trajectory simulation."""
    instance_id: str
    success: bool
    total_steps: int
    total_tokens_estimate: int  # Rough estimate based on step count

    # Intervention tracking
    interventions: List[InterventionPoint] = field(default_factory=list)
    first_intervention_step: Optional[int] = None
    total_interventions: int = 0

    # Loop detection
    loops_detected: int = 0
    loop_steps_wasted: int = 0

    # Counterfactual estimation
    estimated_steps_saved: int = 0
    estimated_success_with_memory: Optional[bool] = None

    # Timing
    simulation_time_ms: float = 0.0

    def to_dict(self) -> Dict:
        result = asdict(self)
        result["interventions"] = [
            {**asdict(i), "intervention_type": i.intervention_type.value}
            for i in self.interventions
        ]
        return result


@dataclass
class GroupMetrics:
    """Aggregated metrics for a group (control or treatment)."""
    group_name: str
    count: int = 0

    # Success metrics
    n_success: int = 0
    success_rate: float = 0.0

    # Step metrics
    avg_steps: float = 0.0
    median_steps: float = 0.0
    total_steps: int = 0

    # Token metrics (estimated)
    avg_tokens: float = 0.0
    total_tokens: int = 0

    # Loop metrics
    n_with_loops: int = 0
    loop_rate: float = 0.0
    avg_loop_steps_wasted: float = 0.0

    # Intervention metrics (treatment only)
    n_with_interventions: int = 0
    intervention_rate: float = 0.0
    avg_interventions_per_trajectory: float = 0.0
    avg_first_intervention_step: float = 0.0

    # Estimated improvements (treatment only)
    avg_steps_saved: float = 0.0
    estimated_improved_success_rate: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ExperimentMetrics:
    """Complete metrics for the A/B experiment."""
    control: GroupMetrics = field(default_factory=lambda: GroupMetrics("control"))
    treatment: GroupMetrics = field(default_factory=lambda: GroupMetrics("treatment"))

    # Comparison metrics
    success_rate_delta: float = 0.0
    token_reduction_pct: float = 0.0
    loop_reduction_pct: float = 0.0

    # Per-trajectory metrics
    trajectory_metrics: List[TrajectoryMetrics] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "control": self.control.to_dict(),
            "treatment": self.treatment.to_dict(),
            "success_rate_delta": self.success_rate_delta,
            "token_reduction_pct": self.token_reduction_pct,
            "loop_reduction_pct": self.loop_reduction_pct,
            "n_trajectories": len(self.trajectory_metrics),
        }


def estimate_tokens(n_steps: int) -> int:
    """
    Estimate token count from step count.

    Based on analysis of SWE-agent trajectories:
    - Average ~500 tokens per step (context + response)
    """
    return n_steps * 500


def compute_group_metrics(
    trajectory_metrics: List[TrajectoryMetrics],
    group_name: str
) -> GroupMetrics:
    """Compute aggregated metrics for a group of trajectories."""
    if not trajectory_metrics:
        return GroupMetrics(group_name=group_name)

    n = len(trajectory_metrics)

    # Basic counts
    n_success = sum(1 for m in trajectory_metrics if m.success)
    total_steps = sum(m.total_steps for m in trajectory_metrics)
    total_tokens = sum(m.total_tokens_estimate for m in trajectory_metrics)

    # Steps distribution
    steps_list = [m.total_steps for m in trajectory_metrics]
    avg_steps = statistics.mean(steps_list)
    median_steps = statistics.median(steps_list)

    # Loop metrics
    n_with_loops = sum(1 for m in trajectory_metrics if m.loops_detected > 0)
    loop_steps_wasted = [m.loop_steps_wasted for m in trajectory_metrics if m.loops_detected > 0]
    avg_loop_steps_wasted = statistics.mean(loop_steps_wasted) if loop_steps_wasted else 0

    # Intervention metrics
    n_with_interventions = sum(1 for m in trajectory_metrics if m.total_interventions > 0)
    intervention_counts = [m.total_interventions for m in trajectory_metrics]
    first_intervention_steps = [
        m.first_intervention_step for m in trajectory_metrics
        if m.first_intervention_step is not None
    ]

    # Estimated improvements
    steps_saved = [m.estimated_steps_saved for m in trajectory_metrics]
    avg_steps_saved = statistics.mean(steps_saved) if steps_saved else 0

    # Estimate improved success rate
    n_improved = sum(
        1 for m in trajectory_metrics
        if m.estimated_success_with_memory is True and not m.success
    )
    estimated_improved_success_rate = (n_success + n_improved) / n if n > 0 else 0

    return GroupMetrics(
        group_name=group_name,
        count=n,
        n_success=n_success,
        success_rate=n_success / n if n > 0 else 0,
        avg_steps=avg_steps,
        median_steps=median_steps,
        total_steps=total_steps,
        avg_tokens=total_tokens / n if n > 0 else 0,
        total_tokens=total_tokens,
        n_with_loops=n_with_loops,
        loop_rate=n_with_loops / n if n > 0 else 0,
        avg_loop_steps_wasted=avg_loop_steps_wasted,
        n_with_interventions=n_with_interventions,
        intervention_rate=n_with_interventions / n if n > 0 else 0,
        avg_interventions_per_trajectory=statistics.mean(intervention_counts) if intervention_counts else 0,
        avg_first_intervention_step=statistics.mean(first_intervention_steps) if first_intervention_steps else 0,
        avg_steps_saved=avg_steps_saved,
        estimated_improved_success_rate=estimated_improved_success_rate,
    )


def compute_experiment_metrics(
    control_metrics: List[TrajectoryMetrics],
    treatment_metrics: List[TrajectoryMetrics],
) -> ExperimentMetrics:
    """Compute complete experiment metrics from control and treatment groups."""

    control = compute_group_metrics(control_metrics, "control")
    treatment = compute_group_metrics(treatment_metrics, "treatment")

    # Comparison metrics
    success_rate_delta = treatment.estimated_improved_success_rate - control.success_rate

    # Token reduction: compare treatment (with savings) to control
    treatment_tokens_with_savings = treatment.total_tokens - (treatment.avg_steps_saved * 500 * treatment.count)
    token_reduction_pct = (
        (control.total_tokens - treatment_tokens_with_savings) / control.total_tokens * 100
        if control.total_tokens > 0 else 0
    )

    # Loop reduction
    loop_reduction_pct = (
        (control.loop_rate - treatment.loop_rate) / control.loop_rate * 100
        if control.loop_rate > 0 else 0
    )

    return ExperimentMetrics(
        control=control,
        treatment=treatment,
        success_rate_delta=success_rate_delta,
        token_reduction_pct=token_reduction_pct,
        loop_reduction_pct=loop_reduction_pct,
        trajectory_metrics=control_metrics + treatment_metrics,
    )
