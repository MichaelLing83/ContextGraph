"""
Graph Builder for Agent Memory A/B Testing Experiment.

Builds the Agent Memory graph from training trajectories, extracting:
- Methodologies (situation-strategy-outcome patterns)
- Loop signatures for detection
- Error patterns and statistics
"""

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
import hashlib

from .config import ExperimentConfig, get_config
from .data_splitter import (
    load_split,
    extract_command_from_text,
    classify_action,
    categorize_task,
    extract_problem_statement,
)


# ============ Data Structures ============

@dataclass
class ActionStep:
    """Single action step in a trajectory."""
    step_num: int
    action_type: str  # search, open, edit, execute, test, submit
    command: str
    target: Optional[str] = None
    result_type: Optional[str] = None  # success, error, no_result


@dataclass
class LoopSignature:
    """Signature for loop detection."""
    action_type: str
    command_pattern: str  # Normalized command pattern
    frequency: int = 1

    def to_dict(self):
        return asdict(self)


@dataclass
class Methodology:
    """A methodology extracted from successful trajectories."""
    methodology_id: str
    task_category: str
    situation_pattern: str  # Describes when this applies
    action_sequence: List[str]  # Sequence of action types
    key_commands: List[str]  # Important commands
    success_rate: float = 1.0
    frequency: int = 1
    source_instances: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "methodology_id": self.methodology_id,
            "task_category": self.task_category,
            "situation_pattern": self.situation_pattern,
            "action_sequence": self.action_sequence,
            "key_commands": self.key_commands,
            "success_rate": self.success_rate,
            "frequency": self.frequency,
            "source_instances": self.source_instances[:5],  # Limit for storage
        }


@dataclass
class ErrorPattern:
    """Pattern representing common errors."""
    error_type: str
    error_message_pattern: str
    recovery_actions: List[str]
    frequency: int = 1

    def to_dict(self):
        return asdict(self)


@dataclass
class AgentMemoryGraph:
    """The complete Agent Memory graph."""
    methodologies: Dict[str, Methodology] = field(default_factory=dict)
    loop_signatures: Dict[str, LoopSignature] = field(default_factory=dict)
    error_patterns: Dict[str, ErrorPattern] = field(default_factory=dict)
    statistics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {
            "methodologies": {k: v.to_dict() for k, v in self.methodologies.items()},
            "loop_signatures": {k: v.to_dict() for k, v in self.loop_signatures.items()},
            "error_patterns": {k: v.to_dict() for k, v in self.error_patterns.items()},
            "statistics": self.statistics,
        }


# ============ Extraction Functions ============

def extract_action_with_target(command: str) -> Tuple[str, Optional[str]]:
    """Extract action type and target from command."""
    if not command:
        return ("unknown", None)

    command = command.strip()

    # Search with target
    patterns = [
        (r'search_dir\s+"([^"]+)"', "search"),
        (r'search_file\s+"([^"]+)"', "search"),
        (r'find_file\s+"([^"]+)"', "search"),
        (r'open\s+(\S+)', "open"),
        (r'edit\s+(\d+:\d+)', "edit"),
        (r'create\s+(\S+)', "create"),
        (r'python\s+(\S+)', "execute"),
        (r'cat\s+(\S+)', "read"),
    ]

    for pattern, action_type in patterns:
        match = re.search(pattern, command)
        if match:
            return (action_type, match.group(1))

    # Action type without target
    action_type, _ = classify_action(command)
    return (action_type, None)


def normalize_command(command: str) -> str:
    """Normalize command for pattern matching."""
    if not command:
        return ""

    # Remove specific file paths but keep structure
    normalized = command
    normalized = re.sub(r'/[a-zA-Z0-9_/]+\.py', '/<FILE>.py', normalized)
    normalized = re.sub(r'/[a-zA-Z0-9_/]+\.txt', '/<FILE>.txt', normalized)
    normalized = re.sub(r'"[^"]*"', '"<TERM>"', normalized)
    normalized = re.sub(r'\d+', '<N>', normalized)

    return normalized


def parse_trajectory(data: dict) -> Tuple[List[ActionStep], bool, str]:
    """Parse trajectory into action steps."""
    trajectory = data.get("trajectory", [])
    success = data.get("target", False) == True
    instance_id = data.get("instance_id", "unknown")

    steps = []
    for turn in trajectory:
        if turn.get("role") == "ai":
            text = turn.get("text", "")
            cmd = extract_command_from_text(text)
            if cmd:
                action_type, target = extract_action_with_target(cmd)
                steps.append(ActionStep(
                    step_num=len(steps),
                    action_type=action_type,
                    command=cmd,
                    target=target,
                ))

    return steps, success, instance_id


def extract_methodology_signature(steps: List[ActionStep], max_steps: int = 15) -> str:
    """Extract a signature from action sequence."""
    action_sequence = [s.action_type for s in steps[:max_steps]]
    return "->".join(action_sequence)


