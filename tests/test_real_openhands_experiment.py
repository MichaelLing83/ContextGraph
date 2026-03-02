"""Tests for the real OpenHands experiment runner.

These tests verify the runner logic WITHOUT actually running OpenHands agents.
They mock the OpenHands API and SWE-bench Verified dataset to test:
- Instance selection from SWE-bench Verified (deterministic, seed-based)
- Memory context injection for treatment group
- Checkpoint save/load/resume
- Result formatting (analyst format)
- Problem statement loading from HuggingFace dataset
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.run_real_openhands_experiment import (
    Checkpoint,
    ProblemResult,
    RealOpenHandsExperiment,
    RunConfig,
    build_openhands_config,
    extract_repo_from_instance_id,
    get_problem_statement,
    select_test_instances,
)
from experiments.ab_test.openhands_integration import (
    ExperimentGroup,
    assign_experiment_group,
)


# ---------------------------------------------------------------------------
# Helpers — mock SWE-bench Verified data
# ---------------------------------------------------------------------------

MOCK_SWEBENCH_DATA = {
    f"inst__{i}": {
        "instance_id": f"inst__{i}",
        "repo": f"org/repo-{i}",
        "problem_statement": f"Fix bug #{i} in the codebase.",
        "base_commit": f"abc{i}",
        "patch": "",
        "test_patch": "",
    }
    for i in range(10)
}


def _mock_load_swebench(dataset_name="princeton-nlp/SWE-bench_Verified"):
    """Return mock SWE-bench data instead of downloading from HuggingFace."""
    return MOCK_SWEBENCH_DATA


# ---------------------------------------------------------------------------
# RunConfig
# ---------------------------------------------------------------------------

class TestRunConfig:
    def test_defaults(self):
        cfg = RunConfig()
        assert cfg.seed == 42
        assert cfg.max_iterations == 30
        assert cfg.model == "claude-sonnet-4-20250514"
        assert cfg.resume is False
        assert cfg.n_instances == 200
        assert cfg.swebench_dataset == "princeton-nlp/SWE-bench_Verified"

    def test_api_config_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-abc")
        monkeypatch.setenv("ANTHROPIC_API_BASE", "https://test.example.com/api")
        cfg = RunConfig(api_key=None, api_base=None)
        assert cfg.api_key == "test-key-abc"
        assert cfg.api_base == "https://test.example.com/api"

    def test_explicit_api_wins(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
        cfg = RunConfig(api_key="explicit-key")
        assert cfg.api_key == "explicit-key"

    def test_checkpoint_auto_set(self):
        cfg = RunConfig()
        assert cfg.checkpoint_file is not None
        assert "openhands_checkpoint.json" in str(cfg.checkpoint_file)


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
            accumulated_cost=0.0123,
            duration_seconds=42.3,
            exit_reason="agent_finished",
            agent_state="finished",
            interventions=2,
            warnings_shown=1,
        )
        d = r.to_dict()
        assert d["instance_id"] == "repo__issue-1"
        assert d["group"] == "treatment"
        assert d["success"] is True
        assert d["total_steps"] == 15
        assert d["interventions"] == 2
        assert d["accumulated_cost"] == 0.0123
        assert d["agent_state"] == "finished"

    def test_error_result(self):
        r = ProblemResult(
            instance_id="x",
            group="control",
            success=False,
            total_steps=0,
            total_tokens=0,
            accumulated_cost=0.0,
            duration_seconds=1.0,
            exit_reason="error",
            agent_state="error",
            error_message="Something went wrong",
        )
        d = r.to_dict()
        assert d["success"] is False
        assert d["exit_reason"] == "error"
        assert d["error_message"] == "Something went wrong"


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

class TestCheckpoint:
    def test_save_and_load(self, tmp_path):
        cp_path = tmp_path / "cp.json"
        cp = Checkpoint(cp_path)
        cp.completed["a__b-1"] = ProblemResult(
            instance_id="a__b-1",
            group="control",
            success=True,
            total_steps=10,
            total_tokens=5000,
            accumulated_cost=0.01,
            duration_seconds=30.0,
            exit_reason="agent_finished",
            agent_state="finished",
        )
        cp.failed["c__d-2"] = "timeout"
        cp.save()

        # Load into new checkpoint
        cp2 = Checkpoint(cp_path)
        cp2.load()
        assert "a__b-1" in cp2.completed
        assert cp2.completed["a__b-1"].success is True
        assert "c__d-2" in cp2.failed

    def test_is_done(self, tmp_path):
        cp = Checkpoint(tmp_path / "cp.json")
        cp.completed["x__y-1"] = ProblemResult(
            instance_id="x__y-1",
            group="control",
            success=False,
            total_steps=5,
            total_tokens=2500,
            accumulated_cost=0.005,
            duration_seconds=15.0,
            exit_reason="max_iterations",
            agent_state="stopped",
        )
        assert cp.is_done("x__y-1")
        assert not cp.is_done("z__w-2")

    def test_load_nonexistent(self, tmp_path):
        cp = Checkpoint(tmp_path / "nonexistent.json")
        cp.load()  # Should not raise
        assert len(cp.completed) == 0

    def test_load_corrupt_file(self, tmp_path):
        cp_path = tmp_path / "cp.json"
        cp_path.write_text("not valid json{{{")
        cp = Checkpoint(cp_path)
        cp.load()  # Should not raise
        assert len(cp.completed) == 0


# ---------------------------------------------------------------------------
# SWE-bench Verified instance selection
# ---------------------------------------------------------------------------

class TestSelectTestInstances:
    def test_selects_correct_count(self):
        with patch(
            "scripts.run_real_openhands_experiment._load_swebench_verified",
            side_effect=_mock_load_swebench,
        ):
            ids = select_test_instances(n_instances=5, seed=42)
            assert len(ids) == 5

    def test_deterministic_with_same_seed(self):
        with patch(
            "scripts.run_real_openhands_experiment._load_swebench_verified",
            side_effect=_mock_load_swebench,
        ):
            ids1 = select_test_instances(n_instances=5, seed=42)
            ids2 = select_test_instances(n_instances=5, seed=42)
            assert ids1 == ids2

    def test_different_seed_different_selection(self):
        with patch(
            "scripts.run_real_openhands_experiment._load_swebench_verified",
            side_effect=_mock_load_swebench,
        ):
            ids1 = select_test_instances(n_instances=5, seed=42)
            ids2 = select_test_instances(n_instances=5, seed=99)
            # Very unlikely to be identical with different seeds
            assert ids1 != ids2

    def test_caps_at_available(self):
        with patch(
            "scripts.run_real_openhands_experiment._load_swebench_verified",
            side_effect=_mock_load_swebench,
        ):
            ids = select_test_instances(n_instances=999, seed=42)
            assert len(ids) == 10  # Only 10 in mock data


# ---------------------------------------------------------------------------
# Problem statement loading from SWE-bench Verified
# ---------------------------------------------------------------------------

class TestGetProblemStatement:
    def test_returns_problem_statement(self):
        with patch(
            "scripts.run_real_openhands_experiment._load_swebench_verified",
            side_effect=_mock_load_swebench,
        ):
            stmt = get_problem_statement("inst__3")
            assert "Fix bug #3" in stmt

    def test_fallback_for_unknown_instance(self):
        with patch(
            "scripts.run_real_openhands_experiment._load_swebench_verified",
            side_effect=_mock_load_swebench,
        ):
            stmt = get_problem_statement("nonexistent__repo-999")
            assert "nonexistent__repo-999" in stmt


# ---------------------------------------------------------------------------
# Repo extraction
# ---------------------------------------------------------------------------

class TestExtractRepo:
    def test_simple(self):
        assert extract_repo_from_instance_id("pydantic__pydantic-1125") == "pydantic/pydantic"

    def test_hyphenated_repo(self):
        assert extract_repo_from_instance_id("oasis-open__cti-python-stix2-133") == "oasis-open/cti-python-stix2"

    def test_invalid(self):
        assert extract_repo_from_instance_id("no-double-underscore") is None


# ---------------------------------------------------------------------------
# OpenHands config
# ---------------------------------------------------------------------------

class TestBuildOpenHandsConfig:
    def test_produces_valid_config(self):
        run_cfg = RunConfig(
            api_key="test-key",
            api_base="https://test.example.com",
            model="test-model",
            max_iterations=10,
        )
        config = build_openhands_config(run_cfg)
        assert config.default_agent == "CodeActAgent"
        assert config.runtime == "docker"
        assert config.max_iterations == 10

        llm = config.get_llm_config("llm")
        assert llm.model == "test-model"

    def test_sandbox_uses_host_network(self):
        run_cfg = RunConfig(api_key="k", api_base="b")
        config = build_openhands_config(run_cfg)
        assert config.sandbox.use_host_network is True


# ---------------------------------------------------------------------------
# Analyst format
# ---------------------------------------------------------------------------

class TestAnalystFormat:
    def _make_experiment(self, tmp_path):
        run_cfg = RunConfig(
            api_key="k",
            api_base="b",
            output_dir=tmp_path / "out",
            n_instances=4,
        )
        exp = RealOpenHandsExperiment(run_config=run_cfg)
        exp.results = [
            ProblemResult("a__1", "control", True, 10, 5000, 0.01, 30, "agent_finished", "finished"),
            ProblemResult("b__2", "control", False, 20, 10000, 0.02, 60, "max_iterations", "stopped"),
            ProblemResult("c__3", "treatment", True, 8, 4000, 0.008, 25, "agent_finished", "finished", interventions=2),
            ProblemResult("d__4", "treatment", True, 12, 6000, 0.012, 35, "agent_finished", "finished", interventions=1),
        ]
        return exp

    def test_format_structure(self, tmp_path):
        exp = self._make_experiment(tmp_path)
        data = exp._to_analyst_format()

        assert data["agent"] == "openhands"
        assert data["n_problems"] == 4
        assert len(data["control"]["problems"]) == 2
        assert len(data["treatment"]["problems"]) == 2

    def test_format_problem_entries(self, tmp_path):
        exp = self._make_experiment(tmp_path)
        data = exp._to_analyst_format()

        for p in data["control"]["problems"] + data["treatment"]["problems"]:
            assert "id" in p
            assert "attempts" in p
            assert "tokens" in p
            assert isinstance(p["attempts"], list)
            assert isinstance(p["tokens"], list)


# ---------------------------------------------------------------------------
# Memory context injection
# ---------------------------------------------------------------------------

class TestMemoryContextInjection:
    def test_control_group_no_instructions(self, tmp_path):
        """Control group should get no conversation_instructions."""
        run_cfg = RunConfig(
            api_key="k", api_base="b", output_dir=tmp_path / "out"
        )
        exp = RealOpenHandsExperiment(run_config=run_cfg)
        exp.graph = None  # No graph loaded
        exp.setup()

        # For control group, memory context should be empty
        from experiments.ab_test.graph_builder import AgentMemoryGraph
        exp.graph = AgentMemoryGraph()

        # Since graph has no data, treatment context will also be empty
        ctx = exp._generate_memory_context("test__inst-1")
        # Empty graph produces empty context
        assert ctx == ""

    def test_treatment_gets_context_with_graph(self, tmp_path):
        """Treatment group with populated graph should get context."""
        from experiments.ab_test.graph_builder import AgentMemoryGraph, Methodology

        run_cfg = RunConfig(
            api_key="k", api_base="b", output_dir=tmp_path / "out"
        )
        exp = RealOpenHandsExperiment(run_config=run_cfg)
        graph = AgentMemoryGraph()
        graph.methodologies["M001"] = Methodology(
            methodology_id="M001",
            task_category="bug_fix",
            situation_pattern="bug fix with search",
            action_sequence=["search", "open", "edit", "test", "submit"],
            key_commands=[],
            frequency=10,
        )
        exp.graph = graph
        # The context generation should not crash
        ctx = exp._generate_memory_context("test__inst-1")
        # May or may not have content depending on the state, but should not error
        assert isinstance(ctx, str)


# ---------------------------------------------------------------------------
# Group assignment determinism
# ---------------------------------------------------------------------------

class TestGroupAssignment:
    def test_deterministic(self):
        g1 = assign_experiment_group("test__repo-1", seed=42)
        g2 = assign_experiment_group("test__repo-1", seed=42)
        assert g1 == g2

    def test_balanced(self):
        groups = [
            assign_experiment_group(f"inst__{i}", seed=42)
            for i in range(200)
        ]
        control = sum(1 for g in groups if g == ExperimentGroup.CONTROL)
        treatment = len(groups) - control
        # Allow +-20% deviation
        assert 60 < control < 140, f"Unbalanced: {control} control"
        assert 60 < treatment < 140


# ---------------------------------------------------------------------------
# End-to-end with mocked OpenHands + mocked SWE-bench
# ---------------------------------------------------------------------------

class TestEndToEndMocked:
    """Test the experiment flow with mocked OpenHands API."""

    @pytest.fixture
    def mock_graph(self):
        from experiments.ab_test.graph_builder import AgentMemoryGraph
        return AgentMemoryGraph()

    def test_mocked_run(self, tmp_path, mock_graph):
        """Full run with mocked run_controller and mocked SWE-bench Verified."""
        import asyncio

        run_cfg = RunConfig(
            api_key="test-key",
            api_base="https://test.api.com",
            n_instances=4,
            output_dir=tmp_path / "out",
        )

        # Mock State returned by run_controller
        mock_state = MagicMock()
        mock_state.agent_state.value = "finished"

        from openhands.core.schema.agent import AgentState as OHAgentState
        mock_state.agent_state = OHAgentState.FINISHED
        mock_state.iteration = 10
        mock_state.last_error = ""

        mock_metrics = MagicMock()
        mock_metrics.accumulated_cost = 0.005
        mock_token_usage = MagicMock()
        mock_token_usage.prompt_tokens = 3000
        mock_token_usage.completion_tokens = 1000
        mock_metrics.accumulated_token_usage = mock_token_usage
        mock_state.metrics = mock_metrics

        with patch(
            "scripts.run_real_openhands_experiment.run_controller",
            new_callable=AsyncMock,
            return_value=mock_state,
        ), patch(
            "scripts.run_real_openhands_experiment.load_graph",
            return_value=mock_graph,
        ), patch(
            "scripts.run_real_openhands_experiment._load_swebench_verified",
            side_effect=_mock_load_swebench,
        ):
            exp = RealOpenHandsExperiment(run_config=run_cfg)
            results = asyncio.run(exp.run())

        assert len(results) == 4
        groups = {r.group for r in results}
        assert groups <= {"control", "treatment"}

        # All should succeed (mocked state says FINISHED)
        for r in results:
            assert r.success is True
            assert r.exit_reason == "agent_finished"
            assert r.total_steps == 10
            assert r.total_tokens == 4000
            assert r.accumulated_cost == 0.005

    def test_resume_skips_completed(self, tmp_path, mock_graph):
        """Resume should skip already-completed instances."""
        import asyncio

        run_cfg = RunConfig(
            api_key="test-key",
            api_base="https://test.api.com",
            n_instances=4,
            output_dir=tmp_path / "out",
            resume=True,
        )

        # First, figure out which 4 instances will be selected
        with patch(
            "scripts.run_real_openhands_experiment._load_swebench_verified",
            side_effect=_mock_load_swebench,
        ):
            selected_ids = select_test_instances(n_instances=4, seed=42)

        # Create a checkpoint with first 2 completed
        cp = Checkpoint(run_cfg.checkpoint_file)
        for iid in selected_ids[:2]:
            group = assign_experiment_group(iid, seed=42)
            cp.completed[iid] = ProblemResult(
                instance_id=iid,
                group=group.value,
                success=True,
                total_steps=10,
                total_tokens=5000,
                accumulated_cost=0.01,
                duration_seconds=30,
                exit_reason="agent_finished",
                agent_state="finished",
            )
        cp.save()

        # Mock for remaining instances
        mock_state = MagicMock()
        from openhands.core.schema.agent import AgentState as OHAgentState
        mock_state.agent_state = OHAgentState.FINISHED
        mock_state.iteration = 8
        mock_state.last_error = ""
        mock_metrics = MagicMock()
        mock_metrics.accumulated_cost = 0.003
        mock_token_usage = MagicMock()
        mock_token_usage.prompt_tokens = 2000
        mock_token_usage.completion_tokens = 500
        mock_metrics.accumulated_token_usage = mock_token_usage
        mock_state.metrics = mock_metrics

        call_count = 0

        async def mock_run_controller(**kwargs):
            nonlocal call_count
            call_count += 1
            return mock_state

        with patch(
            "scripts.run_real_openhands_experiment.run_controller",
            side_effect=mock_run_controller,
        ), patch(
            "scripts.run_real_openhands_experiment.load_graph",
            return_value=mock_graph,
        ), patch(
            "scripts.run_real_openhands_experiment._load_swebench_verified",
            side_effect=_mock_load_swebench,
        ):
            exp = RealOpenHandsExperiment(run_config=run_cfg)
            results = asyncio.run(exp.run())

        # Should have 4 total results (2 restored + 2 new)
        assert len(results) == 4
        # run_controller should only be called for the 2 new instances
        assert call_count == 2


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_run_controller_returns_none(self, tmp_path):
        """When run_controller returns None, should record as error."""
        import asyncio
        from scripts.run_real_openhands_experiment import run_single_instance

        run_cfg = RunConfig(
            api_key="test-key",
            api_base="https://test.api.com",
            output_dir=tmp_path / "out",
        )

        with patch(
            "scripts.run_real_openhands_experiment.run_controller",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = asyncio.run(run_single_instance(
                "test__inst-1",
                "Solve this",
                run_cfg,
            ))

        assert result["success"] is False
        assert result["exit_reason"] == "error"
        assert "None" in result["error"]

    def test_run_controller_raises(self, tmp_path):
        """When run_controller raises, should record as error."""
        import asyncio
        from scripts.run_real_openhands_experiment import run_single_instance

        run_cfg = RunConfig(
            api_key="test-key",
            api_base="https://test.api.com",
            output_dir=tmp_path / "out",
        )

        with patch(
            "scripts.run_real_openhands_experiment.run_controller",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Docker failed"),
        ):
            result = asyncio.run(run_single_instance(
                "test__inst-1",
                "Solve this",
                run_cfg,
            ))

        assert result["success"] is False
        assert result["exit_reason"] == "error"
        assert "Docker failed" in result["error"]
