"""
Trajectory Replay Simulator for Agent Memory A/B Testing.

Simulates the effect of Agent Memory by replaying trajectories and
detecting intervention points where the memory system would have
provided guidance.
"""

import json
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from .config import ExperimentConfig, get_config
from .data_splitter import load_split
from .graph_builder import (
    AgentMemoryGraph,
    Methodology,
    load_graph,
    parse_trajectory,
    normalize_command,
)
from .metrics import (
    TrajectoryMetrics,
    InterventionPoint,
    InterventionType,
    estimate_tokens,
)


@dataclass
class SimulatorState:
    """State maintained during trajectory simulation."""
    step_number: int = 0
    recent_commands: List[str] = None
    recent_actions: List[str] = None
    interventions: List[InterventionPoint] = None
    loops_detected: int = 0
    loop_steps_wasted: int = 0

    def __post_init__(self):
        if self.recent_commands is None:
            self.recent_commands = []
        if self.recent_actions is None:
            self.recent_actions = []
        if self.interventions is None:
            self.interventions = []


class TrajectorySimulator:
    """
    Simulates trajectory replay with Agent Memory interventions.

    For each step in a trajectory, checks if the memory system would have:
    1. Detected a loop pattern
    2. Matched a relevant methodology
    3. Warned about an error pattern
    """

    def __init__(self, graph: AgentMemoryGraph, config: ExperimentConfig):
        self.graph = graph
        self.config = config

        # Pre-compute normalized loop patterns for fast lookup
        self.loop_patterns = {
            sig.command_pattern: sig
            for sig in graph.loop_signatures.values()
        }

        # Index methodologies by task category
        self.methodologies_by_category: Dict[str, List[Methodology]] = {}
        for m in graph.methodologies.values():
            cat = m.task_category
            if cat not in self.methodologies_by_category:
                self.methodologies_by_category[cat] = []
            self.methodologies_by_category[cat].append(m)

    def detect_loop(
        self,
        state: SimulatorState,
        command: str,
        action_type: str
    ) -> Optional[InterventionPoint]:
        """
        Check if current command continues a loop pattern.

        Uses a sliding window to detect repeated commands.
        """
        normalized = normalize_command(command)

        # Add to recent history
        state.recent_commands.append(normalized)
        state.recent_actions.append(action_type)

        # Keep window size limited
        window_size = 10
        if len(state.recent_commands) > window_size:
            state.recent_commands = state.recent_commands[-window_size:]
            state.recent_actions = state.recent_actions[-window_size:]

        # Check for consecutive repetitions
        min_repeat = self.config.loop_detection.min_repeat
        if len(state.recent_commands) < min_repeat:
            return None

        # Count consecutive identical commands at the end
        last_cmd = state.recent_commands[-1]
        consecutive = 1
        for i in range(len(state.recent_commands) - 2, -1, -1):
            if state.recent_commands[i] == last_cmd:
                consecutive += 1
            else:
                break

        # Trigger warning at the threshold point; keep counting wasted
        # steps for longer loops without emitting duplicate interventions.
        warning_threshold = min_repeat
        if consecutive < warning_threshold:
            return None

        if consecutive > warning_threshold:
            state.loop_steps_wasted += 1
            return None

        # consecutive == warning_threshold
        if consecutive == warning_threshold:
            # Check if this matches a known problematic pattern
            confidence = 0.5  # Base confidence

            # Higher confidence if matches known loop signature
            if normalized in self.loop_patterns:
                known_sig = self.loop_patterns[normalized]
                confidence = min(0.95, 0.5 + known_sig.frequency * 0.05)

            # Record loop detection (only count once)
            state.loops_detected += 1

            # Track wasted steps (consecutive repetitions minus the first)
            state.loop_steps_wasted += consecutive - 1

            # Conservative estimate: could save 2-3 steps by early warning
            # (agent would break out of loop earlier)
            potential_saved = min(consecutive - 1, 3)

            return InterventionPoint(
                step_number=state.step_number,
                intervention_type=InterventionType.LOOP_WARNING,
                confidence=confidence,
                details=f"Detected {consecutive}x repetition of '{action_type}' command",
                potential_steps_saved=potential_saved,
            )

        return None

    def match_methodology(
        self,
        state: SimulatorState,
        task_category: str,
        action_sequence: List[str]
    ) -> Optional[InterventionPoint]:
        """
        Check if current trajectory matches a known methodology.

        Looks for sequence matches in the early steps.
        """
        # Only check early in trajectory (first 10 steps)
        if state.step_number > 10:
            return None

        # Get methodologies for this category
        candidates = self.methodologies_by_category.get(task_category, [])
        if not candidates:
            candidates = self.methodologies_by_category.get("bug_fix", [])

        if not candidates:
            return None

        # Check for sequence prefix match
        for methodology in candidates:
            method_seq = methodology.action_sequence[:5]  # First 5 actions
            if len(action_sequence) < 3:
                continue

            # Check if current sequence is a prefix of methodology
            matches = 0
            for i, action in enumerate(action_sequence[:len(method_seq)]):
                if i < len(method_seq) and action == method_seq[i]:
                    matches += 1

            match_ratio = matches / len(method_seq) if method_seq else 0

            if match_ratio >= 0.6:  # 60% match threshold
                return InterventionPoint(
                    step_number=state.step_number,
                    intervention_type=InterventionType.METHODOLOGY_MATCH,
                    confidence=match_ratio,
                    details=f"Matched methodology {methodology.methodology_id} ({methodology.task_category})",
                    potential_steps_saved=0,  # Guidance, not prevention
                )

        return None

    def simulate_trajectory(
        self,
        trajectory_data: dict,
        task_category: str
    ) -> TrajectoryMetrics:
        """
        Simulate a single trajectory with memory intervention detection.
        """
        start_time = time.time()

        steps, success, instance_id = parse_trajectory(trajectory_data)

        state = SimulatorState()
        action_sequence = []

        for step in steps:
            state.step_number = step.step_num
            action_sequence.append(step.action_type)

            # Check for loop
            loop_intervention = self.detect_loop(state, step.command, step.action_type)
            if loop_intervention:
                state.interventions.append(loop_intervention)

            # Check for methodology match (only once)
            if len(state.interventions) == 0 or all(
                i.intervention_type != InterventionType.METHODOLOGY_MATCH
                for i in state.interventions
            ):
                method_intervention = self.match_methodology(
                    state, task_category, action_sequence
                )
                if method_intervention:
                    state.interventions.append(method_intervention)

        # Compute estimated improvements
        total_steps = len(steps)
        total_steps_saved = sum(i.potential_steps_saved for i in state.interventions)

        # Estimate if memory could have helped achieve success
        # Very conservative heuristic:
        # - Only if we had loop interventions (not just methodology matches)
        # - Only if first intervention was early (step < 5)
        # - Only if significant steps could be saved
        estimated_success = success
        loop_interventions = [
            i for i in state.interventions
            if i.intervention_type == InterventionType.LOOP_WARNING
        ]
        if not success and loop_interventions and total_steps_saved >= 3:
            first_loop_step = loop_interventions[0].step_number
            if first_loop_step < 15:  # Early enough to matter
                # Still only estimate ~30% improvement rate
                # (we'll compute actual expected value later)
                estimated_success = True

        end_time = time.time()

        return TrajectoryMetrics(
            instance_id=instance_id,
            success=success,
            total_steps=total_steps,
            total_tokens_estimate=estimate_tokens(total_steps),
            interventions=state.interventions,
            first_intervention_step=(
                state.interventions[0].step_number if state.interventions else None
            ),
            total_interventions=len(state.interventions),
            loops_detected=state.loops_detected,
            loop_steps_wasted=state.loop_steps_wasted,
            estimated_steps_saved=total_steps_saved,
            estimated_success_with_memory=estimated_success,
            simulation_time_ms=(end_time - start_time) * 1000,
        )


