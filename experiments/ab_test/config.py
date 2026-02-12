"""
Configuration for Agent Memory A/B Testing Experiment.

This module defines all configuration parameters for the experiment including
paths, hyperparameters, and experiment settings.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PathConfig:
    """Paths configuration for the experiment."""

    # Project root (auto-detected)
    project_root: Path = field(default_factory=lambda: Path(__file__).parent.parent.parent)

    # Trajectory data paths
    trajectories_dir: Path = field(default_factory=lambda: Path("swe_trajectories/trajectories"))
    metadata_dir: Path = field(default_factory=lambda: Path("swe_trajectories/metadata"))

    # Experiment output paths
    experiment_dir: Path = field(default_factory=lambda: Path("experiments/ab_test"))
    results_dir: Path = field(default_factory=lambda: Path("experiments/ab_test/results"))
    splits_file: Path = field(default_factory=lambda: Path("experiments/ab_test/splits.json"))
    graph_file: Path = field(default_factory=lambda: Path("experiments/ab_test/training_graph.json"))

    def __post_init__(self):
        """Resolve all paths relative to project root."""
        self.trajectories_dir = self.project_root / self.trajectories_dir
        self.metadata_dir = self.project_root / self.metadata_dir
        self.experiment_dir = self.project_root / self.experiment_dir
        self.results_dir = self.project_root / self.results_dir
        self.splits_file = self.project_root / self.splits_file
        self.graph_file = self.project_root / self.graph_file


@dataclass
class SplitConfig:
    """Configuration for train/test split."""

    # Split ratio (training set proportion)
    train_ratio: float = 0.5

    # Random seed for reproducibility
    random_seed: int = 42

    # Stratification factors
    stratify_by_success: bool = True  # Ensure equal success rate in both sets
    stratify_by_task_type: bool = True  # Try to balance task types
    stratify_by_loop_severity: bool = True  # Balance by loop presence

    # Minimum samples per stratum
    min_stratum_size: int = 5


@dataclass
class LoopDetectionConfig:
    """Configuration for loop detection."""

    # Minimum consecutive repetitions to detect as loop
    min_repeat: int = 3

    # Types of loops to detect
    detect_exact_command: bool = True
    detect_action_type: bool = True
    detect_action_target: bool = True

    # Severity thresholds
    mild_loop_threshold: int = 3  # 3-4 repetitions
    moderate_loop_threshold: int = 5  # 5-9 repetitions
    severe_loop_threshold: int = 10  # 10+ repetitions


@dataclass
class MemoryConfig:
    """Configuration for Agent Memory system."""

    # Graph construction
    max_methodologies: int = 100  # Maximum methodologies to extract
    min_methodology_frequency: int = 2  # Minimum occurrences to create methodology

    # Query settings
    max_query_results: int = 5  # Maximum results per query
    similarity_threshold: float = 0.7  # Minimum similarity for matches

    # Loop warning settings
    loop_warning_threshold: int = 2  # Steps before full loop to warn


@dataclass
class SimulationConfig:
    """Configuration for trajectory simulation."""

    # Simulation settings
    batch_size: int = 100  # Process trajectories in batches
    verbose: bool = True  # Print progress

    # Counterfactual estimation
    estimate_savings: bool = True  # Estimate potential token savings
    early_exit_on_warning: bool = True  # Assume agent would exit on warning


@dataclass
class ExperimentConfig:
    """Main experiment configuration."""

    # Sub-configurations
    paths: PathConfig = field(default_factory=PathConfig)
    split: SplitConfig = field(default_factory=SplitConfig)
    loop_detection: LoopDetectionConfig = field(default_factory=LoopDetectionConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)

    # Experiment metadata
    experiment_name: str = "agent_memory_ab_test"
    version: str = "1.0.0"

    # Expected trajectory count (for validation)
    expected_total_trajectories: int = 3591

    # A/B test group names
    control_group: str = "control"
    treatment_group: str = "treatment"


# Default configuration instance
DEFAULT_CONFIG = ExperimentConfig()


def get_config() -> ExperimentConfig:
    """Get the default experiment configuration."""
    return DEFAULT_CONFIG


def validate_paths(config: ExperimentConfig) -> bool:
    """Validate that required paths exist."""
    paths = config.paths

    errors = []

    if not paths.trajectories_dir.exists():
        errors.append(f"Trajectories directory not found: {paths.trajectories_dir}")

    if not paths.experiment_dir.exists():
        paths.experiment_dir.mkdir(parents=True, exist_ok=True)

    if not paths.results_dir.exists():
        paths.results_dir.mkdir(parents=True, exist_ok=True)

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return False

    return True
