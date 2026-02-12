#!/usr/bin/env python
"""CLI for running Agent Memory evaluation experiments."""

import argparse
import json
import sys
from pathlib import Path


def _import_evaluation_modules():
    try:
        from agent_memory import AgentMemory
        from agent_memory.evaluation import random_split, build_graph
        return AgentMemory, random_split, build_graph
    except ModuleNotFoundError:
        # Support running this script directly from any working directory.
        repo_root = Path(__file__).resolve().parents[1]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from agent_memory import AgentMemory
        from agent_memory.evaluation import random_split, build_graph
        return AgentMemory, random_split, build_graph


def main():
    AgentMemory, random_split, build_graph = _import_evaluation_modules()

    parser = argparse.ArgumentParser(
        description="Run Agent Memory evaluation experiment"
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
        "--neo4j-uri",
        type=str,
        default=None,
        help="Neo4j URI (default: None for mock mode)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output file for results (default: stdout)",
    )

    args = parser.parse_args()
    if not args.trajectories_dir.exists():
        print(f"Error: trajectories_dir '{args.trajectories_dir}' does not exist.")
        sys.exit(1)
    if not args.trajectories_dir.is_dir():
        print(f"Error: trajectories_dir '{args.trajectories_dir}' is not a directory.")
        sys.exit(1)

    # Find trajectory files
    traj_files = sorted(args.trajectories_dir.glob("*.traj"))
    if not traj_files:
        print(f"No .traj files found in {args.trajectories_dir}")
        sys.exit(1)

    print(f"Found {len(traj_files)} trajectory files")

    # Split data
    split = random_split(traj_files, ratio=args.split_ratio, seed=args.seed)
    print(f"Train: {split.train_count}, Test: {split.test_count}")

    # Build graph from training data
    print("\nBuilding memory graph from training trajectories...")
    memory = AgentMemory(neo4j_uri=args.neo4j_uri, embedding_api_key=None)
    try:
        build_result = build_graph(split.train, memory, consolidate=True)
    finally:
        memory.close()

    print(f"Loaded {build_result.trajectories_loaded} trajectories")

    # TODO: Actual evaluation would run SWE-agent here
    # For now, print summary
    print("\n" + "=" * 50)
    print("EVALUATION SETUP COMPLETE")
    print("=" * 50)
    print(f"Training trajectories: {build_result.trajectories_loaded}")
    print(f"Test problems: {split.test_count}")
    print("\nTo run full evaluation, integrate with SWE-agent runner.")

    results = {
        "input": {
            "trajectories_dir": str(args.trajectories_dir),
            "split_ratio": args.split_ratio,
            "seed": args.seed,
            "neo4j_uri": args.neo4j_uri,
        },
        "split": {
            "train_count": split.train_count,
            "test_count": split.test_count,
            "train_files": [str(path) for path in split.train],
            "test_files": [str(path) for path in split.test],
        },
        "graph_build": {
            "trajectories_loaded": build_result.trajectories_loaded,
            "fragments_created": build_result.fragments_created,
            "consolidation_run": build_result.consolidation_run,
            "errors": build_result.errors,
        },
    }

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
