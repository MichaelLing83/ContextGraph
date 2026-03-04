#!/usr/bin/env python3
"""Run a rewriter ablation experiment.

Both groups use memory (static graph). The only difference is whether the
query rewriter is enabled (treatment) or not (control). No online learning —
the graph is never modified during the experiment.

Usage:
    # Pilot: 10 problems, 1 attempt (quick validation)
    python scripts/run_rewriter_experiment.py --group both --attempts 1 --n 10

    # Full experiment: 200 problems, 3 attempts
    python scripts/run_rewriter_experiment.py --group both --attempts 3 --n 200

    # Dry run:
    python scripts/run_rewriter_experiment.py --dry-run --n 2 --attempts 1

    # Resume an interrupted run:
    python scripts/run_rewriter_experiment.py --group treatment
    # (automatically skips completed instance::attempt pairs)
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
SWE_AGENT_DIR = REPO_ROOT / "SWE-agent"

CONFIG_MAP = {
    "control": REPO_ROOT / "configs" / "swe_agent_treatment.yaml",
    "treatment": REPO_ROOT / "configs" / "swe_agent_treatment_rewriter.yaml",
}

DEFAULT_OUTPUT_BASE = REPO_ROOT / "results" / "rewriter_ablation"
SELECTION_SEED = 42

# Reuse data classes and helpers from online learning experiment
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from run_online_learning_experiment import (  # noqa: E402
    AttemptResult,
    OnlineProgress,
    load_instance_ids,
    parse_traj_file,
)


def run_swe_agent_single(
    instance_id: str,
    config_path: Path,
    output_dir: Path,
    swe_bench_subset: str = "verified",
    swe_bench_split: str = "test",
    timeout: int = 1800,
) -> int:
    """Run SWE-agent for a single instance. Returns exit code.

    Propagates REWRITER_API_KEY and REWRITER_API_BASE from the host
    environment into the subprocess.
    """
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

    if sys.platform == "linux":
        cmd += [
            "--instances.deployment.docker_args",
            '["--add-host=host.docker.internal:host-gateway"]',
        ]

    env = {
        **os.environ,
        "CONTEXT_GRAPH_ROOT": str(REPO_ROOT),
        "REWRITER_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
        "REWRITER_API_BASE": os.environ.get("ANTHROPIC_API_BASE", ""),
    }

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(SWE_AGENT_DIR),
            env=env,
            timeout=timeout,
        )
        return proc.returncode
    except subprocess.TimeoutExpired:
        logger.warning("Timeout for %s after %ds", instance_id, timeout)
        return -1


def run_group(
    group: str,
    instance_ids: List[str],
    k_attempts: int,
    output_base: Path,
    dry_run: bool = False,
) -> OnlineProgress:
    """Run one experiment group (static graph, no online learning).

    For each problem, runs k attempts serially. No trajectory ingestion.
    """
    config_path = CONFIG_MAP[group]
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    group_dir = output_base / group
    progress_file = group_dir / "progress.json"
    progress = OnlineProgress.load(progress_file)

    total = len(instance_ids) * k_attempts
    done = sum(
        1 for iid in instance_ids for a in range(1, k_attempts + 1)
        if progress.is_done(iid, a)
    )
    logger.info(
        "Group '%s': %d problems x %d attempts = %d total, %d done, %d remaining",
        group, len(instance_ids), k_attempts, total, done, total - done,
    )

    if dry_run:
        for iid in instance_ids[:3]:
            for attempt in range(1, k_attempts + 1):
                status = "SKIP" if progress.is_done(iid, attempt) else "RUN"
                logger.info("  [%s] %s attempt %d", status, iid, attempt)
        logger.info("  ... (%d more problems)", max(0, len(instance_ids) - 3))
        return progress

    try:
        for i, instance_id in enumerate(instance_ids):
            for attempt in range(1, k_attempts + 1):
                if progress.is_done(instance_id, attempt):
                    continue

                logger.info(
                    "[%d/%d] %s attempt %d/%d (group=%s)",
                    i + 1, len(instance_ids), instance_id, attempt, k_attempts, group,
                )

                # Each attempt gets its own output dir to avoid redo_existing skip
                attempt_dir = group_dir / f"attempt_{attempt}"
                attempt_dir.mkdir(parents=True, exist_ok=True)

                start_time = time.time()
                exit_code = run_swe_agent_single(
                    instance_id, config_path, attempt_dir,
                )
                wall_time = time.time() - start_time

                result = parse_traj_file(attempt_dir, instance_id)
                if result is None:
                    result = AttemptResult(
                        instance_id=instance_id,
                        attempt=attempt,
                        error=f"No .traj file (exit code {exit_code})",
                    )
                else:
                    result.attempt = attempt

                progress.add(result)
                progress.save(progress_file)

                logger.info(
                    "  -> success=%s, cost=$%.2f, steps=%d, time=%.0fs",
                    result.success, result.total_cost, result.num_steps, wall_time,
                )
    finally:
        pass  # No memory to close — static graph only

    return progress


def build_results_json(
    instance_ids: List[str],
    k_attempts: int,
    groups: Dict[str, OnlineProgress],
) -> dict:
    """Build the output results.json."""
    result = {
        "experiment_type": "rewriter_ablation",
        "k_attempts": k_attempts,
        "n_problems": len(instance_ids),
        "problem_order": instance_ids,
    }

    for group_name, progress in groups.items():
        problems = []
        for iid in instance_ids:
            attempts = []
            tokens = []
            costs = []

            for a in range(1, k_attempts + 1):
                key = progress.key(iid, a)
                ar = progress.completed.get(key)
                if ar is not None:
                    attempts.append(ar.success)
                    tokens.append(ar.tokens_sent + ar.tokens_received)
                    costs.append(ar.total_cost)

            problems.append({
                "id": iid,
                "attempts": attempts,
                "tokens": tokens,
                "costs": costs,
            })

        result[group_name] = {"problems": problems}

    return result


def _print_summary(
    instance_ids: List[str],
    k_attempts: int,
    groups: Dict[str, OnlineProgress],
) -> None:
    """Print a human-readable summary."""
    print(f"\n{'=' * 60}")
    print("REWRITER ABLATION EXPERIMENT SUMMARY")
    print(f"{'=' * 60}")
    print(f"Problems: {len(instance_ids)}, Attempts per problem: {k_attempts}")

    for group_name, progress in groups.items():
        total_attempts = 0
        total_successes = 0
        total_cost = 0.0

        for iid in instance_ids:
            for a in range(1, k_attempts + 1):
                ar = progress.completed.get(progress.key(iid, a))
                if ar is not None:
                    total_attempts += 1
                    if ar.success:
                        total_successes += 1
                    total_cost += ar.total_cost

        # pass@1: first attempt success
        pass_at_1 = sum(
            1 for iid in instance_ids
            if (ar := progress.completed.get(progress.key(iid, 1))) is not None
            and ar.success
        )
        # pass@k: any attempt success
        pass_at_k = sum(
            1 for iid in instance_ids
            if any(
                (ar := progress.completed.get(progress.key(iid, a))) is not None
                and ar.success
                for a in range(1, k_attempts + 1)
            )
        )

        n = len(instance_ids)
        label = "memory + rewriter" if group_name == "treatment" else "memory only"
        print(f"\n{group_name.upper()} ({label}):")
        print(f"  Completed attempts: {total_attempts}/{n * k_attempts}")
        print(f"  pass@1: {pass_at_1}/{n} ({pass_at_1 / n * 100:.1f}%)")
        if k_attempts > 1:
            print(f"  pass@{k_attempts}: {pass_at_k}/{n} ({pass_at_k / n * 100:.1f}%)")
        print(f"  Total cost: ${total_cost:.2f}")

    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(
        description="Run rewriter ablation experiment (memory vs memory+rewriter)",
    )
    parser.add_argument(
        "--group", choices=["control", "treatment", "both"],
        default="both", help="Which group(s) to run (default: both)",
    )
    parser.add_argument(
        "--attempts", type=int, default=1,
        help="Number of attempts per problem (default: 1 for pilot, use 3 for full)",
    )
    parser.add_argument(
        "--n", type=int, default=10,
        help="Number of test problems (default: 10 for pilot)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Base output directory (default: results/rewriter_ablation/)",
    )
    parser.add_argument(
        "--seed", type=int, default=SELECTION_SEED,
        help="Random seed for problem selection (default: 42)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without running SWE-agent",
    )

    args = parser.parse_args()
    output_base = args.output_dir or DEFAULT_OUTPUT_BASE
    output_base.mkdir(parents=True, exist_ok=True)

    # Load .env for API keys
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass

    # Validate rewriter env vars for treatment group
    if args.group in ("treatment", "both") and not args.dry_run:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.error(
                "ANTHROPIC_API_KEY not set — required for rewriter. "
                "Set it in .env or environment."
            )
            sys.exit(1)
        logger.info(
            "Rewriter API key: %s...%s",
            api_key[:4], api_key[-4:] if len(api_key) > 8 else "****",
        )

    # Load instance IDs
    instance_ids = load_instance_ids(args.n, args.seed, output_base)
    logger.info("Loaded %d instance IDs (seed=%d)", len(instance_ids), args.seed)

    groups_to_run = (
        ["control", "treatment"] if args.group == "both" else [args.group]
    )

    # Verify configs
    for g in groups_to_run:
        if not CONFIG_MAP[g].exists():
            logger.error("Config not found: %s", CONFIG_MAP[g])
            sys.exit(1)
        logger.info("Config for %s: %s", g, CONFIG_MAP[g])

    all_progress = {}
    for group in groups_to_run:
        logger.info("=" * 60)
        logger.info(
            "STARTING %s GROUP (%d problems, %d attempts each)",
            group.upper(), len(instance_ids), args.attempts,
        )
        logger.info("=" * 60)

        progress = run_group(
            group=group,
            instance_ids=instance_ids,
            k_attempts=args.attempts,
            output_base=output_base,
            dry_run=args.dry_run,
        )
        all_progress[group] = progress

    # Save results
    if not args.dry_run:
        # Load progress for groups not run this session
        for g in ["control", "treatment"]:
            if g not in all_progress:
                pf = output_base / g / "progress.json"
                all_progress[g] = OnlineProgress.load(pf)

        results = build_results_json(instance_ids, args.attempts, all_progress)
        results_file = output_base / "results.json"
        results_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
        logger.info("Results saved to %s", results_file)

        _print_summary(instance_ids, args.attempts, all_progress)


if __name__ == "__main__":
    main()
