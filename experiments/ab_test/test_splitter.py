"""
Tests for the data splitter module.
"""

import tempfile
from pathlib import Path

from .config import ExperimentConfig, PathConfig
from .data_splitter import (
    TrajectoryMetadata,
    extract_command_from_text,
    detect_loop_severity,
    categorize_task,
    create_stratification_key,
    stratified_split,
    compute_split_statistics,
    save_split,
    load_split,
)


class TestCommandExtraction:
    """Tests for command extraction from code blocks."""

    def test_extract_plain_code_block(self):
        text = "```\nsearch_dir \"foo\"\n```"
        assert extract_command_from_text(text) == 'search_dir "foo"'

    def test_extract_language_tagged_code_block(self):
        assert extract_command_from_text("```bash\npytest -q\n```") == "pytest -q"
        assert extract_command_from_text("```python\npython -m pytest\n```") == "python -m pytest"

    def test_ignore_malformed_or_multiline_blocks(self):
        assert extract_command_from_text("```bash pytest -q```") is None
        assert extract_command_from_text("```bash\nline1\nline2\n```") is None

    def test_extract_multiline_edit_block(self):
        text = "```\nedit 1:1\nfoo\nend_of_edit\n```"
        assert extract_command_from_text(text) == "edit 1:1"


class TestLoopDetection:
    """Tests for loop detection functionality."""

    def test_no_loop(self):
        """Test trajectory without loops."""
        trajectory = [
            {"role": "ai", "text": "```\nsearch_dir \"test\"\n```"},
            {"role": "user", "text": "results"},
            {"role": "ai", "text": "```\nopen test.py\n```"},
            {"role": "user", "text": "file content"},
            {"role": "ai", "text": "```\nedit 1:5\n...\nend_of_edit\n```"},
        ]
        has_loop, severity, length = detect_loop_severity(trajectory)
        assert not has_loop
        assert severity == "none"
        assert length == 0

    def test_mild_loop(self):
        """Test trajectory with mild loop (3-4 repetitions)."""
        trajectory = [
            {"role": "ai", "text": "```\nsearch_dir \"test\"\n```"},
            {"role": "user", "text": "no results"},
            {"role": "ai", "text": "```\nsearch_dir \"test\"\n```"},
            {"role": "user", "text": "no results"},
            {"role": "ai", "text": "```\nsearch_dir \"test\"\n```"},
            {"role": "user", "text": "no results"},
        ]
        has_loop, severity, length = detect_loop_severity(trajectory)
        assert has_loop
        assert severity == "mild"
        assert length == 3

    def test_moderate_loop(self):
        """Test trajectory with moderate loop (5-9 repetitions)."""
        trajectory = []
        for _ in range(6):
            trajectory.append({"role": "ai", "text": "```\nsearch_dir \"pattern\"\n```"})
            trajectory.append({"role": "user", "text": "no results"})

        has_loop, severity, length = detect_loop_severity(trajectory)
        assert has_loop
        assert severity == "moderate"
        assert length == 6

    def test_severe_loop(self):
        """Test trajectory with severe loop (10+ repetitions)."""
        trajectory = []
        for _ in range(12):
            trajectory.append({"role": "ai", "text": "```\nedit 1:1\nfoo\nend_of_edit\n```"})
            trajectory.append({"role": "user", "text": "syntax error"})

        has_loop, severity, length = detect_loop_severity(trajectory)
        assert has_loop
        assert severity == "severe"
        assert length == 12

    def test_two_repeats_not_a_loop(self):
        """Exactly two repeats should not trigger default min_repeat=3."""
        trajectory = [
            {"role": "ai", "text": "```\nsearch_dir \"foo\"\n```"},
            {"role": "user", "text": "no results"},
            {"role": "ai", "text": "```\nsearch_dir \"foo\"\n```"},
            {"role": "user", "text": "no results"},
        ]
        has_loop, severity, length = detect_loop_severity(trajectory)
        assert not has_loop
        assert severity == "none"
        assert length == 0

    def test_multiple_loops_use_longest_for_severity(self):
        """When multiple loops exist, severity is based on the longest loop."""
        trajectory = []
        for _ in range(3):
            trajectory.append({"role": "ai", "text": "```\nsearch_dir \"foo\"\n```"})
            trajectory.append({"role": "user", "text": "none"})

        trajectory.append({"role": "ai", "text": "```\nopen a.py\n```"})
        trajectory.append({"role": "user", "text": "content"})

        for _ in range(6):
            trajectory.append({"role": "ai", "text": "```\nedit 1:1\nx\nend_of_edit\n```"})
            trajectory.append({"role": "user", "text": "syntax error"})

        has_loop, severity, length = detect_loop_severity(trajectory)
        assert has_loop
        assert severity == "moderate"
        assert length == 6


