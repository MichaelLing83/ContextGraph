"""
Run full simulation on test set for Agent Memory A/B Testing.

Executes simulation on all test trajectories and generates a detailed
simulation report with metrics and predictions.
"""

import json
from datetime import datetime
from pathlib import Path

from .config import get_config
from .data_splitter import load_split
from .graph_builder import load_graph
from .simulator import simulate_test_set
from .metrics import (
    compute_group_metrics,
    InterventionType,
)


def generate_simulation_report(
    metrics: list,
    graph_stats: dict,
    output_path: Path
) -> dict:
    """Generate a comprehensive simulation report."""

    n = len(metrics)
    if n == 0:
        return {}

    # Overall metrics
    n_success = sum(1 for m in metrics if m.success)
    n_with_interventions = sum(1 for m in metrics if m.total_interventions > 0)
    n_with_loops = sum(1 for m in metrics if m.loops_detected > 0)
    n_estimated_success = sum(1 for m in metrics if m.estimated_success_with_memory)

    # Intervention type breakdown
    intervention_counts = {t.value: 0 for t in InterventionType}
    for m in metrics:
        for i in m.interventions:
            intervention_counts[i.intervention_type.value] += 1

    # Steps analysis
    total_steps = sum(m.total_steps for m in metrics)
    total_steps_saved = sum(m.estimated_steps_saved for m in metrics)
    avg_steps = total_steps / n

    # Token analysis
    total_tokens = sum(m.total_tokens_estimate for m in metrics)
    tokens_saved = total_steps_saved * 500  # Rough estimate

    # First intervention timing
    first_intervention_steps = [
        m.first_intervention_step for m in metrics
        if m.first_intervention_step is not None
    ]
    avg_first_intervention = (
        sum(first_intervention_steps) / len(first_intervention_steps)
        if first_intervention_steps else 0
    )

    # Compute group metrics for simulation (treatment estimate)
    group_metrics = compute_group_metrics(metrics, "simulation")

    report = {
        "metadata": {
            "timestamp": datetime.now().isoformat(),
            "n_trajectories": n,
            "graph_statistics": graph_stats,
        },
        "summary": {
            "original_success_rate": n_success / n,
            "estimated_success_rate": n_estimated_success / n,
            "success_rate_improvement": (n_estimated_success - n_success) / n,
            "intervention_coverage": n_with_interventions / n,
            "loop_detection_rate": n_with_loops / n,
        },
        "steps_analysis": {
            "total_steps": total_steps,
            "average_steps": avg_steps,
            "total_steps_saved": total_steps_saved,
            "steps_saved_percentage": total_steps_saved / total_steps * 100 if total_steps > 0 else 0,
        },
        "token_analysis": {
            "total_tokens_estimate": total_tokens,
            "tokens_saved_estimate": tokens_saved,
            "token_reduction_percentage": tokens_saved / total_tokens * 100 if total_tokens > 0 else 0,
        },
        "intervention_analysis": {
            "total_interventions": sum(m.total_interventions for m in metrics),
            "trajectories_with_interventions": n_with_interventions,
            "average_interventions_per_trajectory": sum(m.total_interventions for m in metrics) / n,
            "average_first_intervention_step": avg_first_intervention,
            "intervention_type_counts": intervention_counts,
        },
        "loop_analysis": {
            "trajectories_with_loops": n_with_loops,
            "loop_rate": n_with_loops / n,
            "total_loops_detected": sum(m.loops_detected for m in metrics),
        },
        "expected_outcomes": {
            "expected_pass_at_1": n_estimated_success / n,
            "expected_token_reduction": tokens_saved / total_tokens * 100 if total_tokens > 0 else 0,
            "expected_loop_reduction": (
                (n_with_loops - sum(1 for m in metrics if m.loops_detected > 0 and m.estimated_success_with_memory))
                / n_with_loops * 100 if n_with_loops > 0 else 0
            ),
        },
        "group_metrics": group_metrics.to_dict(),
    }

    # Save report
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(report, f, indent=2)

    return report


