"""
Experiment Runner for Agent Memory A/B Testing.

Orchestrates running the A/B experiment on test trajectories,
coordinating between memory hooks, metrics collection, and
(optionally) live OpenHands execution.
"""

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

if __package__ in (None, ""):
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from experiments.ab_test.config import ExperimentConfig, get_config
    from experiments.ab_test.data_splitter import load_split, SplitResult
    from experiments.ab_test.graph_builder import (
        AgentMemoryGraph,
        load_graph,
        parse_trajectory,
        normalize_command,
    )
    from experiments.ab_test.openhands_integration import (
        MemoryHooks,
        AgentState,
        ExperimentGroup,
        assign_experiment_group,
    )
    from experiments.ab_test.collector import MetricsCollector, create_collector
else:
    from .config import ExperimentConfig, get_config
    from .data_splitter import load_split, SplitResult
    from .graph_builder import (
        AgentMemoryGraph,
        load_graph,
        parse_trajectory,
        normalize_command,
    )
    from .openhands_integration import (
        MemoryHooks,
        AgentState,
        ExperimentGroup,
        assign_experiment_group,
    )
    from .collector import MetricsCollector, create_collector


def _detect_loop_lengths(commands: List[str], min_repeat: int) -> List[int]:
    """Return wasted-step counts for each consecutive repeated-command loop."""
    if len(commands) < min_repeat:
        return []

    loop_lengths: List[int] = []
    i = 0
    while i < len(commands):
        count = 1
        while i + count < len(commands) and commands[i + count] == commands[i]:
            count += 1
        if count >= min_repeat:
            loop_lengths.append(count - 1)
        i += count
    return loop_lengths


@dataclass
class RunConfig:
    """Configuration for experiment run."""
    # Number of instances to run (None = all)
    n_instances: Optional[int] = None

    # Run mode
    mode: str = "simulation"  # "simulation" or "live"

    # Batch size for progress reporting
    batch_size: int = 100

    # Random seed for reproducibility
    seed: int = 42

    # Verbose output
    verbose: bool = True

    # Save intermediate results
    save_intermediate: bool = True
    intermediate_interval: int = 500


