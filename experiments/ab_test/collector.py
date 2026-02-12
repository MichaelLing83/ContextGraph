"""
Real-time Metrics Collector for Agent Memory A/B Testing.

Collects and aggregates metrics during live experiment runs,
with support for streaming updates and persistent storage.
"""

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from threading import Lock

from .config import ExperimentConfig, get_config
from .metrics import (
    TrajectoryMetrics,
    GroupMetrics,
    InterventionPoint,
    InterventionType,
    estimate_tokens,
    compute_group_metrics,
)
from .openhands_integration import ExperimentGroup


@dataclass
class LiveTrajectoryMetrics:
    """
    Real-time metrics for a trajectory in progress.

    Extended version of TrajectoryMetrics with timing and status.
    """
    instance_id: str
    group: str  # "control" or "treatment"
    status: str = "running"  # running, completed, failed, timeout

    # Timing
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    # Outcome
    success: Optional[bool] = None
    exit_reason: Optional[str] = None

    # Step metrics
    total_steps: int = 0
    failed_actions: int = 0

    # Memory metrics (treatment only)
    memory_queries: int = 0
    warnings_shown: int = 0
    interventions: List[Dict] = field(default_factory=list)

    # Loop metrics
    loop_count: int = 0
    loop_steps_wasted: int = 0

    # Counterfactual estimate from interventions
    estimated_steps_saved: int = 0

    # Token estimate
    total_tokens_estimate: int = 0

    def duration_seconds(self) -> float:
        """Get duration in seconds."""
        end = self.end_time or time.time()
        return end - self.start_time

    def to_dict(self) -> Dict:
        result = asdict(self)
        result["duration_seconds"] = self.duration_seconds()
        return result

    def finalize(self, success: bool, exit_reason: str = "completed"):
        """Mark trajectory as complete."""
        self.status = "completed" if success else "failed"
        self.success = success
        self.exit_reason = exit_reason
        self.end_time = time.time()
        self.total_tokens_estimate = estimate_tokens(self.total_steps)


