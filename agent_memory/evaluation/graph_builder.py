"""Build memory graph from trajectory files."""

from dataclasses import dataclass
from pathlib import Path
from typing import List, TYPE_CHECKING
import logging

from agent_memory.evaluation.trajectory_parser import parse_swe_agent_trajectory

if TYPE_CHECKING:
    from agent_memory import AgentMemory

logger = logging.getLogger(__name__)


@dataclass
class BuildResult:
    """Result of building memory graph."""

    trajectories_loaded: int
    fragments_created: int
    errors: List[str]
    consolidation_run: bool = False


def build_graph(
    trajectory_files: List[Path],
    memory: "AgentMemory",
    consolidate: bool = True,
) -> BuildResult:
    """
    Build memory graph from trajectory files.

    Args:
        trajectory_files: List of .traj file paths
        memory: AgentMemory instance to populate
        consolidate: Whether to run consolidation after loading

    Returns:
        BuildResult with loading statistics
    """
    loaded = 0
    fragments = 0
    errors = []

    for path in trajectory_files:
        try:
            raw = parse_swe_agent_trajectory(path)
            memory.learn(raw)
            loaded += 1
            # Estimate fragments (actual count would require store query)
            fragments += max(1, len(raw.steps) // 3)
            logger.info(f"Loaded trajectory from {path.name}")
        except Exception as e:
            error_msg = f"Failed to load {path.name}: {e}"
            logger.warning(error_msg)
            errors.append(error_msg)

    consolidation_run = False
    if consolidate and loaded > 0:
        try:
            memory.consolidator.consolidate()
            consolidation_run = True
            logger.info("Consolidation completed")
        except Exception as e:
            errors.append(f"Consolidation failed: {e}")

    return BuildResult(
        trajectories_loaded=loaded,
        fragments_created=fragments,
        errors=errors,
        consolidation_run=consolidation_run,
    )