class TestTaskCategorization:
    """Tests for task categorization."""

    def test_type_error(self):
        assert categorize_task("TypeError when calling function") == "type_error"
        assert categorize_task("Fix the type error in parser") == "type_error"

    def test_attribute_error(self):
        assert categorize_task("AttributeError: 'NoneType' has no attribute") == "attribute_error"

    def test_bug_fix(self):
        assert categorize_task("Fix bug in login flow") == "bug_fix"
        assert categorize_task("Error when uploading files") == "bug_fix"

    def test_feature_request(self):
        assert categorize_task("Add support for JSON export") == "feature_request"
        assert categorize_task("Implement user authentication") == "feature_request"

    def test_refactoring(self):
        assert categorize_task("Refactor the database layer") == "refactoring"
        assert categorize_task("Rename class User to Account") == "refactoring"

    def test_general(self):
        assert categorize_task("Some vague description") == "general"


class TestStratification:
    """Tests for stratified splitting."""

    def test_create_stratification_key(self):
        """Test stratification key creation."""
        config = ExperimentConfig()
        meta = TrajectoryMetadata(
            instance_id="test",
            success=True,
            task_type="bug_fix",
            has_loop=True,
            loop_severity="mild",
            total_steps=10,
            file_path="/path/to/file"
        )

        key = create_stratification_key(meta, config)
        assert "success=True" in key
        assert "type=bug_fix" in key
        assert "loop=mild" in key

    def test_stratified_split_balance(self):
        """Test that stratified split maintains balance."""
        # Create mock metadata
        metadata_list = []
        for i in range(100):
            metadata_list.append(TrajectoryMetadata(
                instance_id=f"success_{i}",
                success=True,
                task_type="bug_fix",
                has_loop=False,
                loop_severity="none",
                total_steps=10,
                file_path=f"/path/{i}"
            ))

        for i in range(900):
            metadata_list.append(TrajectoryMetadata(
                instance_id=f"failure_{i}",
                success=False,
                task_type="bug_fix",
                has_loop=True,
                loop_severity="moderate",
                total_steps=50,
                file_path=f"/path/{i}"
            ))

        config = ExperimentConfig()
        train_ids, test_ids = stratified_split(metadata_list, config)

        # Check roughly 50/50 split
        assert 450 <= len(train_ids) <= 550
        assert 450 <= len(test_ids) <= 550

        # Check success rate is preserved in both sets
        train_success = sum(1 for id_ in train_ids if id_.startswith("success"))
        test_success = sum(1 for id_ in test_ids if id_.startswith("success"))

        train_rate = train_success / len(train_ids)
        test_rate = test_success / len(test_ids)

        # Success rate should be approximately preserved (10% originally)
        assert 0.05 <= train_rate <= 0.15
        assert 0.05 <= test_rate <= 0.15


class TestSplitStatistics:
    """Tests for split statistics computation."""

    def test_compute_statistics(self):
        """Test statistics computation."""
        train_meta = {
            "t1": TrajectoryMetadata("t1", True, "bug_fix", False, "none", 10, "/p/t1"),
            "t2": TrajectoryMetadata("t2", False, "bug_fix", True, "mild", 20, "/p/t2"),
        }
        test_meta = {
            "t3": TrajectoryMetadata("t3", True, "feature_request", False, "none", 15, "/p/t3"),
        }

        stats = compute_split_statistics(train_meta, test_meta)

        assert stats["train"]["count"] == 2
        assert stats["train"]["success_rate"] == 0.5
        assert stats["train"]["n_success"] == 1
        assert stats["test"]["count"] == 1
        assert stats["test"]["success_rate"] == 1.0


class TestSaveLoad:
    """Tests for save/load functionality."""

    def test_save_and_load_split(self):
        """Test saving and loading split results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create config with temp paths
            paths = PathConfig()
            paths.splits_file = Path(tmpdir) / "splits.json"
            config = ExperimentConfig(paths=paths)

            # Create mock split result
            from .data_splitter import SplitResult
            split_result = SplitResult(
                train_ids=["a", "b", "c"],
                test_ids=["d", "e"],
                train_metadata={"a": {"instance_id": "a", "success": True}},
                test_metadata={"d": {"instance_id": "d", "success": False}},
                split_statistics={"train": {"count": 3}, "test": {"count": 2}}
            )

            # Save
            save_split(split_result, config)
            assert paths.splits_file.exists()

            # Load
            loaded = load_split(config)
            assert loaded is not None
            assert loaded.train_ids == ["a", "b", "c"]
            assert loaded.test_ids == ["d", "e"]
