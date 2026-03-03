#!/usr/bin/env python3
"""Run a REAL SWE-agent A/B experiment on SWE-bench Verified problems.

This script orchestrates a full A/B experiment:
  1. Selects 200 test instance IDs from SWE-bench Verified (seed=42)
  2. Runs SWE-agent (real) on all instances for the CONTROL group (no memory)
  3. Runs SWE-agent (real) on all instances for the TREATMENT group (with QueryMemoryTool)
  4. Collects results from SWE-agent output directories
  5. Saves results to results/live_experiment/swe_agent_results.json

No data leakage: the context graph was built from 3,591 nebius trajectories (different repos).
The test problems come from SWE-bench Verified — tests whether methodology patterns transfer.

Each group can be run independently or together. Resume support skips completed instances.

Usage:
    # Run both groups (uses SWE-bench Verified, 200 problems, seed=42):
    python scripts/run_real_swe_experiment.py

    # Run only control group:
    python scripts/run_real_swe_experiment.py --group control

    # Dry run (shows what would happen):
    python scripts/run_real_swe_experiment.py --dry-run

    # Run with batch mode (parallel workers):
    python scripts/run_real_swe_experiment.py --num-workers 4

    # Use a custom split file instead of SWE-bench Verified:
    python scripts/run_real_swe_experiment.py --split-file results/live_experiment/split.json
"""

import argparse
import json
import logging
import os
import random
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

DEFAULT_OUTPUT_BASE = REPO_ROOT / "results" / "live_experiment"
VERIFIED_IDS_FILE = DEFAULT_OUTPUT_BASE / "verified_instance_ids.json"

# Fixed seed for reproducible problem selection across agents
SELECTION_SEED = 42


@dataclass
class InstanceResult:
    """Result of running SWE-agent on a single problem instance."""

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
class GroupProgress:
    """Tracks progress for one experiment group (control or treatment)."""

    completed: Dict[str, InstanceResult] = field(default_factory=dict)
    failed: Dict[str, str] = field(default_factory=dict)

    def save(self, path: Path) -> None:
        data = {
            "completed": {k: asdict(v) for k, v in self.completed.items()},
            "failed": self.failed,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "GroupProgress":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        completed = {}
        for k, v in data.get("completed", {}).items():
            completed[k] = InstanceResult(**v)
        return cls(
            completed=completed,
            failed=data.get("failed", {}),
        )


def _clean_progress(progress: GroupProgress) -> None:
    """Remove entries from failed that are already in completed."""
    stale = [k for k in progress.failed if k in progress.completed]
    for k in stale:
        del progress.failed[k]
    if stale:
        logger.info("Cleaned %d stale failed entries (now in completed)", len(stale))


def load_verified_instance_ids(
    max_problems: int = 200,
    seed: int = SELECTION_SEED,
    split: str = "test",
    cache_file: Optional[Path] = None,
) -> List[str]:
    """Load instance IDs from SWE-bench Verified, selecting a fixed subset.

    Uses the HuggingFace datasets library to load princeton-nlp/SWE-Bench_Verified.
    Shuffles with a fixed seed and selects max_problems instances.

    The selected IDs are cached to a JSON file so that both SWE-agent and OpenHands
    runners use the exact same problem set.

    Args:
        max_problems: Number of problems to select.
        seed: Random seed for reproducible selection.
        split: Dataset split ("test" or "dev").
        cache_file: Path to cache the selected IDs. If exists and valid, loads from cache.

    Returns:
        List of instance IDs (e.g., ["django__django-12345", ...]).
    """
    if cache_file is None:
        cache_file = VERIFIED_IDS_FILE

    # Try loading from cache first
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            ids = cached.get("instance_ids", [])
            cached_seed = cached.get("seed")
            cached_max = cached.get("max_problems")
            if ids and cached_seed == seed and cached_max == max_problems:
                logger.info(
                    "Loaded %d cached Verified instance IDs from %s",
                    len(ids), cache_file,
                )
                return ids
        except (json.JSONDecodeError, OSError):
            pass

    # Load from HuggingFace
    logger.info("Loading SWE-bench Verified dataset (split=%s)...", split)
    try:
        from datasets import load_dataset
    except ImportError:
        logger.error(
            "The 'datasets' library is required to load SWE-bench Verified. "
            "Install it with: pip install datasets"
        )
        sys.exit(1)

    ds = load_dataset("princeton-nlp/SWE-Bench_Verified", split=split)
    all_ids = [instance["instance_id"] for instance in ds]
    logger.info("SWE-bench Verified has %d instances (split=%s)", len(all_ids), split)

    # Deterministic shuffle and selection
    rng = random.Random(seed)
    rng.shuffle(all_ids)
    selected_ids = sorted(all_ids[:max_problems])  # Sort for deterministic order

    logger.info(
        "Selected %d instances (seed=%d), e.g.: %s",
        len(selected_ids), seed, selected_ids[:3],
    )

    # Cache for cross-agent consistency
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({
        "instance_ids": selected_ids,
        "seed": seed,
        "max_problems": max_problems,
        "split": split,
        "source": "princeton-nlp/SWE-Bench_Verified",
        "total_available": len(all_ids),
    }, indent=2), encoding="utf-8")
    logger.info("Cached selected IDs to %s", cache_file)

    return selected_ids


