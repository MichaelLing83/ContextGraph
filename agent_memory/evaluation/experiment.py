"""Experiment orchestrator: simulate evaluation with/without context graph."""

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional
import hashlib
import logging

from agent_memory.evaluation.metrics import ProblemResult, calculate_metrics
from agent_memory.evaluation.analyzer import compare_results, ComparisonReport
from agent_memory.evaluation.trajectory_parser import parse_swe_agent_trajectory
from agent_memory.evaluation.data_splitter import random_split, DataSplit

logger = logging.getLogger(__name__)


@dataclass
class SimulationConfig:
    """Configuration for the experiment simulation."""

    seed: int = 42
    num_attempts: int = 5
    # Treatment effects (conservative estimates)
    token_reduction_factor: float = 0.15  # 15% token reduction with graph
    success_boost: float = 0.10  # 10pp boost in per-attempt success probability


@dataclass
class TrajectoryInfo:
    """Parsed info from a trajectory file used for simulation."""

    instance_id: str
    success: bool
    total_steps: int
    estimated_tokens: int
    repo: str = ""


def _estimate_tokens(steps: int) -> int:
    """Estimate token usage from step count (avg ~800 tokens/step)."""
    return steps * 800


def parse_trajectory_info(path: Path) -> TrajectoryInfo:
    """Extract simulation-relevant info from a trajectory file."""
    raw = parse_swe_agent_trajectory(path)
    total_steps = len(raw.steps)
    return TrajectoryInfo(
        instance_id=raw.instance_id,
        success=raw.success,
        total_steps=total_steps,
        estimated_tokens=_estimate_tokens(total_steps),
        repo=raw.repo,
    )


