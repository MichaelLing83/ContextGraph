"""
Data Splitter for Agent Memory A/B Testing Experiment.

Implements stratified splitting of SWE-agent trajectories to ensure
balanced train/test sets with equal distributions of:
- Success/failure rates
- Task types
- Loop severity levels
"""

import json
import re
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
import random

from .config import ExperimentConfig, get_config


@dataclass
class TrajectoryMetadata:
    """Metadata for a single trajectory."""
    instance_id: str
    success: bool
    task_type: str
    has_loop: bool
    loop_severity: str  # none, mild, moderate, severe
    total_steps: int
    file_path: str


@dataclass
class SplitResult:
    """Result of the train/test split."""
    train_ids: List[str]
    test_ids: List[str]
    train_metadata: Dict[str, dict]
    test_metadata: Dict[str, dict]
    split_statistics: Dict[str, Any]


def extract_command_from_text(text: str) -> Optional[str]:
    """Extract command from AI response text.

    Handles code blocks with optional language tags like ```bash or ```python.
    """
    if not text:
        return None
    # Match full fenced block and parse command from first line.
    block_match = re.search(r'```(?:\w+)?\n(.*?)\n```', str(text), flags=re.DOTALL)
    if not block_match:
        return None

    lines = block_match.group(1).splitlines()
    if not lines:
        return None

    command = lines[0].strip()
    if not command:
        return None

    # Allow standard single-line commands.
    if len(lines) == 1:
        return command

    # Allow multiline edit blocks that terminate correctly.
    if command.startswith("edit ") and lines[-1].strip() == "end_of_edit":
        return command

    return None


def classify_action(command: str) -> Tuple[str, Optional[str]]:
    """Classify a command into action type and target."""
    if not command:
        return ("unknown", None)

    command = command.strip()
    command_lower = command.lower()

    # Search actions
    if command.startswith(("search_dir", "search_file", "find_file", "grep", "rg", "find ")):
        return ("search", None)

    # File operations
    if command.startswith("open"):
        return ("open", None)
    if command.startswith(("goto", "scroll_")):
        return ("navigate", None)

    # Edit actions
    if command.startswith("edit"):
        return ("edit", None)

    # Create actions
    if command.startswith("create"):
        return ("create", None)

    if "pytest" in command_lower or re.search(r"\btest\b", command_lower):
        return ("test", None)

    # Execute actions
    if command.startswith(("python", "bash")):
        return ("execute", None)

    # Submit
    if command == "submit":
        return ("submit", None)

    return ("other", None)


def detect_loop_severity(trajectory: list, min_repeat: int = 3) -> Tuple[bool, str, int]:
    """
    Detect loops in a trajectory and classify severity.

    Returns:
        has_loop: Whether any loop was detected
        severity: 'none', 'mild', 'moderate', 'severe'
        max_length: Maximum loop length found
    """
    # Extract commands from AI turns
    commands = []
    for turn in trajectory:
        if turn.get("role") == "ai":
            text = turn.get("text", "")
            cmd = extract_command_from_text(text)
            if cmd:
                commands.append(cmd)

    if len(commands) < min_repeat:
        return False, "none", 0

    max_loop_length = 0

    # Detect consecutive identical commands
    i = 0
    while i < len(commands):
        count = 1
        while i + count < len(commands) and commands[i] == commands[i + count]:
            count += 1
        if count >= min_repeat:
            max_loop_length = max(max_loop_length, count)
        i += count

    # Classify severity
    if max_loop_length == 0:
        return False, "none", 0
    elif max_loop_length < 5:
        return True, "mild", max_loop_length
    elif max_loop_length < 10:
        return True, "moderate", max_loop_length
    else:
        return True, "severe", max_loop_length


def extract_problem_statement(trajectory: list) -> str:
    """Extract problem statement from user turn in trajectory."""
    for turn in trajectory:
        if turn.get("role") == "user":
            text = turn.get("text", "")
            if "ISSUE:" in text:
                return text
    return ""


def categorize_task(problem_statement: str) -> str:
    """Categorize task type based on problem statement."""
    problem = problem_statement.lower()

    if "typeerror" in problem or "type error" in problem:
        return "type_error"
    elif "attributeerror" in problem or "attribute error" in problem:
        return "attribute_error"
    elif "keyerror" in problem or "key error" in problem:
        return "key_error"
    elif "indexerror" in problem or "index error" in problem:
        return "index_error"
    elif "valueerror" in problem or "value error" in problem:
        return "value_error"
    elif "importerror" in problem or "import error" in problem:
        return "import_error"
    elif "fix" in problem or "bug" in problem or "error" in problem:
        return "bug_fix"
    elif "add" in problem or "implement" in problem or "feature" in problem:
        return "feature_request"
    elif "refactor" in problem or "rename" in problem:
        return "refactoring"
    else:
        return "general"


