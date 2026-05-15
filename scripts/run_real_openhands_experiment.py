#!/usr/bin/env python
"""
Real OpenHands A/B experiment runner — NO simulation, NO trajectory replay.

Runs SWE-bench Verified problems through OpenHands in Docker containers:
- Control: Standard OpenHands, no memory context
- Treatment: OpenHands with MemoryHooks injecting context via conversation_instructions

Test instances come from SWE-bench Verified (500 problems from HuggingFace).
200 are selected with seed=42 for the experiment. These are completely
different repos from our 3,591 training trajectories — no data leakage.

Usage:
    python scripts/run_real_openhands_experiment.py --n 10
    python scripts/run_real_openhands_experiment.py --n 200 --model claude-sonnet-4-20250514
    python scripts/run_real_openhands_experiment.py --resume  # resume from checkpoint
"""

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Ensure imports work when run directly
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")

from experiments.ab_test.config import ExperimentConfig, get_config
from experiments.ab_test.graph_builder import AgentMemoryGraph, load_graph
from experiments.ab_test.openhands_integration import (
    AgentState as MemoryAgentState,
    ExperimentGroup,
    MemoryContext,
    MemoryHooks,
    assign_experiment_group,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("real_openhands_experiment")

# ---------------------------------------------------------------------------
# OpenHands imports
# ---------------------------------------------------------------------------
from openhands.core.config.llm_config import LLMConfig
from openhands.core.config.openhands_config import OpenHandsConfig
from openhands.core.config.sandbox_config import SandboxConfig
from openhands.core.main import (
    create_runtime,
    run_controller,
    create_memory,
    EventStreamSubscriber,
)
from openhands.events.action import MessageAction
from openhands.events.event import EventSource


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RunConfig:
    """Configuration for the real experiment."""
    # Data — test instances come from SWE-bench Verified (HuggingFace)
    n_instances: int = 200  # Number of Verified instances to select
    swebench_dataset: str = "princeton-nlp/SWE-bench_Verified"

    # LLM
    model: str = "claude-sonnet-4-20250514"
    api_key: Optional[str] = None
    api_base: Optional[str] = None

    # OpenHands
    max_iterations: int = 30
    timeout_seconds: int = 600
    base_container_image: str = "nikolaik/python-nodejs:python3.12-nodejs22"

    # Experiment
    seed: int = 42

    # Output
    output_dir: Path = field(
        default_factory=lambda: _REPO_ROOT / "results" / "live_experiment"
    )
    checkpoint_file: Optional[Path] = None  # auto-set in __post_init__

    # Flags
    resume: bool = False
    verbose: bool = True

    def __post_init__(self):
        if self.api_key is None:
            self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if self.api_base is None:
            self.api_base = os.environ.get("ANTHROPIC_API_BASE", "")
        if self.checkpoint_file is None:
            self.checkpoint_file = self.output_dir / "openhands_checkpoint.json"


@dataclass
class ProblemResult:
    """Result of running a single SWE-bench problem."""
    instance_id: str
    group: str  # "control" or "treatment"
    success: bool
    total_steps: int
    total_tokens: int
    accumulated_cost: float
    duration_seconds: float
    exit_reason: str  # "completed", "timeout", "error", "agent_finished", "agent_error"
    agent_state: str  # final agent state string
    interventions: int = 0
    warnings_shown: int = 0
    loops_detected: int = 0
    error_message: Optional[str] = None
    git_patch: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "instance_id": self.instance_id,
            "group": self.group,
            "success": self.success,
            "total_steps": self.total_steps,
            "total_tokens": self.total_tokens,
            "accumulated_cost": round(self.accumulated_cost, 6),
            "duration_seconds": round(self.duration_seconds, 2),
            "exit_reason": self.exit_reason,
            "agent_state": self.agent_state,
            "interventions": self.interventions,
            "warnings_shown": self.warnings_shown,
            "loops_detected": self.loops_detected,
            "error_message": self.error_message,
            "git_patch": self.git_patch,
        }


# ---------------------------------------------------------------------------
# Checkpoint management
# ---------------------------------------------------------------------------