def detect_loops_in_trajectory(steps: List[ActionStep], min_repeat: int = 3) -> List[LoopSignature]:
    """Detect loop patterns in a trajectory."""
    if len(steps) < min_repeat:
        return []

    loops = []
    commands = [s.command for s in steps]
    normalized = [normalize_command(c) for c in commands]

    i = 0
    while i < len(normalized):
        count = 1
        while i + count < len(normalized) and normalized[i] == normalized[i + count]:
            count += 1
        if count >= min_repeat:
            action_type = steps[i].action_type
            loops.append(LoopSignature(
                action_type=action_type,
                command_pattern=normalized[i],
                frequency=count,
            ))
        i += count

    return loops


def extract_error_from_response(user_text: str) -> Optional[Tuple[str, str]]:
    """Extract error type and message from user response."""
    if not user_text:
        return None

    # Common error patterns
    error_patterns = [
        (r'(TypeError):\s*(.+?)(?:\n|$)', "TypeError"),
        (r'(AttributeError):\s*(.+?)(?:\n|$)', "AttributeError"),
        (r'(KeyError):\s*(.+?)(?:\n|$)', "KeyError"),
        (r'(ValueError):\s*(.+?)(?:\n|$)', "ValueError"),
        (r'(ImportError):\s*(.+?)(?:\n|$)', "ImportError"),
        (r'(ModuleNotFoundError):\s*(.+?)(?:\n|$)', "ImportError"),
        (r'(SyntaxError):\s*(.+?)(?:\n|$)', "SyntaxError"),
        (r'(IndentationError):\s*(.+?)(?:\n|$)', "SyntaxError"),
        (r'(FileNotFoundError):\s*(.+?)(?:\n|$)', "FileNotFoundError"),
        (r'(NameError):\s*(.+?)(?:\n|$)', "NameError"),
    ]

    for pattern, error_type in error_patterns:
        match = re.search(pattern, user_text)
        if match:
            return (error_type, match.group(2)[:100])

    return None


# ============ Graph Building ============

