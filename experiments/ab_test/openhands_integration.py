"""
OpenHands Integration for Agent Memory A/B Testing.

Provides hooks and integration points for injecting Agent Memory
into OpenHands agent execution.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum

from .config import ExperimentConfig, get_config
from .graph_builder import (
    AgentMemoryGraph,
    Methodology,
    load_graph,
    normalize_command,
    extract_action_with_target,
)
from .metrics import InterventionType, InterventionPoint


class ExperimentGroup(str, Enum):
    """A/B test group assignment."""
    CONTROL = "control"
    TREATMENT = "treatment"


@dataclass
class MemoryContext:
    """Context injected into agent prompt from memory."""
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    methodology_hint: Optional[str] = None
    loop_warning: Optional[str] = None

    def to_prompt_injection(self) -> str:
        """Generate prompt text to inject into agent context."""
        parts = []

        if self.loop_warning:
            parts.append(f"⚠️ LOOP WARNING: {self.loop_warning}")

        if self.warnings:
            parts.append("⚠️ WARNINGS:")
            for w in self.warnings:
                parts.append(f"  - {w}")

        if self.methodology_hint:
            parts.append(f"💡 SUGGESTION: {self.methodology_hint}")

        if self.suggestions:
            parts.append("💡 SUGGESTIONS:")
            for s in self.suggestions:
                parts.append(f"  - {s}")

        if not parts:
            return ""

        return "\n[Agent Memory]\n" + "\n".join(parts) + "\n[/Agent Memory]\n"

    def is_empty(self) -> bool:
        """Check if context has any content."""
        return not (self.warnings or self.suggestions or
                    self.methodology_hint or self.loop_warning)


@dataclass
class AgentState:
    """Current state of agent execution."""
    instance_id: str
    step_number: int = 0
    task_category: str = "bug_fix"
    recent_commands: List[str] = field(default_factory=list)
    recent_actions: List[str] = field(default_factory=list)
    errors_encountered: List[str] = field(default_factory=list)
    interventions: List[InterventionPoint] = field(default_factory=list)


class MemoryHooks:
    """
    Hooks for integrating Agent Memory into OpenHands execution.

    Usage:
        hooks = MemoryHooks(graph, config)

        # Before each action
        context = hooks.pre_action_hook(state, proposed_action)
        if context:
            inject_into_prompt(context.to_prompt_injection())

        # After each action
        hooks.post_action_hook(state, action, result)

        # After trajectory completes
        hooks.trajectory_complete_hook(trajectory, success)
    """

    def __init__(
        self,
        graph: AgentMemoryGraph,
        config: Optional[ExperimentConfig] = None,
        group: ExperimentGroup = ExperimentGroup.TREATMENT
    ):
        self.graph = graph
        self.config = config or get_config()
        self.group = group

        # Pre-compute lookup structures
        self.loop_patterns = {
            sig.command_pattern: sig
            for sig in graph.loop_signatures.values()
        }

        self.methodologies_by_category: Dict[str, List[Methodology]] = {}
        for m in graph.methodologies.values():
            cat = m.task_category
            if cat not in self.methodologies_by_category:
                self.methodologies_by_category[cat] = []
            self.methodologies_by_category[cat].append(m)

    def pre_action_hook(
        self,
        state: AgentState,
        proposed_action: Optional[str] = None
    ) -> MemoryContext:
        """
        Called before agent takes an action.

        Checks for:
        1. Loop patterns in recent history
        2. Applicable methodologies
        3. Known error patterns

        Returns context to inject into agent prompt.
        """
        if self.group == ExperimentGroup.CONTROL:
            return MemoryContext()  # No intervention for control group

        context = MemoryContext()

        # Check for loop warning
        loop_warning = self._check_loop_pattern(state, proposed_action)
        if loop_warning:
            context.loop_warning = loop_warning
            state.interventions.append(InterventionPoint(
                step_number=state.step_number,
                intervention_type=InterventionType.LOOP_WARNING,
                confidence=0.8,
                details=loop_warning,
                potential_steps_saved=2,
            ))

        # Check for methodology match (early in trajectory)
        if state.step_number <= 5:
            methodology = self._find_matching_methodology(state)
            if methodology:
                context.methodology_hint = (
                    f"For {state.task_category} tasks, consider: "
                    f"{' → '.join(methodology.action_sequence[:5])}"
                )
                state.interventions.append(InterventionPoint(
                    step_number=state.step_number,
                    intervention_type=InterventionType.METHODOLOGY_MATCH,
                    confidence=0.7,
                    details=f"Matched {methodology.methodology_id}",
                    potential_steps_saved=0,
                ))

        # Check for error pattern warnings
        error_warnings = self._check_error_patterns(state)
        context.warnings.extend(error_warnings)

        return context

    def post_action_hook(
        self,
        state: AgentState,
        action: str,
        result: str
    ):
        """
        Called after agent takes an action.

        Updates state with action history and error tracking.
        """
        # Extract action type
        action_type, target = extract_action_with_target(action)
        normalized = normalize_command(action)

        # Update state
        state.step_number += 1
        state.recent_commands.append(normalized)
        state.recent_actions.append(action_type)

        # Keep window limited
        window_size = 10
        if len(state.recent_commands) > window_size:
            state.recent_commands = state.recent_commands[-window_size:]
            state.recent_actions = state.recent_actions[-window_size:]

        # Check for errors in result
        if self._contains_error(result):
            error_type = self._extract_error_type(result)
            if error_type:
                state.errors_encountered.append(error_type)

    def trajectory_complete_hook(
        self,
        state: AgentState,
        trajectory: List[Dict],
        success: bool
    ):
        """
        Called after trajectory completes.

        Can be used to update the memory graph with new learnings.
        Currently a placeholder for future online learning.
        """
        # For now, just log completion
        # In a full implementation, this would call memory.learn()
        pass

    def _check_loop_pattern(
        self,
        state: AgentState,
        proposed_action: Optional[str] = None
    ) -> Optional[str]:
        """Check if agent is entering a loop pattern."""
        # Include proposed action in check
        commands = state.recent_commands.copy()
        if proposed_action:
            commands.append(normalize_command(proposed_action))

        if len(commands) < 3:
            return None

        # Check for consecutive identical commands
        last_cmd = commands[-1]
        consecutive = 1
        for i in range(len(commands) - 2, -1, -1):
            if commands[i] == last_cmd:
                consecutive += 1
            else:
                break

        min_repeat = self.config.loop_detection.min_repeat
        if consecutive >= min_repeat:
            # Get action type
            if proposed_action:
                action_type, _ = extract_action_with_target(proposed_action)
            elif state.recent_actions:
                action_type = state.recent_actions[-1]
            else:
                action_type = "action"

            # Check if this is a known problematic pattern
            if last_cmd in self.loop_patterns:
                sig = self.loop_patterns[last_cmd]
                return (
                    f"You've repeated the same {action_type} command {consecutive} times. "
                    f"This pattern has been seen in {sig.frequency} failed trajectories. "
                    f"Consider trying a different approach."
                )
            else:
                return (
                    f"You've repeated the same {action_type} command {consecutive} times. "
                    f"Consider trying a different approach to avoid getting stuck."
                )

        return None

    def _find_matching_methodology(self, state: AgentState) -> Optional[Methodology]:
        """Find a matching methodology for the current state."""
        candidates = self.methodologies_by_category.get(state.task_category, [])
        if not candidates:
            candidates = self.methodologies_by_category.get("bug_fix", [])

        if not candidates or len(state.recent_actions) < 2:
            return None

        # Find best matching methodology
        best_match = None
        best_score = 0

        for methodology in candidates:
            score = self._compute_sequence_match(
                state.recent_actions,
                methodology.action_sequence
            )
            if score > best_score and score >= 0.5:
                best_score = score
                best_match = methodology

        return best_match

    def _compute_sequence_match(
        self,
        current: List[str],
        target: List[str]
    ) -> float:
        """Compute match score between current and target action sequences."""
        if not current or not target:
            return 0.0

        matches = 0
        compare_len = min(len(current), len(target))

        for i in range(compare_len):
            if current[i] == target[i]:
                matches += 1

        return matches / compare_len if compare_len > 0 else 0.0

    def _check_error_patterns(self, state: AgentState) -> List[str]:
        """Check for known error patterns and generate warnings."""
        warnings = []

        # Check if we've seen the same error multiple times
        if len(state.errors_encountered) >= 2:
            last_error = state.errors_encountered[-1]
            error_count = sum(1 for e in state.errors_encountered[-5:] if e == last_error)

            if error_count >= 2:
                # Look up recovery suggestions
                if last_error in self.graph.error_patterns:
                    pattern = self.graph.error_patterns[last_error]
                    recovery = ", ".join(pattern.recovery_actions[:2])
                    warnings.append(
                        f"You've encountered {last_error} {error_count} times. "
                        f"Common recovery actions: {recovery}"
                    )

        return warnings

    def _contains_error(self, result: str) -> bool:
        """Check if result contains an error."""
        error_indicators = [
            "Error:", "Exception:", "Traceback",
            "error:", "failed", "FAILED",
        ]
        return any(ind in result for ind in error_indicators)

    def _extract_error_type(self, result: str) -> Optional[str]:
        """Extract error type from result."""
        import re
        patterns = [
            r'(TypeError)',
            r'(AttributeError)',
            r'(KeyError)',
            r'(ValueError)',
            r'(ImportError)',
            r'(SyntaxError)',
            r'(FileNotFoundError)',
            r'(NameError)',
        ]
        for pattern in patterns:
            match = re.search(pattern, result)
            if match:
                return match.group(1)
        return None


def assign_experiment_group(
    instance_id: str,
    seed: int = 42
) -> ExperimentGroup:
    """
    Deterministically assign instance to experiment group.

    Uses hash-based assignment for reproducibility.
    50/50 split between control and treatment.
    """
    # Create deterministic hash
    hash_input = f"{seed}:{instance_id}"
    hash_value = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)

    # 50/50 split
    if hash_value % 2 == 0:
        return ExperimentGroup.CONTROL
    else:
        return ExperimentGroup.TREATMENT


def create_memory_hooks(
    instance_id: str,
    config: Optional[ExperimentConfig] = None
) -> MemoryHooks:
    """
    Factory function to create MemoryHooks for an instance.

    Automatically assigns to experiment group and loads graph.
    """
    config = config or get_config()

    # Load graph
    graph = load_graph(config)
    if graph is None:
        raise RuntimeError("Agent Memory graph not found. Run graph_builder.py first.")

    # Assign group
    group = assign_experiment_group(instance_id)

    return MemoryHooks(graph, config, group)


# ============ OpenHands Integration Example ============

def example_openhands_integration():
    """
    Example showing how to integrate MemoryHooks with OpenHands.

    This is a template - actual integration depends on OpenHands API.
    """
    print("Example OpenHands Integration")
    print("=" * 50)

    # Create hooks for an instance
    instance_id = "example__repo-123"
    config = get_config()

    try:
        graph = load_graph(config)
        if graph is None:
            print("No graph found. Please run graph_builder.py first.")
            return
    except Exception as e:
        print(f"Error loading graph: {e}")
        return

    group = assign_experiment_group(instance_id)
    print(f"Instance {instance_id} assigned to: {group.value}")

    hooks = MemoryHooks(graph, config, group)

    # Simulate agent state
    state = AgentState(
        instance_id=instance_id,
        task_category="bug_fix"
    )

    # Simulate some actions
    actions = [
        'search_dir "test"',
        'search_dir "test"',
        'search_dir "test"',  # Trigger loop warning
    ]

    for action in actions:
        # Pre-action hook
        context = hooks.pre_action_hook(state, action)
        if not context.is_empty():
            print(f"\nStep {state.step_number} - Memory Context:")
            print(context.to_prompt_injection())

        # Simulate action execution
        result = "No matches found."

        # Post-action hook
        hooks.post_action_hook(state, action, result)

    print("\nIntervention Summary:")
    for intervention in state.interventions:
        print(f"  Step {intervention.step_number}: {intervention.intervention_type.value}")


if __name__ == "__main__":
    example_openhands_integration()