def load_trajectory_metadata(filepath: Path) -> Optional[TrajectoryMetadata]:
    """Load and extract metadata from a single trajectory file."""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Could not load {filepath}: {e}")
        return None

    instance_id = data.get("instance_id", filepath.stem)
    success = data.get("target", False) == True
    trajectory = data.get("trajectory", [])

    # Extract problem statement from trajectory (it's in the user turn, not a top-level field)
    problem_statement = extract_problem_statement(trajectory)

    # Detect loops
    has_loop, loop_severity, _ = detect_loop_severity(trajectory)

    # Categorize task
    task_type = categorize_task(problem_statement)

    # Count steps
    total_steps = len([t for t in trajectory if t.get("role") == "ai"])

    return TrajectoryMetadata(
        instance_id=instance_id,
        success=success,
        task_type=task_type,
        has_loop=has_loop,
        loop_severity=loop_severity,
        total_steps=total_steps,
        file_path=str(filepath)
    )


def create_stratification_key(meta: TrajectoryMetadata, config: ExperimentConfig) -> str:
    """Create stratification key based on trajectory metadata."""
    parts = []

    if config.split.stratify_by_success:
        parts.append(f"success={meta.success}")

    if config.split.stratify_by_task_type:
        parts.append(f"type={meta.task_type}")

    if config.split.stratify_by_loop_severity:
        parts.append(f"loop={meta.loop_severity}")

    return "|".join(parts)


def stratified_split(
    metadata_list: List[TrajectoryMetadata],
    config: ExperimentConfig
) -> Tuple[List[str], List[str]]:
    """
    Perform stratified train/test split.

    Ensures that each stratum (combination of success, task_type, loop_severity)
    is split proportionally between train and test sets.
    """
    rng = random.Random(config.split.random_seed)

    # Group by stratification key
    strata: Dict[str, List[TrajectoryMetadata]] = defaultdict(list)
    for meta in metadata_list:
        key = create_stratification_key(meta, config)
        strata[key].append(meta)

    train_ids = []
    test_ids = []

    for stratum_key, items in strata.items():
        # Shuffle within stratum
        rng.shuffle(items)

        # Calculate split point
        n_train = int(len(items) * config.split.train_ratio)

        # Ensure minimum size in both sets (if possible)
        min_size = config.split.min_stratum_size
        if len(items) >= 2 * min_size:
            n_train = max(min_size, min(len(items) - min_size, n_train))

        # Split
        train_items = items[:n_train]
        test_items = items[n_train:]

        train_ids.extend([m.instance_id for m in train_items])
        test_ids.extend([m.instance_id for m in test_items])

    # Final shuffle to remove any ordering artifacts
    rng.shuffle(train_ids)
    rng.shuffle(test_ids)

    return train_ids, test_ids


def compute_split_statistics(
    train_metadata: Dict[str, TrajectoryMetadata],
    test_metadata: Dict[str, TrajectoryMetadata]
) -> Dict:
    """Compute statistics for the split to verify balance."""

    def compute_group_stats(group: Dict[str, TrajectoryMetadata]) -> Dict:
        if not group:
            return {}

        items = list(group.values())
        n = len(items)

        # Success rate
        n_success = sum(1 for m in items if m.success)
        success_rate = n_success / n if n > 0 else 0

        # Task type distribution
        task_types = defaultdict(int)
        for m in items:
            task_types[m.task_type] += 1

        # Loop severity distribution
        loop_severity = defaultdict(int)
        for m in items:
            loop_severity[m.loop_severity] += 1

        # Average steps
        avg_steps = sum(m.total_steps for m in items) / n if n > 0 else 0

        return {
            "count": n,
            "success_rate": success_rate,
            "n_success": n_success,
            "n_failure": n - n_success,
            "task_types": dict(task_types),
            "loop_severity": dict(loop_severity),
            "avg_steps": avg_steps
        }

    return {
        "train": compute_group_stats(train_metadata),
        "test": compute_group_stats(test_metadata)
    }


