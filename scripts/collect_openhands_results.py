#!/usr/bin/env python
"""
Collect and analyse results from OpenHands A/B experiment runs.

Reads one or more ``openhands_results_*.json`` files produced by
``run_openhands_experiment.py`` and produces:

1. A combined summary JSON (``openhands_combined_summary.json``)
2. A human-readable report printed to stdout

Usage:
    python scripts/collect_openhands_results.py results/live_experiment/
    python scripts/collect_openhands_results.py results/live_experiment/ --output combined.json
"""

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from experiments.ab_test.metrics import (
    TrajectoryMetrics,
    GroupMetrics,
    ExperimentMetrics,
    compute_group_metrics,
    compute_experiment_metrics,
    estimate_tokens,
)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_result_files(results_dir: Path) -> List[Dict]:
    """Load all openhands result JSON files from a directory."""
    pattern = "openhands_results_*.json"
    files = sorted(results_dir.glob(pattern))
    if not files:
        print(f"No files matching '{pattern}' found in {results_dir}")
        return []

    all_results: List[Dict] = []
    for f in files:
        try:
            data = json.loads(f.read_text())
            for entry in data.get("results", []):
                entry["_source_file"] = f.name
            all_results.extend(data.get("results", []))
            print(f"  Loaded {len(data.get('results', []))} results from {f.name}")
        except (json.JSONDecodeError, IOError) as exc:
            print(f"  WARNING: could not read {f}: {exc}")

    return all_results


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def result_to_trajectory_metrics(entry: Dict) -> TrajectoryMetrics:
    """Convert a ProblemResult dict to a TrajectoryMetrics object."""
    total_steps = entry.get("total_steps", 0)
    tokens = entry.get("total_tokens", 0) or estimate_tokens(total_steps)

    return TrajectoryMetrics(
        instance_id=entry["instance_id"],
        success=entry.get("success", False),
        total_steps=total_steps,
        total_tokens_estimate=tokens,
        total_interventions=entry.get("interventions", 0),
        loops_detected=entry.get("loops_detected", 0),
        estimated_steps_saved=0,
    )


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def to_analyst_format(results: List[Dict]) -> Dict:
    """
    Convert results to the analyst-expected format for cross-agent comparison.

    Output format::

        {
            "agent": "openhands",
            "n_problems": 200,
            "control": {
                "problems": [
                    {"id": "instance_id", "attempts": [true, false, ...], "tokens": [1234, ...]}
                ]
            },
            "treatment": {
                "problems": [
                    {"id": "instance_id", "attempts": [true, false, ...], "tokens": [1234, ...]}
                ]
            }
        }
    """
    from collections import defaultdict

    # Group by (group, instance_id) — multiple result files may contain
    # repeated runs of the same problem, which become separate attempts.
    grouped: Dict[str, Dict[str, List[Dict]]] = {
        "control": defaultdict(list),
        "treatment": defaultdict(list),
    }
    for r in results:
        group = r.get("group", "control")
        iid = r.get("instance_id", "unknown")
        if group in grouped:
            grouped[group][iid].append(r)

    def _build_problems(entries_by_id: Dict[str, List[Dict]]) -> List[Dict]:
        problems = []
        for iid, entries in sorted(entries_by_id.items()):
            attempts = [e.get("success", False) for e in entries]
            tokens = [
                e.get("total_tokens", 0) or estimate_tokens(e.get("total_steps", 0))
                for e in entries
            ]
            problems.append({"id": iid, "attempts": attempts, "tokens": tokens})
        return problems

    control_problems = _build_problems(grouped["control"])
    treatment_problems = _build_problems(grouped["treatment"])

    return {
        "agent": "openhands",
        "n_problems": len(control_problems) + len(treatment_problems),
        "control": {"problems": control_problems},
        "treatment": {"problems": treatment_problems},
    }


