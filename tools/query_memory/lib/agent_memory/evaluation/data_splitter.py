"""Split trajectory data into train/test sets."""

from dataclasses import dataclass
from pathlib import Path
from typing import List
import random


@dataclass
class DataSplit:
    """Result of splitting data into train/test sets."""

    train: List[Path]
    test: List[Path]

    @property
    def train_count(self) -> int:
        return len(self.train)

    @property
    def test_count(self) -> int:
        return len(self.test)


def random_split(
    files: List[Path],
    ratio: float = 0.5,
    seed: int = 42,
) -> DataSplit:
    """
    Randomly split files into train/test sets.

    Args:
        files: List of trajectory file paths
        ratio: Fraction of files for training (default 0.5)
        seed: Random seed for reproducibility

    Returns:
        DataSplit with train and test file lists
    """
    if not 0 <= ratio <= 1:
        raise ValueError(f"ratio must be between 0 and 1, got {ratio}")

    # Copy list to avoid modifying original
    files = list(files)
    if not files:
        return DataSplit(train=[], test=[])

    # Shuffle with fixed seed
    rng = random.Random(seed)
    rng.shuffle(files)

    # Split
    split_idx = int(len(files) * ratio)
    train = files[:split_idx]
    test = files[split_idx:]

    return DataSplit(train=train, test=test)