class ExperimentRunner:
    """
    Runs the A/B experiment on test trajectories.

    Supports two modes:
    1. Simulation mode: Replays stored trajectories with memory hooks
    2. Live mode: Runs actual OpenHands agents (requires OpenHands installation)
    """

    def __init__(
        self,
        config: Optional[ExperimentConfig] = None,
        run_config: Optional[RunConfig] = None
    ):
        self.config = config or get_config()
        self.run_config = run_config or RunConfig()

        # Load data
        self.split: Optional[SplitResult] = None
        self.graph: Optional[AgentMemoryGraph] = None

        # Metrics collector
        self.collector: Optional[MetricsCollector] = None

        # Progress tracking
        self.processed = 0
        self.start_time: Optional[datetime] = None

    def setup(self) -> bool:
        """Load required data and initialize collector."""
        print("Setting up experiment...")

        # Load split
        self.split = load_split(self.config)
        if self.split is None:
            print("Error: No split found. Run data_splitter.py first.")
            return False

        print(f"Loaded {len(self.split.test_ids)} test instances")

        # Load graph
        self.graph = load_graph(self.config)
        if self.graph is None:
            print("Error: No graph found. Run graph_builder.py first.")
            return False

        print(f"Loaded graph with {len(self.graph.methodologies)} methodologies")

        # Initialize collector
        self.collector = create_collector(self.config)

        return True

    def run(self) -> Dict:
        """Run the experiment and return results."""
        if not self.setup():
            return {}

        self.start_time = datetime.now()

        # Get instances to run
        test_ids = self.split.test_ids
        if self.run_config.n_instances:
            test_ids = test_ids[:self.run_config.n_instances]

        print(f"\nRunning experiment on {len(test_ids)} instances...")
        print(f"Mode: {self.run_config.mode}")

        # Assign groups
        control_ids = []
        treatment_ids = []
        for instance_id in test_ids:
            group = assign_experiment_group(instance_id, self.run_config.seed)
            if group == ExperimentGroup.CONTROL:
                control_ids.append(instance_id)
            else:
                treatment_ids.append(instance_id)

        print(f"Control group: {len(control_ids)} instances")
        print(f"Treatment group: {len(treatment_ids)} instances")

        # Run based on mode
        if self.run_config.mode == "simulation":
            self._run_simulation(control_ids, treatment_ids)
        else:
            self._run_live(control_ids, treatment_ids)

        # Get final results
        results = self.collector.get_experiment_summary()

        # Save results
        self.collector.save_results()

        return results

    def _run_simulation(
        self,
        control_ids: List[str],
        treatment_ids: List[str]
    ):
        """Run simulation on stored trajectories."""
        trajectories_dir = self.config.paths.trajectories_dir

        # Process control group
        print("\n--- Processing Control Group ---")
        for i, instance_id in enumerate(control_ids):
            self._simulate_trajectory(
                instance_id,
                ExperimentGroup.CONTROL,
                trajectories_dir
            )

            if self.run_config.verbose and (i + 1) % self.run_config.batch_size == 0:
                self._print_progress(i + 1, len(control_ids), "Control")

            if self.run_config.save_intermediate:
                if (i + 1) % self.run_config.intermediate_interval == 0:
                    self.collector.save_results()

        # Process treatment group
        print("\n--- Processing Treatment Group ---")
        for i, instance_id in enumerate(treatment_ids):
            self._simulate_trajectory(
                instance_id,
                ExperimentGroup.TREATMENT,
                trajectories_dir
            )

            if self.run_config.verbose and (i + 1) % self.run_config.batch_size == 0:
                self._print_progress(i + 1, len(treatment_ids), "Treatment")

            if self.run_config.save_intermediate:
                if (i + 1) % self.run_config.intermediate_interval == 0:
                    self.collector.save_results()

    def _simulate_trajectory(
        self,
        instance_id: str,
        group: ExperimentGroup,
        trajectories_dir: Path
    ):
        """Simulate a single trajectory with or without memory hooks."""
        # Load trajectory
        traj_file = trajectories_dir / f"{instance_id}.json"
        if not traj_file.exists():
            return

        try:
            with open(traj_file, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            return

        # Parse trajectory
        steps, success, _ = parse_trajectory(data)
        if len(steps) < 3:
            return

        # Get task category
        meta = self.split.test_metadata.get(instance_id, {})
        task_category = meta.get("task_type", "bug_fix")

        # Record loop metrics for both groups from replayed trajectory.
        normalized_commands = [normalize_command(step.command) for step in steps]
        loop_lengths = _detect_loop_lengths(
            normalized_commands,
            self.config.loop_detection.min_repeat,
        )

        # Start tracking
        self.collector.start_trajectory(instance_id, group)
        for loop_length in loop_lengths:
            self.collector.record_loop(instance_id, loop_length)

        # Create memory hooks for treatment group
        hooks = None
        state = None
        if group == ExperimentGroup.TREATMENT:
            hooks = MemoryHooks(self.graph, self.config, group)
            state = AgentState(
                instance_id=instance_id,
                task_category=task_category
            )

        # Simulate each step
        for step in steps:
            step_success = True  # Assume success unless error

            if hooks and state:
                state.step_number = step.step_num

                # Treatment: Check for interventions
                prev_intervention_count = len(state.interventions)
                context = hooks.pre_action_hook(state, step.command)

                if not context.is_empty():
                    self.collector.record_warning(instance_id)

                for intervention in state.interventions[prev_intervention_count:]:
                    self.collector.record_intervention(instance_id, intervention)

                hooks.post_action_hook(state, step.command, "simulated result")

            self.collector.record_step(instance_id, step.command, step_success)

        # Complete trajectory
        self.collector.complete_trajectory(instance_id, success)

    def _run_live(
        self,
        control_ids: List[str],
        treatment_ids: List[str]
    ):
        """
        Run live OpenHands agents.

        This is a placeholder - actual implementation requires OpenHands integration.
        """
        print("Live mode requires OpenHands installation.")
        print("Using simulation mode as fallback.")
        self._run_simulation(control_ids, treatment_ids)

    def _print_progress(self, current: int, total: int, group: str):
        """Print progress update."""
        elapsed = (datetime.now() - self.start_time).total_seconds()
        rate = current / elapsed if elapsed > 0 else 0

        print(f"  {group}: {current}/{total} ({current/total*100:.1f}%) "
              f"- {rate:.1f} instances/sec")


def run_experiment(
    n_instances: Optional[int] = None,
    mode: str = "simulation",
    verbose: bool = True
) -> Dict:
    """
    Convenience function to run the experiment.

    Args:
        n_instances: Number of instances to run (None = all)
        mode: "simulation" or "live"
        verbose: Print progress

    Returns:
        Experiment results dictionary
    """
    run_config = RunConfig(
        n_instances=n_instances,
        mode=mode,
        verbose=verbose
    )

    runner = ExperimentRunner(run_config=run_config)
    return runner.run()


def run_pilot(n_instances: int = 50) -> Dict:
    """Run a small pilot experiment."""
    print("=" * 60)
    print("PILOT EXPERIMENT")
    print("=" * 60)

    return run_experiment(n_instances=n_instances, verbose=True)


def main():
    """Run the full experiment."""
    import argparse

    parser = argparse.ArgumentParser(description="Run Agent Memory A/B Experiment")
    parser.add_argument(
        "--n", type=int, default=None,
        help="Number of instances to run (default: all)"
    )
    parser.add_argument(
        "--mode", choices=["simulation", "live"], default="simulation",
        help="Run mode (default: simulation)"
    )
    parser.add_argument(
        "--pilot", action="store_true",
        help="Run pilot with 50 instances"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Reduce output verbosity"
    )

    args = parser.parse_args()

    if args.pilot:
        results = run_pilot(50)
    else:
        results = run_experiment(
            n_instances=args.n,
            mode=args.mode,
            verbose=not args.quiet
        )
    if not results:
        print("\nExperiment setup failed; no results to report.")
        sys.exit(1)

    # Print final results
    print("\n" + "=" * 60)
    print("EXPERIMENT RESULTS")
    print("=" * 60)

    print(f"\nControl Group:")
    print(f"  Instances: {results['control']['count']}")
    print(f"  Success Rate: {results['control']['success_rate']:.1%}")
    print(f"  Avg Steps: {results['control']['avg_steps']:.1f}")

    print(f"\nTreatment Group:")
    print(f"  Instances: {results['treatment']['count']}")
    print(f"  Success Rate: {results['treatment']['success_rate']:.1%}")
    print(f"  Avg Steps: {results['treatment']['avg_steps']:.1f}")
    print(f"  Intervention Rate: {results['treatment']['intervention_rate']:.1%}")

    print(f"\nDeltas (Treatment - Control):")
    print(f"  Success Rate: {results['deltas']['success_rate_delta']:+.1%}")
    print(f"  Token Reduction: {results['deltas']['token_reduction_pct']:.1f}%")
    print(f"  Loop Reduction: {results['deltas']['loop_reduction_pct']:.1f}%")


if __name__ == "__main__":
    main()
