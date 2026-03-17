#!/usr/bin/env python3
"""Run an online learning experiment with pass@k and pass^k metrics.

Instances run serially. After each attempt (success or failure), the trajectory
is ingested into Neo4j via AgentMemory.learn() (treatment group only).
Each problem gets k retry attempts.

Usage:
    # Run treatment group, 3 attempts per problem, 200 problems:
    python scripts/run_online_learning_experiment.py --group treatment

    # Run both groups, 2 attempts, 5 problems (quick test):
    python scripts/run_online_learning_experiment.py --group both --attempts 2 --n 5

    # Dry run:
    python scripts/run_online_learning_experiment.py --dry-run --n 2 --attempts 2

    # Resume an interrupted run:
    python scripts/run_online_learning_experiment.py --group treatment
    # (automatically skips completed instance::attempt pairs)
"""

import argparse
import json
import logging
import os
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

DEFAULT_OUTPUT_BASE = REPO_ROOT / "results" / "online_learning"
SELECTION_SEED = 42
DEFAULT_TREATMENT_NEO4J_PORT = 7688  # Treatment uses a cloned graph


@dataclass
class AttemptResult:
    """Result of a single attempt on a single problem."""

    instance_id: str
    attempt: int
    success: bool = False
    tokens_sent: int = 0
    tokens_received: int = 0
    total_cost: float = 0.0
    num_steps: int = 0
    exit_status: Optional[str] = None
    learned_traj_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class OnlineProgress:
    """Tracks progress for an online learning experiment group."""

    completed: Dict[str, AttemptResult] = field(default_factory=dict)

    def key(self, instance_id: str, attempt: int) -> str:
        return f"{instance_id}::attempt_{attempt}"

    def is_done(self, instance_id: str, attempt: int) -> bool:
        return self.key(instance_id, attempt) in self.completed

    def add(self, result: AttemptResult) -> None:
        self.completed[self.key(result.instance_id, result.attempt)] = result

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: asdict(v) for k, v in self.completed.items()}
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "OnlineProgress":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        completed = {}
        for k, v in data.items():
            completed[k] = AttemptResult(**v)
        return cls(completed=completed)


def load_instance_ids(max_problems: int, seed: int, cache_dir: Path) -> List[str]:
    """Load instance IDs from SWE-bench Verified.

    Reuses the same selection logic as run_real_swe_experiment.py for consistency.
    """
    # Import from sibling script
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from run_real_swe_experiment import load_verified_instance_ids

    return load_verified_instance_ids(
        max_problems=max_problems,
        seed=seed,
        cache_file=cache_dir / "verified_instance_ids.json",
    )


def parse_traj_file(output_dir: Path, instance_id: str) -> Optional[AttemptResult]:
    """Parse a SWE-agent .traj file into an AttemptResult.

    SWE-agent writes: output_dir/instance_id/instance_id.traj
    """
    traj_file = output_dir / instance_id / f"{instance_id}.traj"
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

    return AttemptResult(
        instance_id=instance_id,
        attempt=0,  # caller sets this
        success=success,
        tokens_sent=model_stats.get("tokens_sent", 0),
        tokens_received=model_stats.get("tokens_received", 0),
        total_cost=model_stats.get("instance_cost", 0.0),
        num_steps=len(trajectory),
        exit_status=exit_status,
    )


def run_swe_agent_single(
    instance_id: str,
    config_path: Path,
    output_dir: Path,
    swe_bench_subset: str = "verified",
    swe_bench_split: str = "test",
    timeout: int = 1800,
) -> int:
    """Run SWE-agent for a single instance. Returns exit code."""
    import re
    import subprocess

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

    env = {**os.environ, "CONTEXT_GRAPH_ROOT": str(REPO_ROOT)}

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