def load_test_instance_ids(split_file: Path, max_problems: int) -> List[str]:
    """Load test problem instance IDs from a split file (legacy format).

    Supports multiple key names in the JSON for backwards compatibility.
    """
    data = json.loads(split_file.read_text(encoding="utf-8"))

    # Try direct instance ID list first
    if "instance_ids" in data and data["instance_ids"]:
        return data["instance_ids"][:max_problems]
    if "test_ids" in data and data["test_ids"]:
        return data["test_ids"][:max_problems]

    # Extract IDs from file paths (test_200_files is the 200-problem test set)
    test_files = (
        data.get("test_200_files")
        or data.get("test_files")
        or data.get("all_test_files", [])
    )
    instance_ids = [Path(f).stem for f in test_files]
    return instance_ids[:max_problems]


def parse_traj_file(output_dir: Path, instance_id: str) -> Optional[InstanceResult]:
    """Parse a SWE-agent .traj file for a completed instance.

    SWE-agent output structure:
        output_dir/instance_id/instance_id.traj
    """
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

    return InstanceResult(
        instance_id=instance_id,
        group="",
        exit_status=exit_status,
        success=success,
        tokens_sent=model_stats.get("tokens_sent", 0),
        tokens_received=model_stats.get("tokens_received", 0),
        total_cost=model_stats.get("instance_cost", 0.0),
        num_steps=len(trajectory),
        api_calls=model_stats.get("api_calls", 0),
    )


def run_swe_agent_batch(
    instance_ids: List[str],
    config_path: Path,
    output_dir: Path,
    num_workers: int = 1,
    swe_bench_subset: str = "verified",
    swe_bench_split: str = "test",
) -> subprocess.CompletedProcess:
    """Run SWE-agent batch mode for multiple instances using sweagent run-batch.

    Uses instance_id regex filter to select the subset of problems.
    """
    # Build a regex filter matching any of the instance IDs.
    # Escape special regex chars in instance IDs and join with |.
    import re
    escaped_ids = [re.escape(iid) for iid in instance_ids]
    filter_pattern = "^(" + "|".join(escaped_ids) + ")$"

    cmd = [
        sys.executable, "-m", "sweagent",
        "run-batch",
        "--config", str(config_path),
        "--instances.type", "swe_bench",
        "--instances.subset", swe_bench_subset,
        "--instances.split", swe_bench_split,
        "--instances.filter", filter_pattern,
        "--output_dir", str(output_dir),
        "--num_workers", str(num_workers),
    ]

    # On Linux, Docker containers need --add-host to resolve host.docker.internal
    # (required for treatment group to reach Neo4j on the host).
    if sys.platform == "linux":
        cmd += [
            "--instances.deployment.docker_args",
            '["--add-host=host.docker.internal:host-gateway"]',
        ]

    env = {
        **os.environ,
        "CONTEXT_GRAPH_ROOT": str(REPO_ROOT),
    }

    logger.info(
        "Running SWE-agent batch: %d instances, %d workers, config=%s",
        len(instance_ids), num_workers, config_path.name,
    )
    logger.debug("Command: %s", " ".join(cmd))

    return subprocess.run(
        cmd,
        cwd=str(SWE_AGENT_DIR),
        env=env,
        timeout=None,  # No timeout for batch — individual instances have cost limits
    )


