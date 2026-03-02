"""Prepare data split for live experiment."""

import json
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent_memory.evaluation.data_splitter import random_split

TRAJECTORIES_DIR = project_root / "swe_trajectories" / "trajectories"
OUTPUT_PATH = project_root / "results" / "live_experiment" / "split.json"
TEST_SIZE = 200


def main():
    # List all trajectory files
    files = sorted(TRAJECTORIES_DIR.glob("*.json"))
    print(f"Found {len(files)} trajectory files")

    # Split 50/50 with seed 42
    split = random_split(files, ratio=0.5, seed=42)
    print(f"Train: {split.train_count}, Test: {split.test_count}")

    # Select first 200 test problems
    test_200 = split.test[:TEST_SIZE]
    print(f"Selected {len(test_200)} test problems")

    # Save split info
    split_data = {
        "train_files": [str(p) for p in split.train],
        "test_200_files": [str(p) for p in test_200],
        "all_test_files": [str(p) for p in split.test],
        "stats": {
            "total_files": len(files),
            "train_count": split.train_count,
            "test_count": split.test_count,
            "test_200_count": len(test_200),
            "seed": 42,
            "ratio": 0.5,
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(split_data, f, indent=2)

    print(f"Split saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