def learn_from_trajectory(
    memory,
    traj_path: Path,
) -> Optional[str]:
    """Ingest a .traj file into the memory graph. Returns trajectory ID."""
    from agent_memory.evaluation.trajectory_parser import parse_swe_agent_trajectory

    try:
        raw = parse_swe_agent_trajectory(traj_path)
        traj_id = memory.learn(raw)
        logger.info("Learned trajectory %s (success=%s)", traj_id, raw.success)
        return traj_id
    except Exception as e:
        logger.warning("Failed to learn from %s: %s", traj_path, e)
        return None


def _resolve_config(
    group: str, treatment_neo4j_port: int, output_dir: Path,
) -> Path:
    """Resolve SWE-agent config, adjusting Neo4j port for treatment if needed.

    When treatment_neo4j_port differs from the default 7687, generates a
    modified YAML config pointing to the cloned Neo4j instance.
    """
    base_config = CONFIG_MAP[group]
    if group != "treatment" or treatment_neo4j_port == 7687:
        return base_config

    content = base_config.read_text(encoding="utf-8")
    content = content.replace(
        "bolt://host.docker.internal:7687",
        f"bolt://host.docker.internal:{treatment_neo4j_port}",
    )
    modified = output_dir / "swe_agent_treatment_generated.yaml"
    modified.parent.mkdir(parents=True, exist_ok=True)
    modified.write_text(content, encoding="utf-8")
    logger.info(
        "Generated treatment config with Neo4j port %d: %s",
        treatment_neo4j_port, modified,
    )
    return modified


def run_group(
    group: str,
    instance_ids: List[str],
    k_attempts: int,
    output_base: Path,
    consolidate_every: int = 8,
    treatment_neo4j_port: int = 7687,
    dry_run: bool = False,
) -> OnlineProgress:
    """Run one experiment group with online learning.

    For each problem, runs k attempts serially. Treatment group ingests
    trajectories into the graph after each attempt.
    """
    group_dir = output_base / group
    config_path = _resolve_config(group, treatment_neo4j_port, group_dir)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    progress_file = group_dir / "progress.json"
    progress = OnlineProgress.load(progress_file)

    # Initialize memory for treatment group
    memory = None
    if group == "treatment" and not dry_run:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / ".env")

        from agent_memory.memory import AgentMemory

        neo4j_uri = f"bolt://localhost:{treatment_neo4j_port}"
        neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
        neo4j_pass = os.environ.get("NEO4J_PASSWORD")
        if not neo4j_pass:
            raise RuntimeError("NEO4J_PASSWORD environment variable is required (set it in .env)")
        embedding_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        embedding_base_url = os.environ.get("OPENAI_BASE_URL")

        memory = AgentMemory(
            neo4j_uri=neo4j_uri,
            neo4j_auth=(neo4j_user, neo4j_pass),
            embedding_api_key=embedding_key,
            embedding_base_url=embedding_base_url,
            consolidate_every=consolidate_every,
        )
        logger.info("Initialized AgentMemory (consolidate_every=%d)", consolidate_every)

    total = len(instance_ids) * k_attempts
    done = sum(1 for iid in instance_ids for a in range(1, k_attempts + 1)
               if progress.is_done(iid, a))
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

    trajs_ingested = 0

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

                # Online learning: ingest trajectory into graph
                if memory is not None:
                    traj_path = attempt_dir / instance_id / f"{instance_id}.traj"
                    if traj_path.exists():
                        traj_id = learn_from_trajectory(memory, traj_path)
                        result.learned_traj_id = traj_id
                        if traj_id:
                            trajs_ingested += 1

                progress.add(result)
                progress.save(progress_file)

                logger.info(
                    "  -> success=%s, cost=$%.2f, steps=%d, time=%.0fs",
                    result.success, result.total_cost, result.num_steps, wall_time,
                )
    finally:
        if memory is not None:
            logger.info("Closing AgentMemory (ingested %d trajectories)", trajs_ingested)
            memory.close()

    return progress