def analyse(results: List[Dict]) -> Dict:
    """Compute per-group and comparison metrics."""
    control_entries = [r for r in results if r.get("group") == "control"]
    treatment_entries = [r for r in results if r.get("group") == "treatment"]

    control_tm = [result_to_trajectory_metrics(r) for r in control_entries]
    treatment_tm = [result_to_trajectory_metrics(r) for r in treatment_entries]

    experiment_metrics = compute_experiment_metrics(control_tm, treatment_tm)

    # Extra per-group timing stats
    def timing_stats(entries: List[Dict]) -> Dict:
        durations = [e.get("duration_seconds", 0) for e in entries]
        if not durations:
            return {"avg_duration": 0, "median_duration": 0, "total_duration": 0}
        return {
            "avg_duration": round(statistics.mean(durations), 2),
            "median_duration": round(statistics.median(durations), 2),
            "total_duration": round(sum(durations), 2),
        }

    # Exit reason breakdown
    def exit_reasons(entries: List[Dict]) -> Dict[str, int]:
        reasons: Dict[str, int] = {}
        for e in entries:
            reason = e.get("exit_reason", "unknown")
            reasons[reason] = reasons.get(reason, 0) + 1
        return reasons

    return {
        "total_instances": len(results),
        "control": {
            **experiment_metrics.control.to_dict(),
            **timing_stats(control_entries),
            "exit_reasons": exit_reasons(control_entries),
        },
        "treatment": {
            **experiment_metrics.treatment.to_dict(),
            **timing_stats(treatment_entries),
            "exit_reasons": exit_reasons(treatment_entries),
            "total_interventions": sum(
                e.get("interventions", 0) for e in treatment_entries
            ),
            "total_warnings": sum(
                e.get("warnings_shown", 0) for e in treatment_entries
            ),
        },
        "deltas": {
            "success_rate_delta": experiment_metrics.success_rate_delta,
            "token_reduction_pct": experiment_metrics.token_reduction_pct,
            "loop_reduction_pct": experiment_metrics.loop_reduction_pct,
        },
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(summary: Dict):
    """Print a human-readable report."""
    print("\n" + "=" * 64)
    print("  OPENHANDS A/B EXPERIMENT  -  COMBINED RESULTS")
    print("=" * 64)

    print(f"\nTotal instances analysed: {summary['total_instances']}")

    for group_key in ("control", "treatment"):
        g = summary[group_key]
        label = group_key.upper()
        print(f"\n--- {label} ({g['count']} instances) ---")
        print(f"  Success rate:     {g['success_rate']:.1%}")
        print(f"  Avg steps:        {g['avg_steps']:.1f}")
        print(f"  Median steps:     {g['median_steps']:.1f}")
        print(f"  Avg tokens:       {g['avg_tokens']:.0f}")
        print(f"  Avg duration:     {g['avg_duration']:.1f}s")
        print(f"  Loop rate:        {g['loop_rate']:.1%}")
        if "exit_reasons" in g:
            reasons = g["exit_reasons"]
            if reasons:
                print(f"  Exit reasons:     {reasons}")
        if group_key == "treatment":
            print(f"  Intervention rate: {g['intervention_rate']:.1%}")
            print(f"  Total interventions: {g.get('total_interventions', 0)}")

    d = summary["deltas"]
    print("\n--- DELTAS (treatment - control) ---")
    print(f"  Success rate:     {d['success_rate_delta']:+.1%}")
    print(f"  Token reduction:  {d['token_reduction_pct']:.1f}%")
    print(f"  Loop reduction:   {d['loop_reduction_pct']:.1f}%")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Collect and analyse OpenHands experiment results"
    )
    parser.add_argument(
        "results_dir",
        type=Path,
        help="Directory containing openhands_results_*.json files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path for combined summary JSON",
    )
    args = parser.parse_args()

    if not args.results_dir.is_dir():
        print(f"Error: {args.results_dir} is not a directory")
        sys.exit(1)

    print(f"Loading results from {args.results_dir} ...")
    results = load_result_files(args.results_dir)
    if not results:
        print("No results to analyse.")
        sys.exit(1)

    summary = analyse(results)
    print_report(summary)

    # Save combined summary
    output_path = args.output or (args.results_dir / "openhands_combined_summary.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2))
    print(f"Combined summary saved to {output_path}")

    # Save analyst-compatible format
    analyst_data = to_analyst_format(results)
    analyst_path = args.results_dir / "openhands_results.json"
    analyst_path.write_text(json.dumps(analyst_data, indent=2))
    print(f"Analyst-format results saved to {analyst_path}")


if __name__ == "__main__":
    main()