class Checkpoint:
    """Manages experiment progress for resume support."""

    def __init__(self, path: Path):
        self.path = path
        self.completed: Dict[str, ProblemResult] = {}
        self.failed: Dict[str, str] = {}

    def load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                for iid, entry in data.get("completed", {}).items():
                    # Reconstruct ProblemResult with defaults for missing fields
                    entry.setdefault("accumulated_cost", 0.0)
                    entry.setdefault("agent_state", "unknown")
                    entry.setdefault("git_patch", None)
                    self.completed[iid] = ProblemResult(**entry)
                self.failed = data.get("failed", {})
                logger.info(
                    "Resumed checkpoint: %d completed, %d failed",
                    len(self.completed),
                    len(self.failed),
                )
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning("Could not load checkpoint: %s", e)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "completed": {iid: r.to_dict() for iid, r in self.completed.items()},
            "failed": self.failed,
            "last_updated": datetime.now().isoformat(),
        }
        self.path.write_text(json.dumps(data, indent=2))

    def is_done(self, instance_id: str) -> bool:
        return instance_id in self.completed


# ---------------------------------------------------------------------------
# SWE-bench Verified data loading
# ---------------------------------------------------------------------------

# Module-level cache for the loaded dataset (avoids re-downloading)
_swebench_cache: Optional[Dict[str, Dict]] = None


# Eval-results helper is shared with run_real_swe_experiment.py — both
# import via the `scripts` package (proper package marker added at
# scripts/__init__.py). This keeps the sweagent import boundary clean:
# the SWE-agent runner pulls in sweagent at import time, so the OpenHands
# path must not import it transitively.
from scripts._swebench_eval_results import (  # noqa: E402
    load_swebench_eval_results as _load_eval_results_oh,
    summarise_group_resolved_source,
)


def _load_swebench_verified(dataset_name: str = "princeton-nlp/SWE-bench_Verified") -> Dict[str, Dict]:
    """Load and cache SWE-bench Verified dataset from HuggingFace.

    Returns a dict mapping instance_id -> full instance record.
    """
    global _swebench_cache
    if _swebench_cache is not None:
        return _swebench_cache

    from datasets import load_dataset
    logger.info("Loading SWE-bench Verified from %s ...", dataset_name)
    ds = load_dataset(dataset_name, split="test")
    _swebench_cache = {row["instance_id"]: dict(row) for row in ds}
    logger.info("Loaded %d SWE-bench Verified instances", len(_swebench_cache))
    return _swebench_cache


def select_test_instances(
    n_instances: int = 200,
    seed: int = 42,
    dataset_name: str = "princeton-nlp/SWE-bench_Verified",
) -> List[str]:
    """Select n_instances from SWE-bench Verified with a fixed seed.

    Uses random.sample with seed=42 so both OpenHands and SWE-agent runners
    get the exact same 200 problem IDs.
    """
    instances = _load_swebench_verified(dataset_name)
    all_ids = sorted(instances.keys())  # Sort for determinism before sampling
    rng = random.Random(seed)
    selected = rng.sample(all_ids, min(n_instances, len(all_ids)))
    return selected


def get_problem_statement(instance_id: str, dataset_name: str = "princeton-nlp/SWE-bench_Verified") -> str:
    """Get the problem statement for a SWE-bench Verified instance.

    Reads directly from the HuggingFace dataset — no trajectory files needed.
    """
    instances = _load_swebench_verified(dataset_name)
    instance = instances.get(instance_id)
    if instance is None:
        return f"Solve SWE-bench instance: {instance_id}"
    return instance.get("problem_statement", f"Solve SWE-bench instance: {instance_id}")


def extract_repo_from_instance_id(instance_id: str) -> Optional[str]:
    """Extract GitHub repo slug from instance ID.

    e.g., "pydantic__pydantic-1125" -> "pydantic/pydantic"
    """
    parts = instance_id.split("__")
    if len(parts) != 2:
        return None
    org = parts[0]
    repo_and_issue = parts[1]
    # repo name is everything before the last hyphen-number sequence
    # e.g., "pydantic-1125" -> "pydantic"
    # e.g., "python-stix2-133" -> "python-stix2"
    segments = repo_and_issue.rsplit("-", 1)
    if len(segments) == 2 and segments[1].isdigit():
        repo = segments[0]
    else:
        repo = repo_and_issue
    return f"{org}/{repo}"


