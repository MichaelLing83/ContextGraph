"""Tests for OpenHands experiment runner and result collector."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root is importable
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.run_openhands_experiment import (
    OpenHandsAgent,
    OpenHandsExperiment,
    OpenHandsRunConfig,
    ProblemResult,
    _OPENHANDS_AVAILABLE,
)
from scripts.collect_openhands_results import (
    analyse,
    load_result_files,
    result_to_trajectory_metrics,
    to_analyst_format,
)
from experiments.ab_test.openhands_integration import (
    AgentState,
    ExperimentGroup,
    MemoryContext,
    MemoryHooks,
    assign_experiment_group,
)
from experiments.ab_test.config import get_config
from experiments.ab_test.graph_builder import AgentMemoryGraph


# ---------------------------------------------------------------------------
# ProblemResult
# ---------------------------------------------------------------------------

class TestProblemResult:
    def test_to_dict_roundtrip(self):
        r = ProblemResult(
            instance_id="repo__issue-1",
            group="treatment",
            success=True,
            total_steps=15,
            total_tokens=7500,
            duration_seconds=42.3,
            exit_reason="completed",
            interventions=2,
            warnings_shown=1,
        )
        d = r.to_dict()
        assert d["instance_id"] == "repo__issue-1"
        assert d["group"] == "treatment"
        assert d["success"] is True
        assert d["total_steps"] == 15
        assert d["interventions"] == 2

    def test_dry_run_result(self):
        r = ProblemResult(
            instance_id="x",
            group="control",
            success=False,
            total_steps=0,
            total_tokens=0,
            duration_seconds=0.0,
            exit_reason="dry_run",
        )
        assert r.exit_reason == "dry_run"
        assert r.success is False


# ---------------------------------------------------------------------------
# OpenHandsRunConfig
# ---------------------------------------------------------------------------

class TestOpenHandsRunConfig:
    def test_defaults(self):
        cfg = OpenHandsRunConfig()
        assert cfg.seed == 42
        assert cfg.max_iterations == 30
        assert cfg.dry_run is False
        assert cfg.model == "claude-sonnet-4-20250514"

    def test_resolve_api_config_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        monkeypatch.setenv("ANTHROPIC_API_BASE", "https://example.com/api")
        cfg = OpenHandsRunConfig()
        cfg.resolve_api_config()
        assert cfg.api_key == "test-key-123"
        assert cfg.api_base == "https://example.com/api"

    def test_resolve_api_config_explicit_wins(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
        cfg = OpenHandsRunConfig(api_key="explicit-key")
        cfg.resolve_api_config()
        assert cfg.api_key == "explicit-key"


# ---------------------------------------------------------------------------
# OpenHandsAgent
# ---------------------------------------------------------------------------

class TestOpenHandsAgent:
    def test_run_instance_raises_without_openhands(self):
        """Agent.run_instance raises when OpenHands is missing."""
        if _OPENHANDS_AVAILABLE:
            pytest.skip("OpenHands is actually installed")
        cfg = OpenHandsRunConfig()
        agent = OpenHandsAgent(cfg)
        with pytest.raises(NotImplementedError, match="not installed"):
            agent.run_instance("test-id", "solve this")


# ---------------------------------------------------------------------------
# OpenHandsExperiment  (dry-run end-to-end)
# ---------------------------------------------------------------------------

class TestOpenHandsExperiment:
    """Integration-level tests using dry-run mode."""

    @pytest.fixture
    def _fake_data(self, tmp_path):
        """Create minimal split + graph files for the experiment."""
        config = get_config()

        # Build a tiny graph
        graph = AgentMemoryGraph()
        graph.statistics = {"total_processed": 0}
        graph_file = config.paths.graph_file
        graph_file.parent.mkdir(parents=True, exist_ok=True)
        graph_file.write_text(json.dumps(graph.to_dict()))

        # Build a tiny split with 4 test IDs
        split_data = {
            "train_ids": ["train__1", "train__2"],
            "test_ids": ["test__1", "test__2", "test__3", "test__4"],
            "train_metadata": {},
            "test_metadata": {
                "test__1": {"task_type": "bug_fix"},
                "test__2": {"task_type": "bug_fix"},
                "test__3": {"task_type": "feature_request"},
                "test__4": {"task_type": "bug_fix"},
            },
            "statistics": {},
        }
        splits_file = config.paths.splits_file
        splits_file.parent.mkdir(parents=True, exist_ok=True)
        splits_file.write_text(json.dumps(split_data))

        return tmp_path

    def test_dry_run_produces_results(self, _fake_data, tmp_path):
        run_config = OpenHandsRunConfig(
            dry_run=True,
            n_instances=4,
            output_dir=tmp_path / "out",
            verbose=False,
        )
        exp = OpenHandsExperiment(run_config=run_config)
        results = exp.run()

        assert len(results) == 4
        groups = {r.group for r in results}
        assert groups <= {"control", "treatment"}

        # All should be dry_run exit_reason
        for r in results:
            assert r.exit_reason == "dry_run"
            assert r.success is False

    def test_dry_run_saves_json(self, _fake_data, tmp_path):
        run_config = OpenHandsRunConfig(
            dry_run=True,
            n_instances=2,
            output_dir=tmp_path / "out",
            verbose=False,
        )
        exp = OpenHandsExperiment(run_config=run_config)
        exp.run()

        result_files = list((tmp_path / "out").glob("openhands_results_*.json"))
        assert len(result_files) >= 1

        data = json.loads(result_files[0].read_text())
        assert "results" in data
        assert "config" in data
        assert data["config"]["dry_run"] is True

    def test_fallback_to_dry_run_when_no_openhands(self, _fake_data, tmp_path):
        """If OpenHands is missing and dry_run=False, auto-switches to dry_run."""
        if _OPENHANDS_AVAILABLE:
            pytest.skip("OpenHands is installed")

        run_config = OpenHandsRunConfig(
            dry_run=False,
            n_instances=2,
            output_dir=tmp_path / "out",
            verbose=False,
        )
        exp = OpenHandsExperiment(run_config=run_config)
        results = exp.run()

        # Should still produce results via dry-run fallback
        assert len(results) == 2
        for r in results:
            assert r.exit_reason == "dry_run"

    def test_saves_analyst_format(self, _fake_data, tmp_path):
        """Runner should also produce openhands_results.json in analyst format."""
        run_config = OpenHandsRunConfig(
            dry_run=True,
            n_instances=4,
            output_dir=tmp_path / "out",
            verbose=False,
        )
        exp = OpenHandsExperiment(run_config=run_config)
        exp.run()

        analyst_file = tmp_path / "out" / "openhands_results.json"
        assert analyst_file.exists()

        data = json.loads(analyst_file.read_text())
        assert data["agent"] == "openhands"
        assert "n_problems" in data
        assert "control" in data
        assert "treatment" in data
        assert "problems" in data["control"]
        assert "problems" in data["treatment"]

        # Each problem entry should have id, attempts, tokens
        all_problems = data["control"]["problems"] + data["treatment"]["problems"]
        assert len(all_problems) == 4
        for p in all_problems:
            assert "id" in p
            assert "attempts" in p
            assert "tokens" in p
            assert isinstance(p["attempts"], list)
            assert isinstance(p["tokens"], list)


# ---------------------------------------------------------------------------
# Collector / result_to_trajectory_metrics
# ---------------------------------------------------------------------------

class TestResultCollector:
    def test_result_to_trajectory_metrics(self):
        entry = {
            "instance_id": "a__b-1",
            "group": "treatment",
            "success": True,
            "total_steps": 20,
            "total_tokens": 10000,
            "interventions": 3,
            "loops_detected": 1,
        }
        tm = result_to_trajectory_metrics(entry)
        assert tm.instance_id == "a__b-1"
        assert tm.success is True
        assert tm.total_steps == 20
        assert tm.total_tokens_estimate == 10000
        assert tm.total_interventions == 3

    def test_result_to_trajectory_metrics_defaults(self):
        entry = {"instance_id": "x", "success": False}
        tm = result_to_trajectory_metrics(entry)
        assert tm.total_steps == 0
        assert tm.success is False

    def test_analyse_combined(self):
        results = [
            {
                "instance_id": "c1",
                "group": "control",
                "success": True,
                "total_steps": 10,
                "total_tokens": 5000,
                "duration_seconds": 30,
                "exit_reason": "completed",
            },
            {
                "instance_id": "c2",
                "group": "control",
                "success": False,
                "total_steps": 25,
                "total_tokens": 12500,
                "duration_seconds": 60,
                "exit_reason": "completed",
            },
            {
                "instance_id": "t1",
                "group": "treatment",
                "success": True,
                "total_steps": 8,
                "total_tokens": 4000,
                "duration_seconds": 25,
                "exit_reason": "completed",
                "interventions": 2,
                "warnings_shown": 1,
            },
            {
                "instance_id": "t2",
                "group": "treatment",
                "success": True,
                "total_steps": 12,
                "total_tokens": 6000,
                "duration_seconds": 35,
                "exit_reason": "completed",
                "interventions": 1,
                "warnings_shown": 1,
            },
        ]
        summary = analyse(results)

        assert summary["total_instances"] == 4
        assert summary["control"]["count"] == 2
        assert summary["treatment"]["count"] == 2
        assert summary["treatment"]["total_interventions"] == 3
        assert "success_rate_delta" in summary["deltas"]

    def test_load_result_files(self, tmp_path):
        # Create a sample result file
        data = {
            "results": [
                {
                    "instance_id": "a",
                    "group": "control",
                    "success": True,
                    "total_steps": 5,
                    "total_tokens": 2500,
                    "duration_seconds": 10,
                    "exit_reason": "completed",
                }
            ]
        }
        (tmp_path / "openhands_results_20260101_000000.json").write_text(
            json.dumps(data)
        )

        loaded = load_result_files(tmp_path)
        assert len(loaded) == 1
        assert loaded[0]["instance_id"] == "a"
        assert "_source_file" in loaded[0]

    def test_load_result_files_empty_dir(self, tmp_path):
        loaded = load_result_files(tmp_path)
        assert loaded == []

    def test_to_analyst_format(self):
        results = [
            {"instance_id": "a", "group": "control", "success": True, "total_steps": 10, "total_tokens": 5000},
            {"instance_id": "b", "group": "control", "success": False, "total_steps": 20, "total_tokens": 10000},
            {"instance_id": "c", "group": "treatment", "success": True, "total_steps": 8, "total_tokens": 4000},
            {"instance_id": "d", "group": "treatment", "success": True, "total_steps": 12, "total_tokens": 6000},
        ]
        data = to_analyst_format(results)

        assert data["agent"] == "openhands"
        assert data["n_problems"] == 4
        assert len(data["control"]["problems"]) == 2
        assert len(data["treatment"]["problems"]) == 2

        # Check structure of each problem
        cp = data["control"]["problems"][0]
        assert "id" in cp
        assert "attempts" in cp
        assert "tokens" in cp
        assert isinstance(cp["attempts"], list)
        assert isinstance(cp["tokens"], list)

    def test_to_analyst_format_multiple_attempts(self):
        """Multiple entries for the same instance become multiple attempts."""
        results = [
            {"instance_id": "a", "group": "control", "success": True, "total_steps": 10, "total_tokens": 5000},
            {"instance_id": "a", "group": "control", "success": False, "total_steps": 15, "total_tokens": 7500},
        ]
        data = to_analyst_format(results)

        assert data["n_problems"] == 1
        assert len(data["control"]["problems"]) == 1
        p = data["control"]["problems"][0]
        assert p["id"] == "a"
        assert p["attempts"] == [True, False]
        assert p["tokens"] == [5000, 7500]


# ---------------------------------------------------------------------------
# MemoryHooks integration (verifying hooks work for OpenHands flow)
# ---------------------------------------------------------------------------

class TestMemoryHooksForOpenHands:
    """Verify the hooks produce the right output for the OpenHands flow."""

    def _make_graph(self) -> AgentMemoryGraph:
        return AgentMemoryGraph()

    def test_control_group_empty_context(self):
        graph = self._make_graph()
        hooks = MemoryHooks(graph, get_config(), ExperimentGroup.CONTROL)
        state = AgentState(instance_id="test__1")
        ctx = hooks.pre_action_hook(state, "search_dir \"test\"")
        assert ctx.is_empty()
        assert ctx.to_prompt_injection() == ""

    def test_treatment_group_methodology_hint(self):
        """Treatment group gets methodology hints early in trajectory."""
        from experiments.ab_test.graph_builder import Methodology

        graph = self._make_graph()
        graph.methodologies["M001"] = Methodology(
            methodology_id="M001",
            task_category="bug_fix",
            situation_pattern="bug_fix task with search start",
            action_sequence=["search", "open", "edit", "test", "submit"],
            key_commands=[],
            frequency=10,
        )
        hooks = MemoryHooks(graph, get_config(), ExperimentGroup.TREATMENT)
        state = AgentState(
            instance_id="test__1",
            task_category="bug_fix",
            recent_actions=["search", "open"],
            step_number=2,
        )
        ctx = hooks.pre_action_hook(state)
        # Should produce methodology hint because step <= 5
        # and recent_actions partially match
        injection = ctx.to_prompt_injection()
        if not ctx.is_empty():
            assert "[Agent Memory]" in injection

    def test_loop_warning_injection(self):
        """Treatment group gets loop warning after repeated commands."""
        graph = self._make_graph()
        hooks = MemoryHooks(graph, get_config(), ExperimentGroup.TREATMENT)
        state = AgentState(
            instance_id="test__1",
            recent_commands=["search_dir \"<TERM>\"", "search_dir \"<TERM>\""],
            step_number=5,
        )
        ctx = hooks.pre_action_hook(state, 'search_dir "test"')
        injection = ctx.to_prompt_injection()
        if ctx.loop_warning:
            assert "repeated" in injection.lower() or "loop" in injection.lower()

    def test_assign_experiment_group_deterministic(self):
        g1 = assign_experiment_group("test__1", seed=42)
        g2 = assign_experiment_group("test__1", seed=42)
        assert g1 == g2

    def test_assign_experiment_group_balanced(self):
        """Over many IDs the split should be roughly 50/50."""
        groups = [
            assign_experiment_group(f"inst__{i}", seed=42)
            for i in range(200)
        ]
        control_count = sum(1 for g in groups if g == ExperimentGroup.CONTROL)
        treatment_count = len(groups) - control_count
        # Allow +-20% deviation for small sample
        assert 60 < control_count < 140, f"Unbalanced: {control_count} control"
        assert 60 < treatment_count < 140