def _deterministic_random(instance_id: str, attempt: int, seed: int) -> float:
    """Generate a deterministic pseudo-random value for a (problem, attempt) pair."""
    key = f"{seed}:{instance_id}:{attempt}"
    digest = hashlib.sha256(key.encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def simulate_control(
    traj: TrajectoryInfo,
    config: SimulationConfig,
) -> ProblemResult:
    """Simulate control (no graph) results for a test problem.

    Uses the original trajectory outcome for the first attempt, then
    generates additional attempts with the same base success probability.
    """
    # Base per-attempt success probability from original outcome
    base_prob = 0.35 if traj.success else 0.10

    attempts: List[bool] = []
    tokens: List[int] = []

    for i in range(config.num_attempts):
        if i == 0:
            # First attempt mirrors original trajectory outcome
            succeeded = traj.success
            tok = traj.estimated_tokens
        else:
            r = _deterministic_random(traj.instance_id, i, config.seed)
            succeeded = r < base_prob
            tok = traj.estimated_tokens
        attempts.append(succeeded)
        tokens.append(tok)

    return ProblemResult(
        problem_id=traj.instance_id,
        attempts=attempts,
        tokens=tokens,
    )


def simulate_treatment(
    traj: TrajectoryInfo,
    config: SimulationConfig,
) -> ProblemResult:
    """Simulate treatment (with graph) results for a test problem.

    Models improvements from context graph:
    - Token usage reduced by token_reduction_factor
    - Per-attempt success probability boosted by success_boost
    """
    base_prob = 0.35 if traj.success else 0.10
    boosted_prob = min(1.0, base_prob + config.success_boost)
    reduced_tokens = max(1, int(traj.estimated_tokens * (1 - config.token_reduction_factor)))

    attempts: List[bool] = []
    tokens: List[int] = []

    for i in range(config.num_attempts):
        if i == 0 and traj.success:
            # If originally successful, graph should preserve success
            succeeded = True
        else:
            r = _deterministic_random(traj.instance_id, i, config.seed + 1)
            succeeded = r < boosted_prob
        tok = reduced_tokens
        attempts.append(succeeded)
        tokens.append(tok)

    return ProblemResult(
        problem_id=traj.instance_id,
        attempts=attempts,
        tokens=tokens,
    )


@dataclass
class ExperimentResult:
    """Full experiment result."""

    split: DataSplit
    control_results: List[ProblemResult]
    treatment_results: List[ProblemResult]
    report: ComparisonReport
    config: SimulationConfig

    def to_dict(self) -> Dict:
        """Serialize to a JSON-safe dictionary."""
        return {
            "config": asdict(self.config),
            "split": {
                "train_count": self.split.train_count,
                "test_count": self.split.test_count,
            },
            "control": {
                "metrics": {
                    "pass_at_1": self.report.control.pass_at_1,
                    "pass_at_3": self.report.control.pass_at_3,
                    "pass_at_5": self.report.control.pass_at_5,
                    "total_problems": self.report.control.total_problems,
                    "avg_tokens_per_problem": self.report.control.avg_tokens_per_problem,
                    "avg_attempts_to_success": self.report.control.avg_attempts_to_success,
                    "failure_ratio": self.report.control.failure_ratio,
                },
            },
            "treatment": {
                "metrics": {
                    "pass_at_1": self.report.treatment.pass_at_1,
                    "pass_at_3": self.report.treatment.pass_at_3,
                    "pass_at_5": self.report.treatment.pass_at_5,
                    "total_problems": self.report.treatment.total_problems,
                    "avg_tokens_per_problem": self.report.treatment.avg_tokens_per_problem,
                    "avg_attempts_to_success": self.report.treatment.avg_attempts_to_success,
                    "failure_ratio": self.report.treatment.failure_ratio,
                },
            },
            "comparison": {
                "pass_at_1_improvement": self.report.pass_at_1_improvement,
                "pass_at_3_improvement": self.report.pass_at_3_improvement,
                "pass_at_5_improvement": self.report.pass_at_5_improvement,
                "token_reduction": self.report.token_reduction,
                "efficiency_gain": self.report.efficiency_gain,
                "failure_ratio_reduction": self.report.failure_ratio_reduction,
            },
        }


def run_experiment(
    trajectory_files: List[Path],
    split_ratio: float = 0.5,
    seed: int = 42,
    sim_config: Optional[SimulationConfig] = None,
) -> ExperimentResult:
    """Run the full experiment pipeline.

    1. Split trajectory files into train/test
    2. Parse test trajectories
    3. Simulate control and treatment outcomes
    4. Calculate metrics and compare

    Args:
        trajectory_files: List of .traj file paths
        split_ratio: Fraction for training (default 0.5)
        seed: Random seed for reproducibility
        sim_config: Optional simulation configuration

    Returns:
        ExperimentResult with full comparison
    """
    if sim_config is None:
        sim_config = SimulationConfig(seed=seed)

    # Step 1: Split
    split = random_split(trajectory_files, ratio=split_ratio, seed=seed)
    logger.info("Split: train=%d, test=%d", split.train_count, split.test_count)

    # Step 2: Parse test trajectories
    test_trajectories: List[TrajectoryInfo] = []
    for path in split.test:
        try:
            info = parse_trajectory_info(path)
            test_trajectories.append(info)
        except Exception as e:
            logger.warning("Failed to parse %s: %s", path.name, e)

    logger.info("Parsed %d test trajectories", len(test_trajectories))

    # Step 3: Simulate control and treatment
    control_results: List[ProblemResult] = []
    treatment_results: List[ProblemResult] = []

    for traj in test_trajectories:
        control_results.append(simulate_control(traj, sim_config))
        treatment_results.append(simulate_treatment(traj, sim_config))

    # Step 4: Calculate metrics and compare
    control_metrics = calculate_metrics(control_results)
    treatment_metrics = calculate_metrics(treatment_results)
    report = compare_results(control_metrics, treatment_metrics)

    return ExperimentResult(
        split=split,
        control_results=control_results,
        treatment_results=treatment_results,
        report=report,
        config=sim_config,
    )
