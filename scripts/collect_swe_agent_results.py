#!/usr/bin/env python3
"""Collect and analyze SWE-agent A/B experiment results.

Reads SWE-agent output directories for control and treatment groups,
creates ProblemResult objects, computes EvaluationMetrics, and generates
a ComparisonReport.

Usage:
    python scripts/collect_swe_agent_results.py \\
        --control-dir results/live_experiment/swe_agent_control \\
        --treatment-dir results/live_experiment/swe_agent_treatment \\
        --output results/live_experiment/swe_agent_results.json
"""

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _ensure_importable():
    """Ensure agent_memory is importable."""
    try:
        import agent_memory  # noqa: F401
    except ModuleNotFoundError:
        repo_root = Path(__file__).resolve().parents[1]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))


def parse_traj_file(traj_path: Path) -> Optional[Dict]:
    """Parse a single .traj file and extract key metrics.

    Returns:
        Dict with instance_id, success, tokens, num_steps, cost, or None on error.
    """
    try:
        data = json.loads(traj_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read %s: %s", traj_path, e)
        return None

    info = data.get("info", {})
    exit_status = info.get("exit_status", "unknown")
    model_stats = info.get("model_stats", {})
    trajectory = data.get("trajectory", [])

    success = "submitted" in str(exit_status).lower()
    tokens = model_stats.get("tokens_sent", 0) + model_stats.get("tokens_received", 0)

    return {
        "instance_id": traj_path.stem,
        "success": success,
        "tokens": tokens,
        "num_steps": len(trajectory),
        "cost": model_stats.get("instance_cost", 0.0),
        "exit_status": exit_status,
    }


def collect_results_from_dir(output_dir: Path) -> Dict[str, Dict]:
    """Collect results from a SWE-agent output directory.

    SWE-agent output structure:
        output_dir/
            instance_id/
                instance_id.traj
                instance_id.patch
                ...
            preds.json
            run_batch_exit_statuses.yaml

    Returns:
        Dict mapping instance_id -> parsed result dict.
    """
    results = {}

    if not output_dir.exists():
        logger.warning("Output directory does not exist: %s", output_dir)
        return results

    # Look for .traj files in subdirectories
    for traj_file in output_dir.rglob("*.traj"):
        parsed = parse_traj_file(traj_file)
        if parsed is not None:
            results[parsed["instance_id"]] = parsed

    # Also check progress.json from the runner
    progress_file = output_dir / "progress.json"
    if progress_file.exists():
        try:
            progress = json.loads(progress_file.read_text(encoding="utf-8"))
            for instance_id, data in progress.get("completed", {}).items():
                if instance_id not in results:
                    tokens = data.get("tokens_sent", 0) + data.get("tokens_received", 0)
                    results[instance_id] = {
                        "instance_id": instance_id,
                        "success": data.get("success", False),
                        "tokens": tokens,
                        "num_steps": data.get("num_steps", 0),
                        "cost": data.get("total_cost", 0.0),
                        "exit_status": data.get("exit_status", "unknown"),
                    }
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read progress file: %s", e)

    logger.info("Collected %d results from %s", len(results), output_dir)
    return results


def build_problem_results(
    results: Dict[str, Dict],
) -> "List[ProblemResult]":
    """Convert raw result dicts to ProblemResult objects.

    Each problem has a single attempt (the live run).
    """
    from agent_memory.evaluation.metrics import ProblemResult

    problem_results = []
    for instance_id, data in sorted(results.items()):
        problem_results.append(
            ProblemResult(
                problem_id=instance_id,
                attempts=[data["success"]],
                tokens=[data["tokens"]],
            )
        )
    return problem_results


def collect_and_analyze(
    control_dir: Path,
    treatment_dir: Path,
) -> Tuple:
    """Collect results from both groups and compute comparison.

    Returns:
        (control_results, treatment_results, report, raw_control, raw_treatment)
    """
    from agent_memory.evaluation.metrics import calculate_metrics
    from agent_memory.evaluation.analyzer import compare_results

    raw_control = collect_results_from_dir(control_dir)
    raw_treatment = collect_results_from_dir(treatment_dir)

    # Find common problem IDs for fair comparison
    common_ids = set(raw_control.keys()) & set(raw_treatment.keys())
    logger.info(
        "Control: %d, Treatment: %d, Common: %d",
        len(raw_control), len(raw_treatment), len(common_ids),
    )

    # Build ProblemResults for common problems only
    control_common = {k: v for k, v in raw_control.items() if k in common_ids}
    treatment_common = {k: v for k, v in raw_treatment.items() if k in common_ids}

    control_results = build_problem_results(control_common)
    treatment_results = build_problem_results(treatment_common)

    control_metrics = calculate_metrics(control_results)
    treatment_metrics = calculate_metrics(treatment_results)
    report = compare_results(control_metrics, treatment_metrics)

    return control_results, treatment_results, report, raw_control, raw_treatment


def main():
    _ensure_importable()

    parser = argparse.ArgumentParser(
        description="Collect and analyze SWE-agent A/B experiment results"
    )
    parser.add_argument(
        "--control-dir",
        type=Path,
        required=True,
        help="Directory with control group SWE-agent output",
    )
    parser.add_argument(
        "--treatment-dir",
        type=Path,
        required=True,
        help="Directory with treatment group SWE-agent output",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/live_experiment/swe_agent_results.json"),
        help="Output JSON file for results",
    )

    args = parser.parse_args()

    (
        control_results,
        treatment_results,
        report,
        raw_control,
        raw_treatment,
    ) = collect_and_analyze(args.control_dir, args.treatment_dir)

    # Print human-readable summary
    print(report.to_summary())

    # Build output JSON in the format expected by the analysis pipeline:
    # {"agent": "swe-agent", "n_problems": N,
    #  "control": {"problems": [{"id": ..., "attempts": [...], "tokens": [...]}]},
    #  "treatment": {"problems": [...]}}
    n_problems = len(control_results)
    output_data = {
        "agent": "swe-agent",
        "n_problems": n_problems,
        "control": {
            "problems": [
                {
                    "id": r.problem_id,
                    "attempts": r.attempts,
                    "tokens": r.tokens,
                }
                for r in control_results
            ],
        },
        "treatment": {
            "problems": [
                {
                    "id": r.problem_id,
                    "attempts": r.attempts,
                    "tokens": r.tokens,
                }
                for r in treatment_results
            ],
        },
    }

    # Save
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output_data, indent=2), encoding="utf-8")
    print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
