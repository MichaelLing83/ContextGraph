#!/usr/bin/env python
"""
Runner script for OpenHands A/B experiment with Agent Memory.

Runs SWE-bench problems through OpenHands in two groups:
- Control: OpenHands with no memory injection
- Treatment: OpenHands with MemoryHooks context injected into system prompt

If OpenHands is not installed, runs in --dry-run mode to validate the
pipeline without executing any agent.
"""

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Ensure imports work when run directly
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from experiments.ab_test.config import ExperimentConfig, get_config
from experiments.ab_test.data_splitter import load_split
from experiments.ab_test.graph_builder import AgentMemoryGraph, load_graph
from experiments.ab_test.openhands_integration import (
    AgentState,
    ExperimentGroup,
    MemoryContext,
    MemoryHooks,
    assign_experiment_group,
)
from experiments.ab_test.collector import MetricsCollector, create_collector

# ---------------------------------------------------------------------------
# OpenHands availability
# ---------------------------------------------------------------------------

_OPENHANDS_AVAILABLE = False
try:
    import openhands  # noqa: F401
    _OPENHANDS_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class OpenHandsRunConfig:
    """Configuration for one OpenHands experiment run."""

    # Path to split file (overrides default config path)
    split_file: Optional[Path] = None

    # Subset of test IDs to run (None = all from split)
    n_instances: Optional[int] = None

    # LLM settings (read from .env by default)
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    model: str = "claude-sonnet-4-20250514"

    # OpenHands settings
    max_iterations: int = 30
    timeout_seconds: int = 600

    # Experiment
    seed: int = 42
    dry_run: bool = False
    verbose: bool = True

    # Output
    output_dir: Path = field(
        default_factory=lambda: _REPO_ROOT / "results" / "live_experiment"
    )

    def resolve_api_config(self):
        """Fill api_key / api_base from environment if not set."""
        if self.api_key is None:
            self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if self.api_base is None:
            self.api_base = os.environ.get("ANTHROPIC_API_BASE", "")


@dataclass
class ProblemResult:
    """Result of running a single SWE-bench problem."""
    instance_id: str
    group: str  # "control" or "treatment"
    success: bool
    total_steps: int
    total_tokens: int
    duration_seconds: float
    exit_reason: str  # "completed", "timeout", "error", "dry_run"
    interventions: int = 0
    warnings_shown: int = 0
    loops_detected: int = 0
    error_message: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "instance_id": self.instance_id,
            "group": self.group,
            "success": self.success,
            "total_steps": self.total_steps,
            "total_tokens": self.total_tokens,
            "duration_seconds": round(self.duration_seconds, 2),
            "exit_reason": self.exit_reason,
            "interventions": self.interventions,
            "warnings_shown": self.warnings_shown,
            "loops_detected": self.loops_detected,
            "error_message": self.error_message,
        }


# ---------------------------------------------------------------------------
# OpenHands wrapper (abstraction layer)
# ---------------------------------------------------------------------------

class OpenHandsAgent:
    """
    Wrapper around OpenHands agent execution.

    If OpenHands is installed, delegates to the real SDK.
    Otherwise, provides a stub that raises NotImplementedError.
    """

    def __init__(self, run_config: OpenHandsRunConfig):
        self.run_config = run_config

    def run_instance(
        self,
        instance_id: str,
        problem_statement: str,
        system_prompt_extra: str = "",
    ) -> Dict:
        """
        Run a single SWE-bench instance through OpenHands.

        Args:
            instance_id: SWE-bench instance ID (e.g. "django__django-12345")
            problem_statement: The problem/issue text
            system_prompt_extra: Additional text to append to the system prompt
                                (used for memory context injection)

        Returns:
            Dict with keys: success, steps, tokens, duration, exit_reason, error
        """
        if not _OPENHANDS_AVAILABLE:
            raise NotImplementedError(
                "OpenHands is not installed. Install with: pip install openhands-ai"
            )

        return self._run_with_openhands(
            instance_id, problem_statement, system_prompt_extra
        )

    def _run_with_openhands(
        self,
        instance_id: str,
        problem_statement: str,
        system_prompt_extra: str,
    ) -> Dict:
        """Run using the real OpenHands SDK."""
        # NOTE: This is the integration point that requires openhands-ai.
        # The exact API depends on the installed OpenHands version.
        # Below is the expected integration pattern based on the OpenHands SDK docs.
        try:
            from openhands.core.config import AppConfig, LLMConfig
            from openhands.core.main import create_runtime, run_controller
            from openhands.events.action import MessageAction

            # Configure LLM
            llm_config = LLMConfig(
                model=self.run_config.model,
                api_key=self.run_config.api_key,
                base_url=self.run_config.api_base,
            )

            # Build system prompt with optional memory context
            config = AppConfig(
                llm=llm_config,
                max_iterations=self.run_config.max_iterations,
            )

            if system_prompt_extra:
                config.default_agent = "CodeActAgent"

            start = time.time()
            runtime = create_runtime(config)
            state = run_controller(
                config=config,
                initial_user_action=MessageAction(
                    content=problem_statement + "\n" + system_prompt_extra
                ),
                runtime=runtime,
            )
            duration = time.time() - start

            # Extract results
            success = getattr(state, "success", False)
            n_steps = len(getattr(state, "history", []))
            metrics = getattr(state, "metrics", None)
            tokens = metrics.get("accumulated_cost", 0) if metrics else 0

            return {
                "success": success,
                "steps": n_steps,
                "tokens": int(tokens),
                "duration": duration,
                "exit_reason": "completed",
                "error": None,
            }

        except Exception as e:
            return {
                "success": False,
                "steps": 0,
                "tokens": 0,
                "duration": 0.0,
                "exit_reason": "error",
                "error": str(e),
            }


