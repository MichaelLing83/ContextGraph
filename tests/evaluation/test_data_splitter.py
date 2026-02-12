"""Tests for data splitter."""

import pytest
from agent_memory.evaluation.data_splitter import random_split


class TestRandomSplit:
    def test_split_ratio(self, tmp_path):
        """Test 50/50 split ratio."""
        # Create 10 dummy trajectory files
        for i in range(10):
            (tmp_path / f"test__test-{i}.traj").touch()

        files = list(tmp_path.glob("*.traj"))
        split = random_split(files, ratio=0.5, seed=42)

        assert len(split.train) == 5
        assert len(split.test) == 5
        assert set(split.train).isdisjoint(set(split.test))

    def test_reproducibility(self, tmp_path):
        """Test that same seed produces same split."""
        for i in range(10):
            (tmp_path / f"test__test-{i}.traj").touch()

        files = list(tmp_path.glob("*.traj"))

        split1 = random_split(files, ratio=0.5, seed=42)
        split2 = random_split(files, ratio=0.5, seed=42)

        assert split1.train == split2.train
        assert split1.test == split2.test

    def test_different_seeds_different_splits(self, tmp_path):
        """Test that different seeds produce different splits."""
        for i in range(10):
            (tmp_path / f"test__test-{i}.traj").touch()

        files = list(tmp_path.glob("*.traj"))

        split1 = random_split(files, ratio=0.5, seed=42)
        split2 = random_split(files, ratio=0.5, seed=123)

        assert split1.train != split2.train

    def test_custom_ratio(self, tmp_path):
        """Test 70/30 split."""
        for i in range(10):
            (tmp_path / f"test__test-{i}.traj").touch()

        files = list(tmp_path.glob("*.traj"))
        split = random_split(files, ratio=0.7, seed=42)

        assert len(split.train) == 7
        assert len(split.test) == 3

    def test_invalid_ratio_raises(self, tmp_path):
        """Test ratio validation."""
        files = [tmp_path / "one.traj"]
        files[0].touch()

        with pytest.raises(ValueError):
            random_split(files, ratio=-0.1, seed=42)

        with pytest.raises(ValueError):
            random_split(files, ratio=1.1, seed=42)

    def test_empty_files_returns_empty_split(self):
        """Test empty input handling."""
        split = random_split([], ratio=0.5, seed=42)
        assert split.train == []
        assert split.test == []
