"""
Agent Memory A/B Testing Experiment.

This package contains tools for evaluating the impact of Agent Memory
on SWE-agent performance through controlled A/B testing.
"""

from .config import ExperimentConfig, get_config, validate_paths
from .data_splitter import (
    TrajectoryMetadata,
    SplitResult,
    split_trajectories,
    save_split,
    load_split
)
from .graph_builder import (
    AgentMemoryGraph,
    Methodology,
    LoopSignature,
    ErrorPattern,
    build_graph_from_trajectories,
    save_graph,
    load_graph,
)

__all__ = [
    "ExperimentConfig",
    "get_config",
    "validate_paths",
    "TrajectoryMetadata",
    "SplitResult",
    "split_trajectories",
    "save_split",
    "load_split",
    "AgentMemoryGraph",
    "Methodology",
    "LoopSignature",
    "ErrorPattern",
    "build_graph_from_trajectories",
    "save_graph",
    "load_graph",
]