def run_swe_agent_single(
    instance_id: str,
    config_path: Path,
    output_dir: Path,
    swe_bench_subset: str = "verified",
    swe_bench_split: str = "test",
    timeout: int = 1800,
) -> subprocess.CompletedProcess:
    """Run SWE-agent for a single problem instance.

    Uses `sweagent run-batch` with a filter matching exactly one instance,
    since `sweagent run` (RunSingleConfig) does not support --instances.filter.
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

    # On Linux, Docker containers need --add-host to resolve host.docker.internal
    # (required for treatment group to reach Neo4j on the host).
    if sys.platform == "linux":
        cmd += [
            "--instances.deployment.docker_args",
            '["--add-host=host.docker.internal:host-gateway"]',
        ]

    env = {
        **os.environ,
        "CONTEXT_GRAPH_ROOT": str(REPO_ROOT),
    }

    logger.info("Running SWE-agent for %s", instance_id)
    logger.debug("Command: %s", " ".join(cmd))

    return subprocess.run(
        cmd,
        cwd=str(SWE_AGENT_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_group_sequential(
    group: str,
    instance_ids: List[str],
    output_dir: Path,
    swe_bench_subset: str = "verified",
    swe_bench_split: str = "test",
    dry_run: bool = False,
) -> GroupProgress:
    """Run one experiment group using a single SWE-agent batch (num_workers=1).

    SWE-agent's own resume mechanism (redo_existing=False) skips instances
    that already have .traj files, so this is both efficient and resumable.
    After the batch finishes, we scan the output directory to build progress.
    """
    config_path = CONFIG_MAP[group]
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    progress_file = output_dir / "progress.json"
    progress = GroupProgress.load(progress_file)

    # Clean stale failed entries that have since succeeded
    _clean_progress(progress)

    remaining = [iid for iid in instance_ids if iid not in progress.completed]
    logger.info(
        "Group '%s': %d total, %d completed, %d remaining",
        group, len(instance_ids), len(progress.completed), len(remaining),
    )

    if not remaining:
        logger.info("All instances completed for group '%s'", group)
        return progress

    if dry_run:
        logger.info("DRY RUN — would process: %s", remaining[:5])
        return progress

    # Run all remaining instances in a single SWE-agent batch process.
    # SWE-agent's redo_existing=False will skip instances with existing .traj files.
    start_time = time.time()
    timed_out = False
    try:
        proc = run_swe_agent_batch(
            remaining, config_path, output_dir,
            num_workers=1,
            swe_bench_subset=swe_bench_subset,
            swe_bench_split=swe_bench_split,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Batch timed out for group '%s'", group)
        timed_out = True
        proc = None

    total_time = time.time() - start_time

    if proc is not None:
        logger.info(
            "Batch completed in %.0fs (exit code %d)",
            total_time, proc.returncode,
        )

    # Scan output directory for results
    for instance_id in instance_ids:
        if instance_id in progress.completed:
            continue
        result = parse_traj_file(output_dir, instance_id)
        if result is not None:
            result.group = group
            progress.completed[instance_id] = result
            # Clear from failed if previously recorded
            progress.failed.pop(instance_id, None)
            logger.info(
                "Completed %s: success=%s, cost=$%.2f, steps=%d",
                instance_id, result.success, result.total_cost, result.num_steps,
            )
        elif instance_id not in progress.failed:
            if timed_out:
                progress.failed[instance_id] = "Timeout"
            else:
                returncode = proc.returncode if proc else -1
                progress.failed[instance_id] = (
                    f"No .traj file (exit code {returncode})"
                )

    _clean_progress(progress)
    progress.save(progress_file)

    logger.info(
        "Group '%s' progress: %d/%d completed",
        group, len(progress.completed), len(instance_ids),
    )
    return progress


def run_group_batch(
    group: str,
    instance_ids: List[str],
    output_dir: Path,
    num_workers: int = 4,
    swe_bench_subset: str = "verified",
    swe_bench_split: str = "test",
    dry_run: bool = False,
) -> GroupProgress:
    """Run one experiment group using sweagent run-batch (parallel workers).

    SWE-agent's redo_existing=False skips instances with existing .traj files.
    """
    config_path = CONFIG_MAP[group]
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    progress_file = output_dir / "progress.json"
    progress = GroupProgress.load(progress_file)
    _clean_progress(progress)

    remaining = [iid for iid in instance_ids if iid not in progress.completed]

    if dry_run:
        logger.info("DRY RUN — would batch-process %d instances", len(remaining))
        return progress

    if not remaining:
        logger.info("All instances completed for group '%s'", group)
        return progress

    logger.info(
        "Starting batch run for group '%s': %d remaining (%d already completed), %d workers",
        group, len(remaining), len(progress.completed), num_workers,
    )

    start_time = time.time()
    proc = run_swe_agent_batch(
        remaining, config_path, output_dir,
        num_workers=num_workers,
        swe_bench_subset=swe_bench_subset,
        swe_bench_split=swe_bench_split,
    )
    total_time = time.time() - start_time

    logger.info(
        "Batch run completed in %.0fs (exit code %d)",
        total_time, proc.returncode,
    )

    # Parse all results from the output directory
    for instance_id in instance_ids:
        if instance_id in progress.completed:
            continue
        result = parse_traj_file(output_dir, instance_id)
        if result is not None:
            result.group = group
            progress.completed[instance_id] = result
            progress.failed.pop(instance_id, None)
        elif instance_id not in progress.failed:
            progress.failed[instance_id] = "No .traj file after batch run"

    _clean_progress(progress)
    progress.save(progress_file)
    return progress


def collect_results(
    control_progress: GroupProgress,
    treatment_progress: GroupProgress,
) -> dict:
    """Build the output JSON in the format expected by the analysis pipeline.

    Format:
    {
        "agent": "swe-agent",
        "n_problems": N,
        "control": {"problems": [{"id": ..., "attempts": [...], "tokens": [...]}]},
        "treatment": {"problems": [...]}
    }
    """
    # Find common problem IDs for fair comparison
    control_ids = set(control_progress.completed.keys())
    treatment_ids = set(treatment_progress.completed.keys())
    common_ids = sorted(control_ids & treatment_ids)

    logger.info(
        "Results: control=%d, treatment=%d, common=%d",
        len(control_ids), len(treatment_ids), len(common_ids),
    )

    def _build_problems(progress: GroupProgress, ids: List[str]) -> List[dict]:
        problems = []
        for iid in ids:
            r = progress.completed[iid]
            total_tokens = r.tokens_sent + r.tokens_received
            problems.append({
                "id": iid,
                "attempts": [r.success],
                "tokens": [total_tokens],
            })
        return problems

    return {
        "agent": "swe-agent",
        "n_problems": len(common_ids),
        "control": {"problems": _build_problems(control_progress, common_ids)},
        "treatment": {"problems": _build_problems(treatment_progress, common_ids)},
    }


def main():
    parser = argparse.ArgumentParser(
        description="Run a REAL SWE-agent A/B experiment on SWE-bench Verified",
    )
    parser.add_argument(
        "--split-file",
        type=Path,
        default=None,
        help="Path to split.json with test problem instance IDs. "
             "If not provided, loads from SWE-bench Verified (default).",
    )
    parser.add_argument(
        "--group",
        choices=["control", "treatment", "both"],
        default="both",
        help="Which experiment group(s) to run (default: both)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Base output directory (default: results/live_experiment/)",
    )
    parser.add_argument(
        "--max-problems",
        type=int,
        default=200,
        help="Maximum number of test problems (default: 200)",
    )
    parser.add_argument(
        "--num-workers",
        type=int,
        default=1,
        help="Number of parallel workers. 1=sequential mode (default: 1)",
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
        help="SWE-bench subset (default: verified)",
    )
    parser.add_argument(
        "--swe-bench-split",
        type=str,
        default="test",
        help="SWE-bench split (default: test)",
    )
    parser.add_argument(
        "--results-file",
        type=Path,
        default=None,
        help="Output JSON file (default: <output-dir>/swe_agent_results.json)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=SELECTION_SEED,
        help=f"Random seed for problem selection (default: {SELECTION_SEED})",
    )
    parser.add_argument(
        "--attempt",
        type=int,
        default=1,
        help="Attempt number for pass@k (1-indexed). Each attempt uses a separate output dir. (default: 1)",
    )

    args = parser.parse_args()

    output_base = args.output_dir or DEFAULT_OUTPUT_BASE
    attempt_suffix = f"_attempt{args.attempt}" if args.attempt > 1 else ""
    control_dir = output_base / f"swe_agent_control{attempt_suffix}"
    treatment_dir = output_base / f"swe_agent_treatment{attempt_suffix}"
    results_file = args.results_file or (output_base / "swe_agent_results.json")

    # Load test instance IDs
    if args.split_file is not None:
        if not args.split_file.exists():
            logger.error("Split file not found: %s", args.split_file)
            sys.exit(1)
        instance_ids = load_test_instance_ids(args.split_file, args.max_problems)
        logger.info("Loaded %d instance IDs from split file", len(instance_ids))
    else:
        # Default: load from SWE-bench Verified
        instance_ids = load_verified_instance_ids(
            max_problems=args.max_problems,
            seed=args.seed,
            split=args.swe_bench_split,
            cache_file=output_base / "verified_instance_ids.json",
        )
        logger.info(
            "Using %d SWE-bench Verified instance IDs (seed=%d)",
            len(instance_ids), args.seed,
        )

    if not instance_ids:
        logger.error("No test instance IDs found")
        sys.exit(1)

    # Verify configs exist
    groups_to_run = (
        ["control", "treatment"] if args.group == "both"
        else [args.group]
    )
    for group in groups_to_run:
        config = CONFIG_MAP[group]
        if not config.exists():
            logger.error("Config not found: %s", config)
            sys.exit(1)

    # Choose runner function based on num_workers
    run_fn = run_group_batch if args.num_workers > 1 else run_group_sequential

    # Run experiment group(s)
    control_progress = GroupProgress()
    treatment_progress = GroupProgress()

    for group in groups_to_run:
        group_dir = control_dir if group == "control" else treatment_dir
        logger.info("=" * 60)
        logger.info("STARTING %s GROUP", group.upper())
        logger.info("=" * 60)

        kwargs = dict(
            group=group,
            instance_ids=instance_ids,
            output_dir=group_dir,
            swe_bench_subset=args.swe_bench_subset,
            swe_bench_split=args.swe_bench_split,
            dry_run=args.dry_run,
        )
        if args.num_workers > 1:
            kwargs["num_workers"] = args.num_workers

        progress = run_fn(**kwargs)

        if group == "control":
            control_progress = progress
        else:
            treatment_progress = progress

    # Collect and save results
    if not args.dry_run:
        # Re-load progress from both groups (in case only one was run this time)
        if not control_progress.completed:
            control_progress = GroupProgress.load(control_dir / "progress.json")
        if not treatment_progress.completed:
            treatment_progress = GroupProgress.load(treatment_dir / "progress.json")

        output_data = collect_results(control_progress, treatment_progress)

        results_file.parent.mkdir(parents=True, exist_ok=True)
        results_file.write_text(json.dumps(output_data, indent=2), encoding="utf-8")
        logger.info("Results saved to %s", results_file)

        # Print summary
        _print_summary(control_progress, treatment_progress, output_data)


def _print_summary(
    control: GroupProgress,
    treatment: GroupProgress,
    output_data: dict,
) -> None:
    """Print a human-readable experiment summary."""
    print(f"\n{'=' * 60}")
    print("SWE-AGENT A/B EXPERIMENT SUMMARY")
    print(f"{'=' * 60}")

    for name, progress in [("Control", control), ("Treatment", treatment)]:
        completed = len(progress.completed)
        failed = len(progress.failed)
        successes = sum(1 for r in progress.completed.values() if r.success)
        total_cost = sum(r.total_cost for r in progress.completed.values())
        print(f"\n{name}:")
        print(f"  Completed: {completed}")
        print(f"  Failed to run: {failed}")
        print(f"  Successes (submitted): {successes}")
        if completed > 0:
            print(f"  Success rate: {successes / completed * 100:.1f}%")
        print(f"  Total cost: ${total_cost:.2f}")

    n = output_data.get("n_problems", 0)
    if n > 0:
        ctrl_success = sum(
            1 for p in output_data["control"]["problems"] if any(p["attempts"])
        )
        treat_success = sum(
            1 for p in output_data["treatment"]["problems"] if any(p["attempts"])
        )
        print(f"\nComparison (n={n} common problems):")
        print(f"  Control pass@1:   {ctrl_success}/{n} ({ctrl_success / n * 100:.1f}%)")
        print(f"  Treatment pass@1: {treat_success}/{n} ({treat_success / n * 100:.1f}%)")

    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