# ---------------------------------------------------------------------------
# Experiment orchestrator
# ---------------------------------------------------------------------------

class OpenHandsExperiment:
    """
    Orchestrates the full A/B experiment using OpenHands.

    For each test problem:
    1. Assign to control or treatment group
    2. For treatment: generate memory context via MemoryHooks
    3. Run OpenHands agent (or dry-run stub)
    4. Collect metrics
    """

    def __init__(
        self,
        experiment_config: Optional[ExperimentConfig] = None,
        run_config: Optional[OpenHandsRunConfig] = None,
    ):
        self.config = experiment_config or get_config()
        self.run_config = run_config or OpenHandsRunConfig()
        self.run_config.resolve_api_config()

        self.graph: Optional[AgentMemoryGraph] = None
        self.collector: Optional[MetricsCollector] = None
        self.results: List[ProblemResult] = []

    def setup(self) -> bool:
        """Load split data and memory graph. Returns True on success."""
        # Load split
        split = load_split(self.config)
        if split is None:
            print("Error: No split file found. Run data_splitter first.")
            return False
        self._split = split
        print(f"Loaded split: {len(split.test_ids)} test instances")

        # Load graph
        self.graph = load_graph(self.config)
        if self.graph is None:
            print("Error: No graph file found. Run graph_builder first.")
            return False
        print(
            f"Loaded graph: {len(self.graph.methodologies)} methodologies, "
            f"{len(self.graph.loop_signatures)} loop signatures"
        )

        # Collector
        self.collector = create_collector(self.config)

        return True

    def run(self) -> List[ProblemResult]:
        """Run the full experiment. Returns list of ProblemResult."""
        if not self.setup():
            return []

        test_ids = self._split.test_ids
        if self.run_config.n_instances is not None:
            test_ids = test_ids[: self.run_config.n_instances]

        # Assign groups
        control_ids = []
        treatment_ids = []
        for iid in test_ids:
            group = assign_experiment_group(iid, self.run_config.seed)
            if group == ExperimentGroup.CONTROL:
                control_ids.append(iid)
            else:
                treatment_ids.append(iid)

        print(f"\nExperiment plan:")
        print(f"  Total test instances: {len(test_ids)}")
        print(f"  Control group:   {len(control_ids)}")
        print(f"  Treatment group: {len(treatment_ids)}")
        print(f"  Dry run: {self.run_config.dry_run}")
        print(f"  OpenHands available: {_OPENHANDS_AVAILABLE}")

        if not _OPENHANDS_AVAILABLE and not self.run_config.dry_run:
            print(
                "\nWARNING: OpenHands not installed. Switching to --dry-run mode."
            )
            self.run_config.dry_run = True

        agent = OpenHandsAgent(self.run_config)

        # Run control group
        print(f"\n--- Control Group ({len(control_ids)} instances) ---")
        for i, iid in enumerate(control_ids):
            result = self._run_single(iid, ExperimentGroup.CONTROL, agent)
            self.results.append(result)
            if self.run_config.verbose and (i + 1) % 10 == 0:
                print(f"  Control: {i + 1}/{len(control_ids)}")

        # Run treatment group
        print(f"\n--- Treatment Group ({len(treatment_ids)} instances) ---")
        for i, iid in enumerate(treatment_ids):
            result = self._run_single(iid, ExperimentGroup.TREATMENT, agent)
            self.results.append(result)
            if self.run_config.verbose and (i + 1) % 10 == 0:
                print(f"  Treatment: {i + 1}/{len(treatment_ids)}")

        # Save results
        self._save_results()

        # Print summary
        self._print_summary()

        return self.results

    # ----- private helpers -----

    def _run_single(
        self,
        instance_id: str,
        group: ExperimentGroup,
        agent: OpenHandsAgent,
    ) -> ProblemResult:
        """Run a single instance (control or treatment)."""
        start = time.time()

        # Start collector tracking
        self.collector.start_trajectory(instance_id, group)

        # Build system prompt extra for treatment
        system_prompt_extra = ""
        hooks: Optional[MemoryHooks] = None
        state: Optional[AgentState] = None

        if group == ExperimentGroup.TREATMENT and self.graph is not None:
            hooks = MemoryHooks(self.graph, self.config, group)
            meta = self._split.test_metadata.get(instance_id, {})
            task_category = meta.get("task_type", "bug_fix")
            state = AgentState(
                instance_id=instance_id,
                task_category=task_category,
            )
            # Generate initial memory context
            context = hooks.pre_action_hook(state)
            if not context.is_empty():
                system_prompt_extra = context.to_prompt_injection()
                self.collector.record_warning(instance_id)

        # Get problem statement
        problem_statement = self._get_problem_statement(instance_id)

        # Execute
        if self.run_config.dry_run:
            result_dict = self._dry_run_stub(instance_id)
        else:
            try:
                result_dict = agent.run_instance(
                    instance_id,
                    problem_statement,
                    system_prompt_extra=system_prompt_extra,
                )
            except NotImplementedError:
                result_dict = self._dry_run_stub(instance_id)

        duration = time.time() - start

        # Record steps in collector
        for _ in range(result_dict.get("steps", 0)):
            self.collector.record_step(instance_id, "action", True)

        # Complete trajectory
        success = result_dict.get("success", False)
        self.collector.complete_trajectory(
            instance_id, success, result_dict.get("exit_reason", "completed")
        )

        return ProblemResult(
            instance_id=instance_id,
            group=group.value,
            success=success,
            total_steps=result_dict.get("steps", 0),
            total_tokens=result_dict.get("tokens", 0),
            duration_seconds=duration,
            exit_reason=result_dict.get("exit_reason", "completed"),
            interventions=len(state.interventions) if state else 0,
            warnings_shown=1 if system_prompt_extra else 0,
            error_message=result_dict.get("error"),
        )

    def _dry_run_stub(self, instance_id: str) -> Dict:
        """Return a placeholder result for dry-run mode."""
        return {
            "success": False,
            "steps": 0,
            "tokens": 0,
            "duration": 0.0,
            "exit_reason": "dry_run",
            "error": None,
        }

    def _to_analyst_format(self) -> Dict:
        """
        Convert results to the analyst-expected format for cross-agent comparison.

        Format::

            {
                "agent": "openhands",
                "n_problems": 200,
                "control":   {"problems": [{"id": ..., "attempts": [...], "tokens": [...]}]},
                "treatment": {"problems": [{"id": ..., "attempts": [...], "tokens": [...]}]}
            }
        """
        from collections import defaultdict
        from experiments.ab_test.metrics import estimate_tokens as _est

        grouped: Dict[str, Dict[str, List[ProblemResult]]] = {
            "control": defaultdict(list),
            "treatment": defaultdict(list),
        }
        for r in self.results:
            grouped[r.group][r.instance_id].append(r)

        def _build(entries_by_id: Dict[str, List[ProblemResult]]) -> List[Dict]:
            out = []
            for iid, entries in sorted(entries_by_id.items()):
                attempts = [e.success for e in entries]
                tokens = [e.total_tokens or _est(e.total_steps) for e in entries]
                out.append({"id": iid, "attempts": attempts, "tokens": tokens})
            return out

        control_problems = _build(grouped["control"])
        treatment_problems = _build(grouped["treatment"])
        return {
            "agent": "openhands",
            "n_problems": len(control_problems) + len(treatment_problems),
            "control": {"problems": control_problems},
            "treatment": {"problems": treatment_problems},
        }

    def _get_problem_statement(self, instance_id: str) -> str:
        """Load the problem statement for an instance."""
        # Try loading from trajectory file
        traj_file = self.config.paths.trajectories_dir / f"{instance_id}.json"
        if traj_file.exists():
            try:
                with open(traj_file, "r") as f:
                    data = json.load(f)
                trajectory = data.get("trajectory", [])
                for turn in trajectory:
                    if turn.get("role") == "user":
                        text = turn.get("text", "")
                        if "ISSUE:" in text or len(text) > 100:
                            return text
            except (json.JSONDecodeError, IOError):
                pass

        return f"Solve SWE-bench instance: {instance_id}"

    def _save_results(self):
        """Save experiment results to output directory."""
        output_dir = self.run_config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Save per-problem results
        results_file = output_dir / f"openhands_results_{timestamp}.json"
        results_data = {
            "timestamp": timestamp,
            "config": {
                "model": self.run_config.model,
                "max_iterations": self.run_config.max_iterations,
                "seed": self.run_config.seed,
                "dry_run": self.run_config.dry_run,
                "openhands_available": _OPENHANDS_AVAILABLE,
                "n_instances": self.run_config.n_instances,
            },
            "results": [r.to_dict() for r in self.results],
        }
        with open(results_file, "w") as f:
            json.dump(results_data, f, indent=2)
        print(f"\nResults saved to {results_file}")

        # Save analyst-compatible format (openhands_results.json)
        analyst_file = output_dir / "openhands_results.json"
        analyst_data = self._to_analyst_format()
        with open(analyst_file, "w") as f:
            json.dump(analyst_data, f, indent=2)
        print(f"Analyst-format results saved to {analyst_file}")

        # Also save via collector
        self.collector.save_results(output_dir)

    def _print_summary(self):
        """Print a human-readable summary."""
        control = [r for r in self.results if r.group == "control"]
        treatment = [r for r in self.results if r.group == "treatment"]

        def _stats(group: List[ProblemResult]) -> Dict:
            if not group:
                return {"count": 0, "success_rate": 0, "avg_steps": 0}
            n = len(group)
            n_success = sum(1 for r in group if r.success)
            avg_steps = sum(r.total_steps for r in group) / n if n else 0
            avg_tokens = sum(r.total_tokens for r in group) / n if n else 0
            return {
                "count": n,
                "success_rate": n_success / n if n else 0,
                "avg_steps": avg_steps,
                "avg_tokens": avg_tokens,
            }

        cs = _stats(control)
        ts = _stats(treatment)

        print("\n" + "=" * 60)
        print("OPENHANDS EXPERIMENT SUMMARY")
        print("=" * 60)
        print(f"\nControl ({cs['count']} instances):")
        print(f"  Success rate:  {cs['success_rate']:.1%}")
        print(f"  Avg steps:     {cs['avg_steps']:.1f}")
        print(f"  Avg tokens:    {cs['avg_tokens']:.0f}")
        print(f"\nTreatment ({ts['count']} instances):")
        print(f"  Success rate:  {ts['success_rate']:.1%}")
        print(f"  Avg steps:     {ts['avg_steps']:.1f}")
        print(f"  Avg tokens:    {ts['avg_tokens']:.0f}")
        total_interventions = sum(r.interventions for r in treatment)
        print(f"  Interventions: {total_interventions}")

        if cs["count"] > 0 and ts["count"] > 0:
            delta = ts["success_rate"] - cs["success_rate"]
            print(f"\nDelta (treatment - control):")
            print(f"  Success rate: {delta:+.1%}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Run OpenHands A/B experiment with Agent Memory"
    )
    parser.add_argument(
        "--n",
        type=int,
        default=None,
        help="Number of test instances to run (default: all)",
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
        help="Timeout per problem in seconds (default: 600)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate pipeline without running agents",
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

    run_config = OpenHandsRunConfig(
        n_instances=args.n,
        seed=args.seed,
        model=args.model,
        max_iterations=args.max_iterations,
        timeout_seconds=args.timeout,
        dry_run=args.dry_run,
        verbose=not args.quiet,
    )
    if args.output:
        run_config.output_dir = args.output

    experiment = OpenHandsExperiment(run_config=run_config)
    results = experiment.run()

    if not results:
        print("\nExperiment produced no results.")
        sys.exit(1)


if __name__ == "__main__":
    main()