def build_results_json(
    instance_ids: List[str],
    k_attempts: int,
    groups: Dict[str, OnlineProgress],
) -> dict:
    """Build the output results.json."""
    result = {
        "experiment_type": "online_learning",
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
            learned_traj_ids = []

            for a in range(1, k_attempts + 1):
                key = progress.key(iid, a)
                ar = progress.completed.get(key)
                if ar is not None:
                    attempts.append(ar.success)
                    tokens.append(ar.tokens_sent + ar.tokens_received)
                    costs.append(ar.total_cost)
                    if ar.learned_traj_id:
                        learned_traj_ids.append(ar.learned_traj_id)

            entry = {
                "id": iid,
                "attempts": attempts,
                "tokens": tokens,
                "costs": costs,
            }
            if group_name == "treatment" and learned_traj_ids:
                entry["learned_traj_ids"] = learned_traj_ids
            problems.append(entry)

        group_data = {"problems": problems}
        if group_name == "treatment":
            total_ingested = sum(
                1 for ar in progress.completed.values()
                if ar.learned_traj_id is not None
            )
            group_data["graph_stats"] = {"trajectories_ingested": total_ingested}

        result[group_name] = group_data

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Run online learning experiment with pass@k and pass^k",
    )
    parser.add_argument(
        "--group", choices=["control", "treatment", "both"],
        default="both", help="Which group(s) to run (default: both)",
    )
    parser.add_argument(
        "--attempts", type=int, default=3,
        help="Number of retry attempts per problem (default: 3)",
    )
    parser.add_argument(
        "--n", type=int, default=200,
        help="Number of test problems (default: 200)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Base output directory (default: results/online_learning/)",
    )
    parser.add_argument(
        "--seed", type=int, default=SELECTION_SEED,
        help="Random seed for problem selection (default: 42)",
    )
    parser.add_argument(
        "--consolidate-every", type=int, default=8,
        help="Consolidation frequency for online learning (default: 8)",
    )
    parser.add_argument(
        "--treatment-neo4j-port", type=int, default=DEFAULT_TREATMENT_NEO4J_PORT,
        help=(
            "Neo4j bolt port for treatment group's cloned graph (default: 7688). "
            "Clone the graph first: bash scripts/clone_neo4j_graph.sh"
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without running SWE-agent",
    )

    args = parser.parse_args()
    output_base = args.output_dir or DEFAULT_OUTPUT_BASE
    output_base.mkdir(parents=True, exist_ok=True)

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

    all_progress = {}
    for group in groups_to_run:
        logger.info("=" * 60)
        logger.info("STARTING %s GROUP (%d problems, %d attempts each)",
                     group.upper(), len(instance_ids), args.attempts)
        logger.info("=" * 60)

        progress = run_group(
            group=group,
            instance_ids=instance_ids,
            k_attempts=args.attempts,
            output_base=output_base,
            consolidate_every=args.consolidate_every,
            treatment_neo4j_port=args.treatment_neo4j_port,
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


def _print_summary(
    instance_ids: List[str],
    k_attempts: int,
    groups: Dict[str, OnlineProgress],
) -> None:
    """Print a human-readable summary."""
    print(f"\n{'=' * 60}")
    print("ONLINE LEARNING EXPERIMENT SUMMARY")
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

        # pass@1: any problem with first attempt success
        pass_at_1 = sum(
            1 for iid in instance_ids
            if (ar := progress.completed.get(progress.key(iid, 1))) is not None
            and ar.success
        )
        # pass@k: any problem with any attempt success
        pass_at_k = sum(
            1 for iid in instance_ids
            if any(
                (ar := progress.completed.get(progress.key(iid, a))) is not None
                and ar.success
                for a in range(1, k_attempts + 1)
            )
        )

        n = len(instance_ids)
        print(f"\n{group_name.upper()}:")
        print(f"  Completed attempts: {total_attempts}/{n * k_attempts}")
        print(f"  pass@1: {pass_at_1}/{n} ({pass_at_1 / n * 100:.1f}%)")
        print(f"  pass@{k_attempts}: {pass_at_k}/{n} ({pass_at_k / n * 100:.1f}%)")
        print(f"  Total cost: ${total_cost:.2f}")

    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