def split_trajectories(config: Optional[ExperimentConfig] = None) -> SplitResult:
    """
    Main function to split trajectories into train/test sets.

    Returns:
        SplitResult containing train/test IDs and metadata
    """
    if config is None:
        config = get_config()

    print("Loading trajectory metadata...")
    trajectories_dir = config.paths.trajectories_dir
    traj_files = list(trajectories_dir.glob("*.json"))
    print(f"Found {len(traj_files)} trajectory files")

    # Load all metadata
    metadata_list = []
    for i, filepath in enumerate(traj_files):
        if i % 500 == 0:
            print(f"  Progress: {i}/{len(traj_files)}")
        meta = load_trajectory_metadata(filepath)
        if meta:
            metadata_list.append(meta)

    print(f"Loaded metadata for {len(metadata_list)} trajectories")

    # Validate count
    if len(metadata_list) < config.expected_total_trajectories * 0.9:
        print(f"Warning: Expected ~{config.expected_total_trajectories} trajectories, got {len(metadata_list)}")

    # Perform stratified split
    print("Performing stratified split...")
    train_ids, test_ids = stratified_split(metadata_list, config)

    # Create metadata dictionaries
    id_to_meta = {m.instance_id: m for m in metadata_list}
    train_metadata = {id_: id_to_meta[id_] for id_ in train_ids if id_ in id_to_meta}
    test_metadata = {id_: id_to_meta[id_] for id_ in test_ids if id_ in id_to_meta}

    # Compute statistics
    stats = compute_split_statistics(train_metadata, test_metadata)

    print(f"\nSplit complete:")
    print(f"  Train: {len(train_ids)} trajectories")
    print(f"  Test: {len(test_ids)} trajectories")
    print(f"\nTrain success rate: {stats['train']['success_rate']:.3f}")
    print(f"Test success rate: {stats['test']['success_rate']:.3f}")

    return SplitResult(
        train_ids=train_ids,
        test_ids=test_ids,
        train_metadata={id_: asdict(m) for id_, m in train_metadata.items()},
        test_metadata={id_: asdict(m) for id_, m in test_metadata.items()},
        split_statistics=stats
    )


def save_split(split_result: SplitResult, config: Optional[ExperimentConfig] = None):
    """Save split results to JSON file."""
    if config is None:
        config = get_config()

    output = {
        "train_ids": split_result.train_ids,
        "test_ids": split_result.test_ids,
        "train_metadata": split_result.train_metadata,
        "test_metadata": split_result.test_metadata,
        "statistics": split_result.split_statistics,
        "config": {
            "train_ratio": config.split.train_ratio,
            "random_seed": config.split.random_seed,
            "stratify_by_success": config.split.stratify_by_success,
            "stratify_by_task_type": config.split.stratify_by_task_type,
            "stratify_by_loop_severity": config.split.stratify_by_loop_severity
        }
    }

    output_path = config.paths.splits_file
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"Split saved to {output_path}")


def load_split(config: Optional[ExperimentConfig] = None) -> Optional[SplitResult]:
    """Load previously saved split from JSON file."""
    if config is None:
        config = get_config()

    splits_file = config.paths.splits_file
    if not splits_file.exists():
        return None

    with open(splits_file, 'r') as f:
        data = json.load(f)

    # Metadata remains dictionaries in persisted split format.
    train_metadata = {}
    for id_, meta_dict in data.get("train_metadata", {}).items():
        train_metadata[id_] = meta_dict

    test_metadata = {}
    for id_, meta_dict in data.get("test_metadata", {}).items():
        test_metadata[id_] = meta_dict

    return SplitResult(
        train_ids=data["train_ids"],
        test_ids=data["test_ids"],
        train_metadata=train_metadata,
        test_metadata=test_metadata,
        split_statistics=data.get("statistics", {})
    )


def main():
    """Run the data splitting process."""
    config = get_config()

    # Validate paths
    if not config.paths.trajectories_dir.exists():
        print(f"Error: Trajectories directory not found: {config.paths.trajectories_dir}")
        return

    # Split trajectories
    split_result = split_trajectories(config)

    # Save results
    save_split(split_result, config)

    # Print detailed statistics
    print("\n" + "=" * 50)
    print("Split Statistics")
    print("=" * 50)

    stats = split_result.split_statistics

    print("\n--- Train Set ---")
    train_stats = stats["train"]
    print(f"Count: {train_stats['count']}")
    print(f"Success Rate: {train_stats['success_rate']:.3f} ({train_stats['n_success']}/{train_stats['count']})")
    print(f"Task Types: {train_stats['task_types']}")
    print(f"Loop Severity: {train_stats['loop_severity']}")

    print("\n--- Test Set ---")
    test_stats = stats["test"]
    print(f"Count: {test_stats['count']}")
    print(f"Success Rate: {test_stats['success_rate']:.3f} ({test_stats['n_success']}/{test_stats['count']})")
    print(f"Task Types: {test_stats['task_types']}")
    print(f"Loop Severity: {test_stats['loop_severity']}")


if __name__ == "__main__":
    main()
