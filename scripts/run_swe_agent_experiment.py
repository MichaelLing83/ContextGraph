#!/usr/bin/env python3
"""Runner script for SWE-agent A/B experiment.

Runs SWE-agent on SWE-bench Verified problems using control or treatment config.
Supports resume (skips already-completed problems).

Usage:
    python scripts/run_swe_agent_experiment.py \\
        --split-file results/live_experiment/verified_200.json \\
        --group control \\
        --output-dir results/live_experiment/swe_agent_control

    python scripts/run_swe_agent_experiment.py \\
        --split-file results/live_experiment/verified_200.json \\
        --group treatment \\
        --output-dir results/live_experiment/swe_agent_treatment
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
SWE_AGENT_DIR = REPO_ROOT / "SWE-agent"

CONFIG_MAP = {
    "control": REPO_ROOT / "configs" / "swe_agent_control.yaml",
    "treatment": REPO_ROOT / "configs" / "swe_agent_treatment.yaml",
}


@dataclass
class ProblemRunResult:
    """Result of running SWE-agent on a single problem."""

    instance_id: str
    group: str
    exit_status: Optional[str] = None
    success: bool = False
    tokens_sent: int = 0
    tokens_received: int = 0
    total_cost: float = 0.0
    num_steps: int = 0
    api_calls: int = 0
    wall_time_seconds: float = 0.0
    error: Optional[str] = None


@dataclass
class ExperimentProgress:
    """Tracks experiment progress for resume support."""

    completed: Dict[str, ProblemRunResult] = field(default_factory=dict)
    failed: Dict[str, str] = field(default_factory=dict)

    def save(self, path: Path) -> None:
        data = {
            "completed": {k: asdict(v) for k, v in self.completed.items()},
            "failed": self.failed,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "ExperimentProgress":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        completed = {}
        for k, v in data.get("completed", {}).items():
            completed[k] = ProblemRunResult(**v)
        return cls(
            completed=completed,
            failed=data.get("failed", {}),
        )


def load_test_problems(split_file: Path, max_problems: int) -> List[str]:
    """Load test problem IDs from the split file.

    Supports multiple formats:
    - {"instance_ids": [...]}  (verified_200.json)
    - {"test_ids": [...]}
    - {"test_200_files": [...]}  (legacy split.json with file paths)
    """
    data = json.loads(split_file.read_text(encoding="utf-8"))
    test_ids = (
        data.get("instance_ids")
        or data.get("test_ids")
        or []
    )
    if not test_ids:
        # Fallback: extract IDs from test file paths (try multiple key names)
        test_files = (
            data.get("test_200_files")
            or data.get("test_files")
            or data.get("all_test_files")
            or data.get("split", {}).get("test_files", [])
        )
        test_ids = [Path(f).stem for f in test_files]
    return test_ids[:max_problems]


def parse_traj_result(output_dir: Path, instance_id: str) -> Optional[ProblemRunResult]:
    """Parse SWE-agent output for a completed problem."""
    # SWE-agent output structure: output_dir/instance_id/instance_id.traj
    instance_dir = output_dir / instance_id
    traj_file = instance_dir / f"{instance_id}.traj"

    if not traj_file.exists():
        return None

    try:
        data = json.loads(traj_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    info = data.get("info", {})
    exit_status = info.get("exit_status", "unknown")
    model_stats = info.get("model_stats", {})
    trajectory = data.get("trajectory", [])

    success = "submitted" in str(exit_status).lower()

    return ProblemRunResult(
        instance_id=instance_id,
        group="",  # Will be set by caller
        exit_status=exit_status,
        success=success,
        tokens_sent=model_stats.get("tokens_sent", 0),
        tokens_received=model_stats.get("tokens_received", 0),
        total_cost=model_stats.get("instance_cost", 0.0),
        num_steps=len(trajectory),
        api_calls=model_stats.get("api_calls", 0),
    )


def run_swe_agent(
    instance_id: str,
    config_path: Path,
    output_dir: Path,
    swe_bench_subset: str = "lite",
    swe_bench_split: str = "test",
) -> subprocess.CompletedProcess:
    """Run SWE-agent for a single problem instance.

    Uses `sweagent run-batch` with a single-instance filter, since
    `sweagent run` (RunSingleConfig) does not support --instances.filter.
    """
    import re
    escaped_id = re.escape(instance_id)
    cmd = [
        sys.executable, "-m", "sweagent",
        "run-batch",
        "--config", str(config_path),
        "--instances.type", "swe_bench",
        "--instances.subset", swe_bench_subset,
        "--instances.split", swe_bench_split,
        "--instances.filter", f"^{escaped_id}$",
        "--output_dir", str(output_dir),
        "--num_workers", "1",
    ]

    env = {
        **os.environ,
        "CONTEXT_GRAPH_ROOT": str(REPO_ROOT),
    }
    # Load .env file if present (for API keys)
    dotenv_path = REPO_ROOT / ".env"
    if dotenv_path.exists():
        for line in dotenv_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()

    logger.info("Running SWE-agent for %s", instance_id)
    logger.debug("Command: %s", " ".join(cmd))

    return subprocess.run(
        cmd,
        cwd=str(SWE_AGENT_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,  # 30 min timeout per problem
    )


def run_experiment(
    split_file: Path,
    group: str,
    output_dir: Path,
    max_problems: int,
    dry_run: bool = False,
    swe_bench_subset: str = "lite",
    swe_bench_split: str = "test",
) -> ExperimentProgress:
    """Run the full experiment for one group.

    Args:
        split_file: Path to split.json with test problem IDs
        group: "control" or "treatment"
        output_dir: Directory for SWE-agent output
        max_problems: Maximum number of problems to run
        dry_run: If True, only print what would be done
        swe_bench_subset: SWE-bench subset name (default: "lite")
        swe_bench_split: SWE-bench split name (default: "test")

    Returns:
        ExperimentProgress with results
    """
    config_path = CONFIG_MAP[group]
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    test_problems = load_test_problems(split_file, max_problems)
    logger.info(
        "Loaded %d test problems (max %d) for group '%s'",
        len(test_problems), max_problems, group,
    )

    progress_file = output_dir / "progress.json"
    progress = ExperimentProgress.load(progress_file)
    logger.info(
        "Resuming: %d completed, %d failed",
        len(progress.completed), len(progress.failed),
    )

    remaining = [p for p in test_problems if p not in progress.completed]
    logger.info("%d problems remaining", len(remaining))

    if dry_run:
        logger.info("Dry run - would process: %s", remaining[:5])
        return progress

    for i, instance_id in enumerate(remaining):
        logger.info(
            "[%d/%d] Processing %s (%s)",
            i + 1, len(remaining), instance_id, group,
        )

        start_time = time.time()
        try:
            proc = run_swe_agent(
                instance_id, config_path, output_dir,
                swe_bench_subset=swe_bench_subset,
                swe_bench_split=swe_bench_split,
            )
            wall_time = time.time() - start_time

            if proc.returncode != 0:
                logger.warning(
                    "SWE-agent exited with code %d for %s",
                    proc.returncode, instance_id,
                )
                if proc.stderr:
                    logger.warning("stderr: %s", proc.stderr[-500:])

            # Parse the output trajectory
            result = parse_traj_result(output_dir, instance_id)
            if result is not None:
                result.group = group
                result.wall_time_seconds = wall_time
                progress.completed[instance_id] = result
                logger.info(
                    "Completed %s: success=%s, cost=$%.2f, steps=%d",
                    instance_id, result.success, result.total_cost, result.num_steps,
                )
            else:
                progress.failed[instance_id] = (
                    f"No .traj file found (exit code {proc.returncode})"
                )
                logger.warning("No trajectory output for %s", instance_id)

        except subprocess.TimeoutExpired:
            wall_time = time.time() - start_time
            progress.failed[instance_id] = "Timeout (30 min)"
            logger.warning("Timeout for %s after %.0fs", instance_id, wall_time)

        except Exception as e:
            progress.failed[instance_id] = str(e)
            logger.error("Error processing %s: %s", instance_id, e)

        # Save progress after each problem
        progress.save(progress_file)

    return progress


def main():
    parser = argparse.ArgumentParser(
        description="Run SWE-agent A/B experiment"
    )
    parser.add_argument(
        "--split-file",
        type=Path,
        required=True,
        help="Path to split.json with test problem IDs",
    )
    parser.add_argument(
        "--group",
        choices=["control", "treatment"],
        required=True,
        help="Experiment group to run",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for SWE-agent output",
    )
    parser.add_argument(
        "--max-problems",
        type=int,
        default=200,
        help="Maximum number of test problems to run (default: 200)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be done, don't run SWE-agent",
    )
    parser.add_argument(
        "--swe-bench-subset",
        type=str,
        default="verified",
        help="SWE-bench subset name (default: verified)",
    )
    parser.add_argument(
        "--swe-bench-split",
        type=str,
        default="test",
        help="SWE-bench split name (default: test)",
    )

    args = parser.parse_args()

    if not args.split_file.exists():
        logger.error("Split file not found: %s", args.split_file)
        sys.exit(1)

    progress = run_experiment(
        split_file=args.split_file,
        group=args.group,
        output_dir=args.output_dir,
        max_problems=args.max_problems,
        dry_run=args.dry_run,
        swe_bench_subset=args.swe_bench_subset,
        swe_bench_split=args.swe_bench_split,
    )

    # Print summary
    total = len(progress.completed) + len(progress.failed)
    successes = sum(1 for r in progress.completed.values() if r.success)
    total_cost = sum(r.total_cost for r in progress.completed.values())

    print(f"\n{'=' * 50}")
    print(f"EXPERIMENT SUMMARY ({args.group})")
    print(f"{'=' * 50}")
    print(f"Total processed: {total}")
    print(f"Completed: {len(progress.completed)}")
    print(f"Failed to run: {len(progress.failed)}")
    print(f"Successes: {successes}")
    print(f"Total cost: ${total_cost:.2f}")
    print(f"Progress saved to: {args.output_dir / 'progress.json'}")


if __name__ == "__main__":
    main()
