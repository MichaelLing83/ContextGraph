#!/usr/bin/env python
"""CLI for running the Context Graph experiment pipeline."""

import argparse
import json
import sys
from pathlib import Path


def _ensure_importable():
    """Ensure agent_memory is importable."""
    try:
        import agent_memory  # noqa: F401
    except ModuleNotFoundError:
        repo_root = Path(__file__).resolve().parents[1]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))


def main():
    _ensure_importable()

    from agent_memory.evaluation.experiment import (
        run_experiment,
        SimulationConfig,
    )

    parser = argparse.ArgumentParser(
        description="Run Context Graph experiment (simulation mode)"
    )
    parser.add_argument(
        "trajectories_dir",
        type=Path,
        help="Directory containing SWE-agent .traj files",
    )
    parser.add_argument(
        "--split-ratio",
        type=float,
        default=0.5,
        help="Train/test split ratio (default: 0.5)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    parser.add_argument(
        "--num-attempts",
        type=int,
        default=5,
        help="Number of simulated attempts per problem (default: 5)",
    )
    parser.add_argument(
        "--token-reduction",
        type=float,
        default=0.15,
        help="Token reduction factor for treatment group (default: 0.15)",
    )
    parser.add_argument(
        "--success-boost",
        type=float,
        default=0.10,
        help="Success probability boost for treatment group (default: 0.10)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON file for results (default: stdout summary only)",
    )

    args = parser.parse_args()

    if not args.trajectories_dir.exists():
        print(f"Error: '{args.trajectories_dir}' does not exist.")
        sys.exit(1)
    if not args.trajectories_dir.is_dir():
        print(f"Error: '{args.trajectories_dir}' is not a directory.")
        sys.exit(1)

    # Find trajectory files
    traj_files = sorted(args.trajectories_dir.glob("*.traj"))
    if not traj_files:
        print(f"No .traj files found in {args.trajectories_dir}")
        sys.exit(1)

    print(f"Found {len(traj_files)} trajectory files")

    sim_config = SimulationConfig(
        seed=args.seed,
        num_attempts=args.num_attempts,
        token_reduction_factor=args.token_reduction,
        success_boost=args.success_boost,
    )

    result = run_experiment(
        trajectory_files=traj_files,
        split_ratio=args.split_ratio,
        seed=args.seed,
        sim_config=sim_config,
    )

    # Print human-readable summary
    print(f"\nTrain: {result.split.train_count}, Test: {result.split.test_count}")
    print()
    print(result.report.to_summary())

    # Save JSON if requested
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result.to_dict(), indent=2),
            encoding="utf-8",
        )
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