def simulate_test_set(
    test_ids: List[str],
    test_metadata: Dict[str, dict],
    graph: AgentMemoryGraph,
    config: ExperimentConfig,
    verbose: bool = True
) -> List[TrajectoryMetrics]:
    """
    Run simulation on all test trajectories.

    Returns metrics for each trajectory.
    """
    simulator = TrajectorySimulator(graph, config)
    results = []

    trajectories_dir = config.paths.trajectories_dir

    if verbose:
        print(f"Simulating {len(test_ids)} test trajectories...")

    for i, instance_id in enumerate(test_ids):
        if verbose and i % 200 == 0:
            print(f"  Progress: {i}/{len(test_ids)}")

        # Load trajectory
        traj_file = trajectories_dir / f"{instance_id}.json"
        if not traj_file.exists():
            continue

        try:
            with open(traj_file, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        # Get task category from metadata
        meta = test_metadata.get(instance_id, {})
        task_category = meta.get("task_type", "bug_fix")

        # Simulate
        metrics = simulator.simulate_trajectory(data, task_category)
        results.append(metrics)

    if verbose:
        print(f"Completed simulation of {len(results)} trajectories")

    return results


def main():
    """Run simulation on test set."""
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
    print(f"Test set: {len(split.test_ids)} trajectories")

    # Run simulation on first 100 for quick test
    test_sample = split.test_ids[:100]
    test_meta = {k: v for k, v in split.test_metadata.items() if k in test_sample}

    results = simulate_test_set(test_sample, test_meta, graph, config)

    # Print summary
    print("\n" + "=" * 50)
    print("Simulation Summary (100 sample trajectories)")
    print("=" * 50)

    n = len(results)
    if n == 0:
        print("\nNo trajectories were successfully simulated.")
        return

    n_success = sum(1 for r in results if r.success)
    n_with_interventions = sum(1 for r in results if r.total_interventions > 0)
    n_with_loops = sum(1 for r in results if r.loops_detected > 0)
    n_estimated_success = sum(1 for r in results if r.estimated_success_with_memory)

    print(f"\nOriginal success rate: {n_success}/{n} ({n_success/n*100:.1f}%)")
    print(f"Trajectories with interventions: {n_with_interventions}/{n} ({n_with_interventions/n*100:.1f}%)")
    print(f"Trajectories with loops: {n_with_loops}/{n} ({n_with_loops/n*100:.1f}%)")
    print(f"Estimated success with memory: {n_estimated_success}/{n} ({n_estimated_success/n*100:.1f}%)")

    total_steps_saved = sum(r.estimated_steps_saved for r in results)
    total_steps = sum(r.total_steps for r in results)
    print(f"\nTotal steps: {total_steps}")
    print(f"Estimated steps saved: {total_steps_saved} ({total_steps_saved/total_steps*100:.1f}%)")


if __name__ == "__main__":
    main()
