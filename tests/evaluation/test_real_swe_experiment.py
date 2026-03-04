"""Tests for the real SWE-agent A/B experiment runner.

Tests cover:
  - Instance ID loading from split.json
  - Trajectory file parsing
  - Progress save/load/resume
  - Result collection and output format
  - Config file validation
"""

import json
import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.run_real_swe_experiment import (
    GroupProgress,
    InstanceResult,
    collect_results,
    load_test_instance_ids,
    load_verified_instance_ids,
    parse_traj_file,
    run_group_sequential,
    CONFIG_MAP,
    REPO_ROOT,
    SELECTION_SEED,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_traj_data(instance_id, success=True, num_steps=10, cost=1.5):
    """Create a minimal .traj file data dict."""
    exit_status = "submitted (exit_format)" if success else "failed"
    return {
        "trajectory": [
            {"action": f"action_{i}", "observation": f"obs_{i}"}
            for i in range(num_steps)
        ],
        "info": {
            "exit_status": exit_status,
            "model_stats": {
                "instance_cost": cost,
                "tokens_sent": num_steps * 1000,
                "tokens_received": num_steps * 100,
                "api_calls": num_steps,
            },
        },
    }


def _write_traj(base_dir, instance_id, **kwargs):
    """Write a .traj file in SWE-agent output layout."""
    instance_dir = base_dir / instance_id
    instance_dir.mkdir(parents=True, exist_ok=True)
    traj_file = instance_dir / f"{instance_id}.traj"
    traj_file.write_text(
        json.dumps(_make_traj_data(instance_id, **kwargs)),
        encoding="utf-8",
    )
    return traj_file


# ---------------------------------------------------------------------------
# Test: load_test_instance_ids
# ---------------------------------------------------------------------------

class TestLoadTestInstanceIds:
    def test_load_from_instance_ids(self, tmp_path):
        """instance_ids key (used by verified_instance_ids.json)."""
        split = tmp_path / "split.json"
        split.write_text(json.dumps({
            "instance_ids": ["django__django-12345", "sympy__sympy-67890"],
        }))
        ids = load_test_instance_ids(split, max_problems=200)
        assert ids == ["django__django-12345", "sympy__sympy-67890"]

    def test_load_from_test_ids(self, tmp_path):
        split = tmp_path / "split.json"
        split.write_text(json.dumps({
            "test_ids": ["django__django-12345", "sympy__sympy-67890"],
        }))
        ids = load_test_instance_ids(split, max_problems=200)
        assert ids == ["django__django-12345", "sympy__sympy-67890"]

    def test_load_from_test_200_files(self, tmp_path):
        split = tmp_path / "split.json"
        split.write_text(json.dumps({
            "test_200_files": [
                "/data/trajectories/scikit-hep__vector-319.json",
                "/data/trajectories/iris-hep__qastle-52.json",
            ],
        }))
        ids = load_test_instance_ids(split, max_problems=200)
        assert ids == ["scikit-hep__vector-319", "iris-hep__qastle-52"]

    def test_load_from_test_files_fallback(self, tmp_path):
        split = tmp_path / "split.json"
        split.write_text(json.dumps({
            "test_files": [
                "/data/django__django-12345.json",
            ],
        }))
        ids = load_test_instance_ids(split, max_problems=200)
        assert ids == ["django__django-12345"]

    def test_max_problems_limit(self, tmp_path):
        split = tmp_path / "split.json"
        all_ids = [f"repo__project-{i}" for i in range(50)]
        split.write_text(json.dumps({"test_ids": all_ids}))
        ids = load_test_instance_ids(split, max_problems=10)
        assert len(ids) == 10
        assert ids == all_ids[:10]

    def test_empty_split(self, tmp_path):
        split = tmp_path / "split.json"
        split.write_text(json.dumps({"test_ids": []}))
        ids = load_test_instance_ids(split, max_problems=200)
        assert ids == []

    def test_real_split_file(self):
        """Test with the actual split file if it exists."""
        split = REPO_ROOT / "results" / "live_experiment" / "split.json"
        if not split.exists():
            pytest.skip("Real split file not found")
        ids = load_test_instance_ids(split, max_problems=200)
        assert len(ids) == 200
        # Instance IDs should match SWE-bench format: owner__repo-number
        for iid in ids[:5]:
            assert "__" in iid, f"Invalid instance ID format: {iid}"


# ---------------------------------------------------------------------------
# Test: load_verified_instance_ids
# ---------------------------------------------------------------------------

class TestLoadVerifiedInstanceIds:
    def test_loads_from_cache(self, tmp_path):
        """If cache file exists with matching seed/max, load from it."""
        cache = tmp_path / "verified_ids.json"
        cache.write_text(json.dumps({
            "instance_ids": ["django__django-11111", "sympy__sympy-22222"],
            "seed": SELECTION_SEED,
            "max_problems": 2,
        }))
        ids = load_verified_instance_ids(
            max_problems=2, seed=SELECTION_SEED, cache_file=cache,
        )
        assert ids == ["django__django-11111", "sympy__sympy-22222"]

    def test_ignores_cache_with_different_seed(self, tmp_path):
        """Cache with different seed should be ignored."""
        cache = tmp_path / "verified_ids.json"
        cache.write_text(json.dumps({
            "instance_ids": ["old__id-1"],
            "seed": 999,
            "max_problems": 200,
        }))
        # This will try to load from HuggingFace, which we mock
        with patch("datasets.load_dataset") as mock_ds:
            mock_ds.return_value = [
                {"instance_id": f"repo__proj-{i}"} for i in range(500)
            ]
            ids = load_verified_instance_ids(
                max_problems=5, seed=SELECTION_SEED, cache_file=cache,
            )
        assert len(ids) == 5
        assert ids != ["old__id-1"]  # Should not use old cache

    @patch("datasets.load_dataset")
    def test_loads_from_huggingface(self, mock_load_ds, tmp_path):
        """Without cache, loads from HuggingFace datasets."""
        mock_load_ds.return_value = [
            {"instance_id": f"django__django-{i}"} for i in range(500)
        ]
        cache = tmp_path / "verified_ids.json"
        ids = load_verified_instance_ids(
            max_problems=10, seed=SELECTION_SEED, cache_file=cache,
        )
        assert len(ids) == 10
        # Should be sorted for deterministic order
        assert ids == sorted(ids)
        # Should have written cache
        assert cache.exists()
        cached = json.loads(cache.read_text())
        assert cached["seed"] == SELECTION_SEED
        assert cached["max_problems"] == 10
        assert len(cached["instance_ids"]) == 10

    @patch("datasets.load_dataset")
    def test_deterministic_selection(self, mock_load_ds, tmp_path):
        """Same seed + same data = same selection."""
        all_ids = [{"instance_id": f"repo__proj-{i}"} for i in range(500)]
        mock_load_ds.return_value = all_ids

        cache1 = tmp_path / "cache1.json"
        cache2 = tmp_path / "cache2.json"
        ids1 = load_verified_instance_ids(
            max_problems=50, seed=42, cache_file=cache1,
        )
        ids2 = load_verified_instance_ids(
            max_problems=50, seed=42, cache_file=cache2,
        )
        assert ids1 == ids2

    @patch("datasets.load_dataset")
    def test_different_seed_different_selection(self, mock_load_ds, tmp_path):
        """Different seed = different selection."""
        all_ids = [{"instance_id": f"repo__proj-{i}"} for i in range(500)]
        mock_load_ds.return_value = all_ids

        cache1 = tmp_path / "cache1.json"
        cache2 = tmp_path / "cache2.json"
        ids1 = load_verified_instance_ids(
            max_problems=50, seed=42, cache_file=cache1,
        )
        ids2 = load_verified_instance_ids(
            max_problems=50, seed=99, cache_file=cache2,
        )
        assert ids1 != ids2


# ---------------------------------------------------------------------------
# Test: parse_traj_file
# ---------------------------------------------------------------------------

class TestParseTrajFile:
    def test_parse_successful_submission(self, tmp_path):
        _write_traj(tmp_path, "django__django-12345", success=True, num_steps=15, cost=2.0)
        result = parse_traj_file(tmp_path, "django__django-12345")
        assert result is not None
        assert result.instance_id == "django__django-12345"
        assert result.success is True
        assert result.num_steps == 15
        assert result.total_cost == 2.0
        assert result.tokens_sent == 15000
        assert result.tokens_received == 1500

    def test_parse_failed_run(self, tmp_path):
        _write_traj(tmp_path, "sympy__sympy-99999", success=False, num_steps=20)
        result = parse_traj_file(tmp_path, "sympy__sympy-99999")
        assert result is not None
        assert result.success is False
        assert result.exit_status == "failed"

    def test_parse_missing_traj(self, tmp_path):
        result = parse_traj_file(tmp_path, "nonexistent__repo-123")
        assert result is None

    def test_parse_corrupted_json(self, tmp_path):
        instance_dir = tmp_path / "bad__repo-1"
        instance_dir.mkdir()
        (instance_dir / "bad__repo-1.traj").write_text("not json")
        result = parse_traj_file(tmp_path, "bad__repo-1")
        assert result is None


# ---------------------------------------------------------------------------
# Test: GroupProgress
# ---------------------------------------------------------------------------

class TestGroupProgress:
    def test_save_and_load(self, tmp_path):
        progress = GroupProgress()
        progress.completed["inst-1"] = InstanceResult(
            instance_id="inst-1",
            group="control",
            success=True,
            tokens_sent=5000,
            tokens_received=500,
            total_cost=1.5,
            num_steps=10,
        )
        progress.failed["inst-2"] = "Timeout"

        progress_file = tmp_path / "progress.json"
        progress.save(progress_file)

        loaded = GroupProgress.load(progress_file)
        assert "inst-1" in loaded.completed
        assert loaded.completed["inst-1"].success is True
        assert loaded.completed["inst-1"].total_cost == 1.5
        assert loaded.failed["inst-2"] == "Timeout"

    def test_load_nonexistent(self, tmp_path):
        progress = GroupProgress.load(tmp_path / "nope.json")
        assert len(progress.completed) == 0
        assert len(progress.failed) == 0

    def test_load_corrupted_file(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json")
        progress = GroupProgress.load(bad_file)
        assert len(progress.completed) == 0

    def test_resume_skips_completed(self):
        progress = GroupProgress()
        progress.completed["done-1"] = InstanceResult(
            instance_id="done-1", group="control", success=True,
        )
        all_ids = ["done-1", "todo-2", "todo-3"]
        remaining = [iid for iid in all_ids if iid not in progress.completed]
        assert remaining == ["todo-2", "todo-3"]


# ---------------------------------------------------------------------------
# Test: collect_results
# ---------------------------------------------------------------------------

class TestCollectResults:
    def test_basic_collection(self):
        ctrl = GroupProgress()
        ctrl.completed["p1"] = InstanceResult(
            instance_id="p1", group="control",
            success=True, tokens_sent=5000, tokens_received=500,
        )
        ctrl.completed["p2"] = InstanceResult(
            instance_id="p2", group="control",
            success=False, tokens_sent=8000, tokens_received=800,
        )

        treat = GroupProgress()
        treat.completed["p1"] = InstanceResult(
            instance_id="p1", group="treatment",
            success=True, tokens_sent=3000, tokens_received=300,
        )
        treat.completed["p2"] = InstanceResult(
            instance_id="p2", group="treatment",
            success=True, tokens_sent=6000, tokens_received=600,
        )

        output = collect_results(ctrl, treat)

        assert output["agent"] == "swe-agent"
        assert output["n_problems"] == 2
        assert len(output["control"]["problems"]) == 2
        assert len(output["treatment"]["problems"]) == 2

    def test_common_ids_only(self):
        """Only problems completed in both groups are included."""
        ctrl = GroupProgress()
        ctrl.completed["p1"] = InstanceResult(
            instance_id="p1", group="control", success=True,
            tokens_sent=1000, tokens_received=100,
        )
        ctrl.completed["p2"] = InstanceResult(
            instance_id="p2", group="control", success=False,
            tokens_sent=2000, tokens_received=200,
        )

        treat = GroupProgress()
        treat.completed["p1"] = InstanceResult(
            instance_id="p1", group="treatment", success=True,
            tokens_sent=1000, tokens_received=100,
        )
        # p2 not in treatment, p3 only in treatment
        treat.completed["p3"] = InstanceResult(
            instance_id="p3", group="treatment", success=True,
            tokens_sent=1000, tokens_received=100,
        )

        output = collect_results(ctrl, treat)
        assert output["n_problems"] == 1
        assert output["control"]["problems"][0]["id"] == "p1"
        assert output["treatment"]["problems"][0]["id"] == "p1"

    def test_empty_results(self):
        output = collect_results(GroupProgress(), GroupProgress())
        assert output["n_problems"] == 0
        assert output["control"]["problems"] == []
        assert output["treatment"]["problems"] == []

    def test_output_json_schema(self):
        """Verify the output matches the expected schema for the analysis pipeline."""
        ctrl = GroupProgress()
        treat = GroupProgress()
        for iid in ["p1", "p2"]:
            ctrl.completed[iid] = InstanceResult(
                instance_id=iid, group="control",
                success=True, tokens_sent=5000, tokens_received=500,
            )
            treat.completed[iid] = InstanceResult(
                instance_id=iid, group="treatment",
                success=True, tokens_sent=3000, tokens_received=300,
            )

        output = collect_results(ctrl, treat)

        # Verify it's JSON-serializable
        json.dumps(output)

        # Verify schema
        assert "agent" in output
        assert "n_problems" in output
        for group in ("control", "treatment"):
            assert "problems" in output[group]
            for prob in output[group]["problems"]:
                assert "id" in prob
                assert "attempts" in prob
                assert "tokens" in prob
                assert isinstance(prob["attempts"], list)
                assert isinstance(prob["tokens"], list)
                assert len(prob["attempts"]) == len(prob["tokens"])


# ---------------------------------------------------------------------------
# Test: Config files exist and are valid YAML
# ---------------------------------------------------------------------------

class TestConfigs:
    def test_control_config_exists(self):
        assert CONFIG_MAP["control"].exists(), "Control config not found"

    def test_treatment_config_exists(self):
        assert CONFIG_MAP["treatment"].exists(), "Treatment config not found"

    def test_control_config_valid_yaml(self):
        import yaml
        with open(CONFIG_MAP["control"]) as f:
            config = yaml.safe_load(f)
        assert "agent" in config
        assert "model" in config["agent"]
        assert "tools" in config["agent"]

    def test_treatment_config_has_query_memory(self):
        import yaml
        with open(CONFIG_MAP["treatment"]) as f:
            config = yaml.safe_load(f)
        # Treatment must have query_memory in bundles
        bundles = config["agent"]["tools"]["bundles"]
        bundle_paths = [b["path"] for b in bundles]
        assert "tools/query_memory" in bundle_paths

    def test_treatment_has_neo4j_env(self):
        import yaml
        with open(CONFIG_MAP["treatment"]) as f:
            config = yaml.safe_load(f)
        env_vars = config["agent"]["tools"]["env_variables"]
        assert "NEO4J_URI" in env_vars
        assert "NEO4J_USER" in env_vars
        assert "NEO4J_PASSWORD" in env_vars

    def test_control_does_not_have_query_memory(self):
        import yaml
        with open(CONFIG_MAP["control"]) as f:
            config = yaml.safe_load(f)
        bundles = config["agent"]["tools"]["bundles"]
        bundle_paths = [b["path"] for b in bundles]
        assert "tools/query_memory" not in bundle_paths

    def test_both_configs_same_model(self):
        """Control and treatment must use the same model for fair comparison."""
        import yaml
        with open(CONFIG_MAP["control"]) as f:
            ctrl = yaml.safe_load(f)
        with open(CONFIG_MAP["treatment"]) as f:
            treat = yaml.safe_load(f)
        assert ctrl["agent"]["model"]["name"] == treat["agent"]["model"]["name"]

    def test_both_configs_same_cost_limit(self):
        import yaml
        with open(CONFIG_MAP["control"]) as f:
            ctrl = yaml.safe_load(f)
        with open(CONFIG_MAP["treatment"]) as f:
            treat = yaml.safe_load(f)
        assert (
            ctrl["agent"]["model"]["per_instance_cost_limit"]
            == treat["agent"]["model"]["per_instance_cost_limit"]
        )


# ---------------------------------------------------------------------------
# Test: Tool bundle structure
# ---------------------------------------------------------------------------

class TestQueryMemoryBundle:
    BUNDLE_DIR = REPO_ROOT / "tools" / "query_memory"

    def test_config_yaml_exists(self):
        assert (self.BUNDLE_DIR / "config.yaml").exists()

    def test_bin_script_exists(self):
        assert (self.BUNDLE_DIR / "bin" / "query_memory").exists()

    def test_bin_script_is_executable(self):
        script = self.BUNDLE_DIR / "bin" / "query_memory"
        assert script.stat().st_mode & 0o111, "Script must be executable"

    def test_install_sh_exists(self):
        assert (self.BUNDLE_DIR / "install.sh").exists()

    def test_config_yaml_valid(self):
        import yaml
        with open(self.BUNDLE_DIR / "config.yaml") as f:
            config = yaml.safe_load(f)
        assert "tools" in config
        assert "query_memory" in config["tools"]
        tool_def = config["tools"]["query_memory"]
        assert "signature" in tool_def
        assert "docstring" in tool_def
        assert "arguments" in tool_def
        # Must have 3 arguments
        assert len(tool_def["arguments"]) == 3

    @pytest.mark.skipif(
        not (REPO_ROOT / "tools" / "query_memory" / "lib").exists(),
        reason="lib/ is a build artifact created by install.sh, not tracked in git",
    )
    def test_lib_has_agent_memory(self):
        lib_dir = self.BUNDLE_DIR / "lib" / "agent_memory"
        assert lib_dir.exists(), "lib/agent_memory should be bundled"
        assert (lib_dir / "__init__.py").exists()


# ---------------------------------------------------------------------------
# Test: run_group_sequential (mocked SWE-agent subprocess)
# ---------------------------------------------------------------------------

class TestRunGroupSequential:
    @patch("scripts.run_real_swe_experiment.run_swe_agent_batch")
    def test_sequential_run_completes(self, mock_batch, tmp_path):
        """Test that sequential runner processes instances and saves progress."""
        instance_ids = ["p1", "p2"]

        # Mock SWE-agent batch returning success
        mock_batch.return_value = MagicMock(returncode=0, stderr="")

        # Create .traj files as if SWE-agent produced them
        for iid in instance_ids:
            _write_traj(tmp_path, iid, success=True, num_steps=10, cost=1.0)

        progress = run_group_sequential(
            group="control",
            instance_ids=instance_ids,
            output_dir=tmp_path,
        )

        assert mock_batch.call_count == 1
        assert len(progress.completed) == 2
        assert "p1" in progress.completed
        assert "p2" in progress.completed
        assert progress.completed["p1"].success is True

    @patch("scripts.run_real_swe_experiment.run_swe_agent_batch")
    def test_resume_skips_completed(self, mock_batch, tmp_path):
        """Test that already-completed instances are skipped on resume."""
        # Pre-populate progress with p1 completed
        progress = GroupProgress()
        progress.completed["p1"] = InstanceResult(
            instance_id="p1", group="control", success=True,
        )
        progress.save(tmp_path / "progress.json")

        # Create traj for p2
        _write_traj(tmp_path, "p2", success=True, num_steps=5, cost=0.5)
        mock_batch.return_value = MagicMock(returncode=0, stderr="")

        result = run_group_sequential(
            group="control",
            instance_ids=["p1", "p2"],
            output_dir=tmp_path,
        )

        # Batch should be called once (for remaining instance p2)
        assert mock_batch.call_count == 1
        assert len(result.completed) == 2

    @patch("scripts.run_real_swe_experiment.run_swe_agent_batch")
    def test_dry_run(self, mock_batch, tmp_path):
        """Dry run should not invoke SWE-agent."""
        progress = run_group_sequential(
            group="control",
            instance_ids=["p1", "p2"],
            output_dir=tmp_path,
            dry_run=True,
        )
        mock_batch.assert_not_called()
        assert len(progress.completed) == 0

    @patch("scripts.run_real_swe_experiment.run_swe_agent_batch")
    def test_handles_timeout(self, mock_batch, tmp_path):
        """Test that subprocess timeout is handled gracefully."""
        import subprocess
        mock_batch.side_effect = subprocess.TimeoutExpired(cmd="sweagent", timeout=1800)

        progress = run_group_sequential(
            group="control",
            instance_ids=["p1"],
            output_dir=tmp_path,
        )

        assert len(progress.completed) == 0
        assert "p1" in progress.failed
        assert "Timeout" in progress.failed["p1"]

    @patch("scripts.run_real_swe_experiment.run_swe_agent_batch")
    def test_handles_no_traj_output(self, mock_batch, tmp_path):
        """Test when SWE-agent exits but produces no .traj file."""
        mock_batch.return_value = MagicMock(returncode=1, stderr="Docker error")

        progress = run_group_sequential(
            group="control",
            instance_ids=["p1"],
            output_dir=tmp_path,
        )

        assert len(progress.completed) == 0
        assert "p1" in progress.failed
        assert "No .traj file" in progress.failed["p1"]