def build_graph_from_trajectories(
    train_ids: List[str],
    trajectories_dir: Path,
    config: ExperimentConfig
) -> AgentMemoryGraph:
    """Build the Agent Memory graph from training trajectories."""

    graph = AgentMemoryGraph()

    # Track patterns for aggregation
    methodology_groups = defaultdict(lambda: {
        "instances": [],
        "task_categories": [],
        "key_commands": [],
    })
    loop_counts = defaultdict(int)
    error_recovery = defaultdict(list)

    # Statistics
    total_processed = 0
    successful_count = 0
    failed_count = 0
    with_loops_count = 0

    print(f"Building graph from {len(train_ids)} training trajectories...")

    for i, instance_id in enumerate(train_ids):
        if i % 500 == 0:
            print(f"  Progress: {i}/{len(train_ids)}")

        # Load trajectory
        traj_file = trajectories_dir / f"{instance_id}.json"
        if not traj_file.exists():
            continue

        try:
            with open(traj_file, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        steps, success, inst_id = parse_trajectory(data)
        if len(steps) < 3:
            continue

        total_processed += 1

        # Extract problem statement for task categorization
        problem_stmt = extract_problem_statement(data.get("trajectory", []))
        task_category = categorize_task(problem_stmt)

        if success:
            successful_count += 1

            # Extract methodology from successful trajectory
            signature = extract_methodology_signature(steps)
            methodology_groups[signature]["instances"].append(instance_id)
            methodology_groups[signature]["task_categories"].append(task_category)
            methodology_groups[signature]["key_commands"].extend([s.command for s in steps[:5]])

        else:
            failed_count += 1

            # Detect loops in failed trajectories
            loops = detect_loops_in_trajectory(steps)
            if loops:
                with_loops_count += 1
                for loop in loops:
                    key = f"{loop.action_type}:{loop.command_pattern}"
                    loop_counts[key] += 1

            # Extract error patterns
            trajectory = data.get("trajectory", [])
            for j, turn in enumerate(trajectory):
                if turn.get("role") == "user":
                    error = extract_error_from_response(turn.get("text", ""))
                    if error:
                        error_type, error_msg = error
                        # Look at next AI action as potential recovery
                        for k in range(j + 1, min(j + 3, len(trajectory))):
                            if trajectory[k].get("role") == "ai":
                                cmd = extract_command_from_text(trajectory[k].get("text", ""))
                                if cmd:
                                    action_type, _ = classify_action(cmd)
                                    error_recovery[error_type].append(action_type)
                                    break

    # ============ Consolidate Methodologies ============

    print("Consolidating methodologies...")
    methodology_id = 0

    for signature, group in methodology_groups.items():
        if len(group["instances"]) < config.memory.min_methodology_frequency:
            continue

        if methodology_id >= config.memory.max_methodologies:
            break

        # Get most common task category
        task_cats = group["task_categories"]
        most_common_cat = max(set(task_cats), key=task_cats.count) if task_cats else "general"

        # Get action sequence from signature
        action_sequence = signature.split("->")

        # Get unique key commands
        key_commands = list(set(group["key_commands"]))[:5]

        methodology = Methodology(
            methodology_id=f"M{methodology_id:03d}",
            task_category=most_common_cat,
            situation_pattern=f"{most_common_cat} task with {action_sequence[0]} start",
            action_sequence=action_sequence,
            key_commands=key_commands,
            frequency=len(group["instances"]),
            source_instances=group["instances"],
        )

        graph.methodologies[methodology.methodology_id] = methodology
        methodology_id += 1

    # ============ Consolidate Loop Signatures ============

    print("Consolidating loop signatures...")
    for key, count in sorted(loop_counts.items(), key=lambda x: -x[1])[:50]:
        parts = key.split(":", 1)
        if len(parts) == 2:
            action_type, pattern = parts
            sig_id = hashlib.md5(key.encode()).hexdigest()[:8]
            graph.loop_signatures[sig_id] = LoopSignature(
                action_type=action_type,
                command_pattern=pattern,
                frequency=count,
            )

    # ============ Consolidate Error Patterns ============

    print("Consolidating error patterns...")
    for error_type, recovery_actions in error_recovery.items():
        if len(recovery_actions) < 3:
            continue

        # Get most common recovery actions
        action_counts = defaultdict(int)
        for action in recovery_actions:
            action_counts[action] += 1

        common_recoveries = sorted(action_counts.items(), key=lambda x: -x[1])[:3]

        graph.error_patterns[error_type] = ErrorPattern(
            error_type=error_type,
            error_message_pattern="",  # Would need more parsing
            recovery_actions=[a[0] for a in common_recoveries],
            frequency=len(recovery_actions),
        )

    # ============ Statistics ============

    graph.statistics = {
        "total_processed": total_processed,
        "successful_trajectories": successful_count,
        "failed_trajectories": failed_count,
        "with_loops_count": with_loops_count,
        "loop_rate": with_loops_count / failed_count if failed_count > 0 else 0,
        "methodologies_extracted": len(graph.methodologies),
        "loop_signatures_extracted": len(graph.loop_signatures),
        "error_patterns_extracted": len(graph.error_patterns),
    }

    return graph


def save_graph(graph: AgentMemoryGraph, config: ExperimentConfig):
    """Save the graph to JSON file."""
    output_path = config.paths.graph_file
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump(graph.to_dict(), f, indent=2)

    print(f"Graph saved to {output_path}")


def load_graph(config: ExperimentConfig) -> Optional[AgentMemoryGraph]:
    """Load graph from JSON file."""
    graph_file = config.paths.graph_file
    if not graph_file.exists():
        return None

    with open(graph_file, 'r') as f:
        data = json.load(f)

    graph = AgentMemoryGraph()

    for mid, mdata in data.get("methodologies", {}).items():
        graph.methodologies[mid] = Methodology(**mdata)

    for sid, sdata in data.get("loop_signatures", {}).items():
        graph.loop_signatures[sid] = LoopSignature(**sdata)

    for eid, edata in data.get("error_patterns", {}).items():
        graph.error_patterns[eid] = ErrorPattern(**edata)

    graph.statistics = data.get("statistics", {})

    return graph


def main():
    """Build the Agent Memory graph from training set."""
    config = get_config()

    # Load split
    split = load_split(config)
    if split is None:
        print("Error: No split found. Run data_splitter.py first.")
        return

    print(f"Loaded split with {len(split.train_ids)} training trajectories")

    # Build graph
    graph = build_graph_from_trajectories(
        split.train_ids,
        config.paths.trajectories_dir,
        config
    )

    # Save graph
    save_graph(graph, config)

    # Print summary
    print("\n" + "=" * 50)
    print("Graph Building Complete")
    print("=" * 50)
    print(f"\nStatistics:")
    for key, value in graph.statistics.items():
        if isinstance(value, float):
            print(f"  {key}: {value:.3f}")
        else:
            print(f"  {key}: {value}")

    print(f"\nMethodologies extracted: {len(graph.methodologies)}")
    print(f"Loop signatures: {len(graph.loop_signatures)}")
    print(f"Error patterns: {len(graph.error_patterns)}")

    # Show sample methodologies
    print("\n--- Sample Methodologies ---")
    for i, (mid, m) in enumerate(list(graph.methodologies.items())[:5]):
        print(f"\n{mid} ({m.task_category}, freq={m.frequency}):")
        print(f"  Actions: {' -> '.join(m.action_sequence[:5])}")


if __name__ == "__main__":
    main()