# ---------------------------------------------------------------------------
# OpenHands runner
# ---------------------------------------------------------------------------

def build_openhands_config(run_config: RunConfig) -> OpenHandsConfig:
    """Build an OpenHandsConfig for a single run."""
    llm_config = LLMConfig(
        model=run_config.model,
        api_key=run_config.api_key,
        base_url=run_config.api_base if run_config.api_base else None,
        temperature=0.0,
        num_retries=3,
        retry_min_wait=5,
        retry_max_wait=30,
    )

    config = OpenHandsConfig(
        llms={"llm": llm_config},
        default_agent="CodeActAgent",
        runtime="docker",
        max_iterations=run_config.max_iterations,
        sandbox=SandboxConfig(
            base_container_image=run_config.base_container_image,
            timeout=run_config.timeout_seconds,
            use_host_network=True,  # Allow access to Neo4j on host
        ),
        workspace_base=str(run_config.output_dir / "workspace"),
    )

    return config


async def run_single_instance(
    instance_id: str,
    problem_statement: str,
    run_config: RunConfig,
    conversation_instructions: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a single SWE-bench instance through OpenHands.

    Args:
        instance_id: SWE-bench instance ID
        problem_statement: The issue text
        run_config: Experiment run configuration
        conversation_instructions: Optional extra instructions for the agent
            (used for memory context injection in the treatment group)

    Returns:
        Dict with: success, steps, tokens, cost, duration, exit_reason,
                   agent_state, error, git_patch
    """
    config = build_openhands_config(run_config)

    # Set up save path for trajectory
    traj_dir = run_config.output_dir / "trajectories"
    traj_dir.mkdir(parents=True, exist_ok=True)
    config.save_trajectory_path = str(traj_dir / f"{instance_id}.json")

    start_time = time.time()

    try:
        initial_action = MessageAction(content=problem_statement)

        state = await run_controller(
            config=config,
            initial_user_action=initial_action,
            headless_mode=True,
            conversation_instructions=conversation_instructions,
        )

        duration = time.time() - start_time

        if state is None:
            return {
                "success": False,
                "steps": 0,
                "tokens": 0,
                "cost": 0.0,
                "duration": duration,
                "exit_reason": "error",
                "agent_state": "error",
                "error": "run_controller returned None",
                "git_patch": None,
            }

        # Extract metrics
        metrics = state.metrics if hasattr(state, "metrics") else None
        accumulated_cost = metrics.accumulated_cost if metrics else 0.0
        token_usage = metrics.accumulated_token_usage if metrics else None
        total_tokens = 0
        if token_usage:
            total_tokens = (
                getattr(token_usage, "prompt_tokens", 0)
                + getattr(token_usage, "completion_tokens", 0)
            )

        n_steps = state.iteration or 0
        agent_state_str = state.agent_state.value if state.agent_state else "unknown"

        # Determine success: agent finished without error
        from openhands.core.schema.agent import AgentState as OHAgentState
        success = state.agent_state == OHAgentState.FINISHED

        exit_reason = "completed"
        if state.agent_state == OHAgentState.ERROR:
            exit_reason = "agent_error"
        elif state.agent_state == OHAgentState.STOPPED:
            exit_reason = "max_iterations"
        elif state.agent_state == OHAgentState.FINISHED:
            exit_reason = "agent_finished"

        return {
            "success": success,
            "steps": n_steps,
            "tokens": total_tokens,
            "cost": accumulated_cost,
            "duration": duration,
            "exit_reason": exit_reason,
            "agent_state": agent_state_str,
            "error": state.last_error if state.last_error else None,
            "git_patch": None,  # Could extract from runtime if needed
        }

    except asyncio.TimeoutError:
        return {
            "success": False,
            "steps": 0,
            "tokens": 0,
            "cost": 0.0,
            "duration": time.time() - start_time,
            "exit_reason": "timeout",
            "agent_state": "timeout",
            "error": f"Timeout after {run_config.timeout_seconds}s",
            "git_patch": None,
        }

    except Exception as e:
        logger.error("Error running instance %s: %s", instance_id, e, exc_info=True)
        return {
            "success": False,
            "steps": 0,
            "tokens": 0,
            "cost": 0.0,
            "duration": time.time() - start_time,
            "exit_reason": "error",
            "agent_state": "error",
            "error": str(e),
            "git_patch": None,
        }


# ---------------------------------------------------------------------------
# Experiment orchestrator
# ---------------------------------------------------------------------------

class RealOpenHandsExperiment:
    """Orchestrates the real A/B experiment using OpenHands."""

    def __init__(self, run_config: RunConfig, experiment_config: Optional[ExperimentConfig] = None):
        self.run_config = run_config
        self.experiment_config = experiment_config or get_config()
        self.graph: Optional[AgentMemoryGraph] = None
        self.checkpoint = Checkpoint(run_config.checkpoint_file)
        self.results: List[ProblemResult] = []

    def setup(self) -> bool:
        """Load split data, memory graph, and checkpoint."""
        # Load graph for treatment group
        self.graph = load_graph(self.experiment_config)
        if self.graph is None:
            logger.warning("No memory graph found. Treatment group will have no context.")

        # Load checkpoint if resuming
        if self.run_config.resume:
            self.checkpoint.load()
            # Restore completed results
            self.results = list(self.checkpoint.completed.values())
            logger.info("Restored %d completed results from checkpoint", len(self.results))

        return True

    def _generate_memory_context(self, instance_id: str) -> str:
        """Generate memory context for the treatment group."""
        if self.graph is None:
            return ""

        hooks = MemoryHooks(
            self.graph, self.experiment_config, ExperimentGroup.TREATMENT
        )
        state = MemoryAgentState(
            instance_id=instance_id,
            task_category="bug_fix",  # Default; could be inferred
        )
        context = hooks.pre_action_hook(state)
        if context.is_empty():
            return ""
        return context.to_prompt_injection()

    async def _run_single(
        self,
        instance_id: str,
        group: ExperimentGroup,
    ) -> ProblemResult:
        """Run a single instance and return ProblemResult."""
        # Build conversation instructions for treatment
        conversation_instructions = None
        n_interventions = 0
        n_warnings = 0

        if group == ExperimentGroup.TREATMENT:
            memory_text = self._generate_memory_context(instance_id)
            if memory_text:
                conversation_instructions = memory_text
                n_warnings = 1
                n_interventions = 1

        # Load problem statement from SWE-bench Verified
        problem_statement = get_problem_statement(
            instance_id, self.run_config.swebench_dataset
        )

        logger.info(
            "Running %s [%s] (instructions=%s)",
            instance_id,
            group.value,
            "yes" if conversation_instructions else "no",
        )

        # Run OpenHands
        result = await run_single_instance(
            instance_id=instance_id,
            problem_statement=problem_statement,
            run_config=self.run_config,
            conversation_instructions=conversation_instructions,
        )

        return ProblemResult(
            instance_id=instance_id,
            group=group.value,
            success=result["success"],
            total_steps=result["steps"],
            total_tokens=result["tokens"],
            accumulated_cost=result["cost"],
            duration_seconds=result["duration"],
            exit_reason=result["exit_reason"],
            agent_state=result["agent_state"],
            interventions=n_interventions,
            warnings_shown=n_warnings,
            error_message=result["error"],
            git_patch=result["git_patch"],
        )

    async def run(self) -> List[ProblemResult]:
        """Run the full experiment."""
        if not self.setup():
            return []

        # Load test instances from SWE-bench Verified
        instance_ids = select_test_instances(
            n_instances=self.run_config.n_instances,
            seed=self.run_config.seed,
            dataset_name=self.run_config.swebench_dataset,
        )
        logger.info("Selected %d SWE-bench Verified instances", len(instance_ids))

        # Assign groups
        assignments: List[tuple] = []  # (instance_id, group)
        for iid in instance_ids:
            group = assign_experiment_group(iid, self.run_config.seed)
            assignments.append((iid, group))

        control_count = sum(1 for _, g in assignments if g == ExperimentGroup.CONTROL)
        treatment_count = len(assignments) - control_count

        logger.info(
            "Experiment plan: %d total, %d control, %d treatment",
            len(assignments),
            control_count,
            treatment_count,
        )

        # Run each instance sequentially
        for i, (iid, group) in enumerate(assignments):
            # Skip if already completed
            if self.checkpoint.is_done(iid):
                logger.info(
                    "[%d/%d] Skipping %s (already completed)",
                    i + 1, len(assignments), iid,
                )
                continue

            logger.info(
                "[%d/%d] Running %s [%s]",
                i + 1, len(assignments), iid, group.value,
            )

            try:
                result = await self._run_single(iid, group)
                self.results.append(result)
                self.checkpoint.completed[iid] = result
                logger.info(
                    "  -> %s: success=%s, steps=%d, cost=$%.4f, time=%.1fs",
                    iid,
                    result.success,
                    result.total_steps,
                    result.accumulated_cost,
                    result.duration_seconds,
                )
            except Exception as e:
                logger.error("  -> %s: FAILED: %s", iid, e)
                self.checkpoint.failed[iid] = str(e)

            # Save checkpoint after each instance
            self.checkpoint.save()

        # Save final results
        self._save_results()
        self._print_summary()

        return self.results

    def _save_results(self):
        """Save experiment results."""
        output_dir = self.run_config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Save detailed results
        results_file = output_dir / f"openhands_results_{timestamp}.json"
        results_data = {
            "timestamp": timestamp,
            "config": {
                "model": self.run_config.model,
                "max_iterations": self.run_config.max_iterations,
                "timeout_seconds": self.run_config.timeout_seconds,
                "seed": self.run_config.seed,
                "n_instances": self.run_config.n_instances,
                "swebench_dataset": self.run_config.swebench_dataset,
                "base_container_image": self.run_config.base_container_image,
            },
            "results": [r.to_dict() for r in self.results],
        }
        results_file.write_text(json.dumps(results_data, indent=2))
        logger.info("Detailed results saved to %s", results_file)

        # Save analyst-compatible format
        analyst_file = output_dir / "openhands_results.json"
        analyst_data = self._to_analyst_format()
        analyst_file.write_text(json.dumps(analyst_data, indent=2))
        logger.info("Analyst-format results saved to %s", analyst_file)

    def _to_analyst_format(self) -> Dict:
        """Convert results to the analyst-expected format.

        Uses SWE-bench harness eval_results.json (if present in the output
        dir) for ground-truth resolved status. Falls back to the
        AgentState.FINISHED heuristic and logs a warning otherwise — pass@k
        derived from the heuristic alone is *not* ground truth.
        """
        from collections import defaultdict
        from experiments.ab_test.metrics import estimate_tokens

        eval_lookup = _load_eval_results_oh(self.run_config.output_dir)
        if eval_lookup is None:
            logger.warning(
                "No SWE-bench eval_results.json under %s — pass@k will use the "
                "AgentState.FINISHED heuristic. Run swebench.harness."
                "run_evaluation before trusting these numbers.",
                self.run_config.output_dir,
            )

        grouped: Dict[str, Dict[str, List[ProblemResult]]] = {
            "control": defaultdict(list),
            "treatment": defaultdict(list),
        }
        for r in self.results:
            grouped[r.group][r.instance_id].append(r)

        def _resolved(iid: str, entry: "ProblemResult") -> tuple[bool, str]:
            if eval_lookup is not None and iid in eval_lookup:
                return eval_lookup[iid], "swebench_eval"
            return entry.success, "heuristic"

        def _build(entries_by_id):
            out = []
            # Track per-instance source labels (one per instance, even
            # though each instance may have multiple attempts). The
            # shared summarise_group_resolved_source helper aggregates
            # these into the standard {label, counts} schema.
            instance_sources: List[str] = []
            for iid, entries in sorted(entries_by_id.items()):
                attempts = []
                sources = []
                for e in entries:
                    resolved, source = _resolved(iid, e)
                    attempts.append(resolved)
                    sources.append(source)
                tokens = [e.total_tokens or estimate_tokens(e.total_steps) for e in entries]
                # Use the dominant source label for this instance — usually
                # all attempts have the same source; if they ever differ
                # it's worth flagging via "mixed" rather than picking one
                # silently.
                source_label = sources[0] if len(set(sources)) == 1 else "mixed"
                instance_sources.append(source_label)
                out.append({
                    "id": iid,
                    "attempts": attempts,
                    "tokens": tokens,
                    "resolved_source": source_label,
                })
            return out, instance_sources

        control_problems, control_sources = _build(grouped["control"])
        treatment_problems, treatment_sources = _build(grouped["treatment"])

        control_summary = summarise_group_resolved_source(
            eval_lookup, control_sources,
        )
        treatment_summary = summarise_group_resolved_source(
            eval_lookup, treatment_sources,
        )

        # Matches the schema used by run_real_swe_experiment.collect_results
        # so downstream consumers can read `resolved_source[group]` uniformly
        # across SWE-agent and OpenHands result files. The `mixed` count is
        # mostly OpenHands-specific (an instance whose attempts came from
        # different sources); SWE-agent's analyst format has one attempt per
        # instance so its mixed count is always 0.
        return {
            "agent": "openhands",
            "n_problems": len(control_problems) + len(treatment_problems),
            "resolved_source": {
                "control": control_summary["label"],
                "treatment": treatment_summary["label"],
            },
            "resolved_source_counts": {
                "control": control_summary["counts"],
                "treatment": treatment_summary["counts"],
            },
            "control": {"problems": control_problems},
            "treatment": {"problems": treatment_problems},
        }

    def _print_summary(self):
        """Print human-readable summary."""
        control = [r for r in self.results if r.group == "control"]
        treatment = [r for r in self.results if r.group == "treatment"]

        def _stats(group_results):
            if not group_results:
                return {"n": 0, "sr": 0, "steps": 0, "cost": 0, "tokens": 0}
            n = len(group_results)
            return {
                "n": n,
                "sr": sum(1 for r in group_results if r.success) / n,
                "steps": sum(r.total_steps for r in group_results) / n,
                "cost": sum(r.accumulated_cost for r in group_results),
                "tokens": sum(r.total_tokens for r in group_results) / n,
            }

        cs = _stats(control)
        ts = _stats(treatment)

        print("\n" + "=" * 60)
        print("REAL OPENHANDS EXPERIMENT SUMMARY")
        print("=" * 60)
        print(f"\nControl ({cs['n']} instances):")
        print(f"  Success rate:  {cs['sr']:.1%}")
        print(f"  Avg steps:     {cs['steps']:.1f}")
        print(f"  Avg tokens:    {cs['tokens']:.0f}")
        print(f"  Total cost:    ${cs['cost']:.4f}")
        print(f"\nTreatment ({ts['n']} instances):")
        print(f"  Success rate:  {ts['sr']:.1%}")
        print(f"  Avg steps:     {ts['steps']:.1f}")
        print(f"  Avg tokens:    {ts['tokens']:.0f}")
        print(f"  Total cost:    ${ts['cost']:.4f}")
        total_interventions = sum(r.interventions for r in treatment)
        print(f"  Interventions: {total_interventions}")

        if cs["n"] > 0 and ts["n"] > 0:
            delta = ts["sr"] - cs["sr"]
            print(f"\nDelta (treatment - control):")
            print(f"  Success rate: {delta:+.1%}")

        total_cost = cs["cost"] + ts["cost"]
        print(f"\nTotal experiment cost: ${total_cost:.4f}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Run REAL OpenHands A/B experiment with Agent Memory"
    )
    parser.add_argument(
        "--n",
        type=int,
        default=200,
        help="Number of SWE-bench Verified instances to select (default: 200)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for group assignment (default: 42)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="claude-sonnet-4-20250514",
        help="LLM model name",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=30,
        help="Max agent iterations per problem (default: 30)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Sandbox timeout in seconds (default: 600)",
    )
    parser.add_argument(
        "--container-image",
        type=str,
        default="nikolaik/python-nodejs:python3.12-nodejs22",
        help="Base Docker container image",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from checkpoint",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory for results",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce output verbosity",
    )
    args = parser.parse_args()

    run_config = RunConfig(
        n_instances=args.n,
        seed=args.seed,
        model=args.model,
        max_iterations=args.max_iterations,
        timeout_seconds=args.timeout,
        base_container_image=args.container_image,
        resume=args.resume,
        verbose=not args.quiet,
    )
    if args.output:
        run_config.output_dir = args.output

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    experiment = RealOpenHandsExperiment(run_config=run_config)
    results = asyncio.run(experiment.run())

    if not results:
        print("\nExperiment produced no results.")
        sys.exit(1)


if __name__ == "__main__":
    main()