class MetricsCollector:
    """
    Thread-safe metrics collector for A/B experiment.

    Collects metrics from multiple concurrent trajectories and
    provides real-time aggregation.
    """

    def __init__(self, config: Optional[ExperimentConfig] = None):
        self.config = config or get_config()
        self.lock = Lock()

        # Active trajectories
        self.active: Dict[str, LiveTrajectoryMetrics] = {}

        # Completed trajectories by group
        self.completed_control: List[LiveTrajectoryMetrics] = []
        self.completed_treatment: List[LiveTrajectoryMetrics] = []

        # Experiment metadata
        self.start_time = datetime.now()
        self.experiment_id = self.start_time.strftime("%Y%m%d_%H%M%S")

    def start_trajectory(
        self,
        instance_id: str,
        group: ExperimentGroup
    ) -> LiveTrajectoryMetrics:
        """Start tracking a new trajectory."""
        with self.lock:
            metrics = LiveTrajectoryMetrics(
                instance_id=instance_id,
                group=group.value
            )
            self.active[instance_id] = metrics
            return metrics

    def record_step(
        self,
        instance_id: str,
        action: str,
        success: bool
    ):
        """Record a single step in a trajectory."""
        with self.lock:
            if instance_id not in self.active:
                return

            metrics = self.active[instance_id]
            metrics.total_steps += 1
            if not success:
                metrics.failed_actions += 1

    def record_memory_query(self, instance_id: str):
        """Record a memory query (treatment group)."""
        with self.lock:
            if instance_id in self.active:
                self.active[instance_id].memory_queries += 1

    def record_warning(self, instance_id: str):
        """Record a warning shown to agent."""
        with self.lock:
            if instance_id in self.active:
                self.active[instance_id].warnings_shown += 1

    def record_intervention(
        self,
        instance_id: str,
        intervention: InterventionPoint
    ):
        """Record an intervention point."""
        with self.lock:
            if instance_id in self.active:
                metrics = self.active[instance_id]
                metrics.interventions.append({
                    "step": intervention.step_number,
                    "type": intervention.intervention_type.value,
                    "confidence": intervention.confidence,
                    "details": intervention.details,
                })
                metrics.estimated_steps_saved += intervention.potential_steps_saved

    def record_loop(self, instance_id: str, loop_length: int):
        """Record a detected loop."""
        with self.lock:
            if instance_id in self.active:
                metrics = self.active[instance_id]
                metrics.loop_count += 1
                metrics.loop_steps_wasted += loop_length

    def complete_trajectory(
        self,
        instance_id: str,
        success: bool,
        exit_reason: str = "completed"
    ):
        """Mark a trajectory as complete and move to completed list."""
        with self.lock:
            if instance_id not in self.active:
                return

            metrics = self.active.pop(instance_id)
            metrics.finalize(success, exit_reason)

            if metrics.group == "control":
                self.completed_control.append(metrics)
            else:
                self.completed_treatment.append(metrics)

    def get_live_stats(self) -> Dict:
        """Get current live statistics."""
        with self.lock:
            n_control = len(self.completed_control)
            n_treatment = len(self.completed_treatment)
            n_active = len(self.active)

            control_success = sum(1 for m in self.completed_control if m.success)
            treatment_success = sum(1 for m in self.completed_treatment if m.success)

            return {
                "active_trajectories": n_active,
                "completed_control": n_control,
                "completed_treatment": n_treatment,
                "control_success_rate": control_success / n_control if n_control > 0 else 0,
                "treatment_success_rate": treatment_success / n_treatment if n_treatment > 0 else 0,
                "elapsed_time": (datetime.now() - self.start_time).total_seconds(),
            }

    def get_group_metrics(self, group: str) -> GroupMetrics:
        """Get aggregated metrics for a group."""
        with self.lock:
            if group == "control":
                completed = self.completed_control
            else:
                completed = self.completed_treatment

            if not completed:
                return GroupMetrics(group_name=group)

            # Convert to TrajectoryMetrics format
            trajectory_metrics = []
            for m in completed:
                tm = TrajectoryMetrics(
                    instance_id=m.instance_id,
                    success=m.success or False,
                    total_steps=m.total_steps,
                    total_tokens_estimate=m.total_tokens_estimate,
                    interventions=[],
                    total_interventions=len(m.interventions),
                    loops_detected=m.loop_count,
                    loop_steps_wasted=m.loop_steps_wasted,
                    estimated_steps_saved=m.estimated_steps_saved,
                )
                trajectory_metrics.append(tm)

            return compute_group_metrics(trajectory_metrics, group)

    def get_experiment_summary(self) -> Dict:
        """Get complete experiment summary."""
        control_metrics = self.get_group_metrics("control")
        treatment_metrics = self.get_group_metrics("treatment")

        # Compute deltas
        success_delta = treatment_metrics.success_rate - control_metrics.success_rate

        token_reduction = 0
        if control_metrics.avg_tokens > 0:
            token_reduction = (
                (control_metrics.avg_tokens - treatment_metrics.avg_tokens)
                / control_metrics.avg_tokens * 100
            )

        loop_reduction = 0
        if control_metrics.loop_rate > 0:
            loop_reduction = (
                (control_metrics.loop_rate - treatment_metrics.loop_rate)
                / control_metrics.loop_rate * 100
            )

        return {
            "experiment_id": self.experiment_id,
            "start_time": self.start_time.isoformat(),
            "elapsed_seconds": (datetime.now() - self.start_time).total_seconds(),
            "control": control_metrics.to_dict(),
            "treatment": treatment_metrics.to_dict(),
            "deltas": {
                "success_rate_delta": success_delta,
                "token_reduction_pct": token_reduction,
                "loop_reduction_pct": loop_reduction,
            },
            "active_trajectories": len(self.active),
        }

    def save_results(self, output_dir: Optional[Path] = None):
        """Save all results to JSON files."""
        output_dir = output_dir or self.config.paths.results_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save summary
        summary = self.get_experiment_summary()
        summary_file = output_dir / f"experiment_{self.experiment_id}_summary.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)

        # Save detailed trajectory metrics
        all_trajectories = []
        with self.lock:
            for m in self.completed_control + self.completed_treatment:
                all_trajectories.append(m.to_dict())

        trajectories_file = output_dir / f"experiment_{self.experiment_id}_trajectories.json"
        with open(trajectories_file, 'w') as f:
            json.dump(all_trajectories, f, indent=2)

        print(f"Results saved to {output_dir}")
        return summary_file, trajectories_file


def create_collector(config: Optional[ExperimentConfig] = None) -> MetricsCollector:
    """Factory function to create a MetricsCollector."""
    return MetricsCollector(config)


# ============ Example Usage ============

def example_collection():
    """Example showing how to use the MetricsCollector."""
    print("Example Metrics Collection")
    print("=" * 50)

    collector = create_collector()

    # Simulate some trajectories
    from .openhands_integration import assign_experiment_group

    instances = [f"test__instance-{i}" for i in range(10)]

    for instance_id in instances:
        group = assign_experiment_group(instance_id)
        print(f"\nProcessing {instance_id} ({group.value})")

        # Start trajectory
        collector.start_trajectory(instance_id, group)

        # Simulate some steps
        import random
        n_steps = random.randint(10, 50)
        for step in range(n_steps):
            success = random.random() > 0.1  # 90% success rate
            collector.record_step(instance_id, f"action_{step}", success)

            if group == "treatment" and random.random() > 0.7:
                collector.record_warning(instance_id)

        # Random outcome
        trajectory_success = random.random() > 0.9  # 10% success rate
        collector.complete_trajectory(instance_id, trajectory_success)

    # Print summary
    print("\n" + "=" * 50)
    print("Experiment Summary")
    print("=" * 50)

    summary = collector.get_experiment_summary()
    print(f"\nControl: {summary['control']['count']} trajectories")
    print(f"  Success rate: {summary['control']['success_rate']:.1%}")

    print(f"\nTreatment: {summary['treatment']['count']} trajectories")
    print(f"  Success rate: {summary['treatment']['success_rate']:.1%}")

    print(f"\nDeltas:")
    print(f"  Success rate: {summary['deltas']['success_rate_delta']:+.1%}")


if __name__ == "__main__":
    example_collection()