def print_report_summary(report: dict):
    """Print a human-readable summary of the simulation report."""

    print("\n" + "=" * 60)
    print("SIMULATION REPORT")
    print("=" * 60)

    summary = report["summary"]
    print("\n--- Success Rate ---")
    print(f"Original:  {summary['original_success_rate']:.1%}")
    print(f"Estimated: {summary['estimated_success_rate']:.1%}")
    print(f"Improvement: +{summary['success_rate_improvement']:.1%}")

    steps = report["steps_analysis"]
    print("\n--- Steps Analysis ---")
    print(f"Total steps: {steps['total_steps']:,}")
    print(f"Average steps: {steps['average_steps']:.1f}")
    print(f"Steps saved: {steps['total_steps_saved']:,} ({steps['steps_saved_percentage']:.1f}%)")

    tokens = report["token_analysis"]
    print("\n--- Token Analysis ---")
    print(f"Total tokens: {tokens['total_tokens_estimate']:,}")
    print(f"Tokens saved: {tokens['tokens_saved_estimate']:,} ({tokens['token_reduction_percentage']:.1f}%)")

    interventions = report["intervention_analysis"]
    print("\n--- Intervention Coverage ---")
    print(f"Trajectories with interventions: {interventions['trajectories_with_interventions']}")
    print(f"Total interventions: {interventions['total_interventions']}")
    print(f"Avg interventions per trajectory: {interventions['average_interventions_per_trajectory']:.2f}")
    print(f"Avg first intervention step: {interventions['average_first_intervention_step']:.1f}")

    print("\n--- Intervention Types ---")
    for itype, count in interventions["intervention_type_counts"].items():
        if count > 0:
            print(f"  {itype}: {count}")

    loops = report["loop_analysis"]
    print("\n--- Loop Detection ---")
    print(f"Trajectories with loops: {loops['trajectories_with_loops']} ({loops['loop_rate']:.1%})")
    print(f"Total loops detected: {loops['total_loops_detected']}")

    expected = report["expected_outcomes"]
    print("\n--- Expected Outcomes (vs. Control) ---")
    print(f"Expected pass@1: {expected['expected_pass_at_1']:.1%}")
    print(f"Expected token reduction: {expected['expected_token_reduction']:.1f}%")

    print("\n" + "=" * 60)


def main():
    """Run full simulation on test set."""
    config = get_config()

    # Load split
    split = load_split(config)
    if split is None:
        print("Error: No split found. Run data_splitter.py first.")
        return

    # Load graph
    graph = load_graph(config)
    if graph is None:
        print("Error: No graph found. Run graph_builder.py first.")
        return

    print(f"Loaded graph with {len(graph.methodologies)} methodologies")
    print(f"Running simulation on {len(split.test_ids)} test trajectories...")

    # Run simulation on full test set
    results = simulate_test_set(
        split.test_ids,
        split.test_metadata,
        graph,
        config,
        verbose=True
    )
    if not results:
        print("\nNo trajectories were simulated successfully; skipping report generation.")
        return

    # Generate report
    output_path = config.paths.results_dir / "simulation_report.json"
    report = generate_simulation_report(results, graph.statistics, output_path)
    if not report:
        print("\nSimulation report is empty; skipping summary output.")
        return

    print(f"\nReport saved to: {output_path}")

    # Print summary
    print_report_summary(report)

    # Save per-trajectory metrics
    trajectory_output = config.paths.results_dir / "simulation_trajectories.json"
    trajectory_output.parent.mkdir(parents=True, exist_ok=True)
    with open(trajectory_output, 'w') as f:
        json.dump([m.to_dict() for m in results], f, indent=2)
    print(f"Trajectory metrics saved to: {trajectory_output}")


if __name__ == "__main__":
    main()
