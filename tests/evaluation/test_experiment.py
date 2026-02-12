"""Tests for experiment pipeline."""

import json

import pytest

from agent_memory.evaluation.experiment import (
    ExperimentResult,
    SimulationConfig,
    TrajectoryInfo,
    run_experiment,
    simulate_control,
    simulate_treatment,
    parse_trajectory_info,
    _deterministic_random,
)
from agent_memory.evaluation.metrics import ProblemResult, calculate_metrics


def _make_traj_file(tmp_path, instance_id, success=True, num_steps=10):
    """Create a minimal .traj file for testing."""
    exit_status = "submitted" if success else "failed"
    data = {
        "trajectory": [
            {"action": f"action_{i}", "observation": f"obs_{i}", "thought": f"thought_{i}"}
            for i in range(num_steps)
        ],
        "exit_status": exit_status,
        "info": {},
        "problem_statement": "Fix bug in module",
    }
    path = tmp_path / f"{instance_id}.traj"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestSimulationConfig:
    def test_defaults(self):
        cfg = SimulationConfig()
        assert cfg.seed == 42
        assert cfg.num_attempts == 5
        assert cfg.token_reduction_factor == 0.15
        assert cfg.success_boost == 0.10


class TestDeterministicRandom:
    def test_reproducibility(self):
        val1 = _deterministic_random("problem-1", 0, 42)
        val2 = _deterministic_random("problem-1", 0, 42)
        assert val1 == val2

    def test_different_inputs_differ(self):
        val1 = _deterministic_random("problem-1", 0, 42)
        val2 = _deterministic_random("problem-2", 0, 42)
        assert val1 != val2

    def test_range(self):
        for i in range(100):
            val = _deterministic_random(f"p-{i}", i, 42)
            assert 0.0 <= val <= 1.0


class TestTrajectoryInfo:
    def test_parse_trajectory_info(self, tmp_path):
        path = _make_traj_file(tmp_path, "django__django-12345", success=True, num_steps=8)
        info = parse_trajectory_info(path)
        assert info.instance_id == "django__django-12345"
        assert info.success is True
        assert info.total_steps == 8
        assert info.estimated_tokens == 8 * 800

    def test_parse_failed_trajectory(self, tmp_path):
        path = _make_traj_file(tmp_path, "django__django-99999", success=False, num_steps=20)
        info = parse_trajectory_info(path)
        assert info.success is False
        assert info.total_steps == 20


class TestSimulateControl:
    def test_first_attempt_matches_original(self):
        traj = TrajectoryInfo(
            instance_id="test-1",
            success=True,
            total_steps=10,
            estimated_tokens=8000,
        )
        config = SimulationConfig(seed=42, num_attempts=5)
        result = simulate_control(traj, config)

        assert result.problem_id == "test-1"
        assert result.attempts[0] is True  # First attempt mirrors original
        assert len(result.attempts) == 5
        assert len(result.tokens) == 5
        assert result.tokens[0] == 8000

    def test_failed_first_attempt(self):
        traj = TrajectoryInfo(
            instance_id="test-2",
            success=False,
            total_steps=15,
            estimated_tokens=12000,
        )
        config = SimulationConfig(seed=42, num_attempts=3)
        result = simulate_control(traj, config)

        assert result.attempts[0] is False
        assert len(result.attempts) == 3

    def test_reproducible(self):
        traj = TrajectoryInfo(
            instance_id="test-3",
            success=True,
            total_steps=10,
            estimated_tokens=8000,
        )
        config = SimulationConfig(seed=42, num_attempts=5)
        r1 = simulate_control(traj, config)
        r2 = simulate_control(traj, config)

        assert r1.attempts == r2.attempts
        assert r1.tokens == r2.tokens


class TestSimulateTreatment:
    def test_preserves_original_success(self):
        traj = TrajectoryInfo(
            instance_id="test-1",
            success=True,
            total_steps=10,
            estimated_tokens=8000,
        )
        config = SimulationConfig(seed=42, num_attempts=5)
        result = simulate_treatment(traj, config)

        assert result.attempts[0] is True  # Original success preserved

    def test_token_reduction(self):
        traj = TrajectoryInfo(
            instance_id="test-1",
            success=True,
            total_steps=10,
            estimated_tokens=10000,
        )
        config = SimulationConfig(
            seed=42,
            num_attempts=5,
            token_reduction_factor=0.20,
        )
        result = simulate_treatment(traj, config)

        # All tokens should be reduced
        for tok in result.tokens:
            assert tok == 8000  # 10000 * (1 - 0.20)

    def test_treatment_uses_fewer_tokens_than_control(self):
        traj = TrajectoryInfo(
            instance_id="test-1",
            success=True,
            total_steps=10,
            estimated_tokens=10000,
        )
        config = SimulationConfig(seed=42, num_attempts=5, token_reduction_factor=0.15)
        control = simulate_control(traj, config)
        treatment = simulate_treatment(traj, config)

        assert sum(treatment.tokens) < sum(control.tokens)


class TestRunExperiment:
    def test_full_pipeline(self, tmp_path):
        """Test the complete experiment pipeline end-to-end."""
        # Create trajectory files
        for i in range(10):
            success = i < 4  # 40% success rate
            _make_traj_file(tmp_path, f"test__repo-{i}", success=success, num_steps=10 + i)

        traj_files = sorted(tmp_path.glob("*.traj"))
        result = run_experiment(
            trajectory_files=traj_files,
            split_ratio=0.5,
            seed=42,
            sim_config=SimulationConfig(seed=42, num_attempts=5),
        )

        assert isinstance(result, ExperimentResult)
        assert result.split.train_count == 5
        assert result.split.test_count == 5
        assert len(result.control_results) == 5
        assert len(result.treatment_results) == 5
        assert result.report is not None

    def test_report_has_expected_fields(self, tmp_path):
        for i in range(6):
            success = i < 3
            _make_traj_file(tmp_path, f"test__repo-{i}", success=success, num_steps=10)

        traj_files = sorted(tmp_path.glob("*.traj"))
        result = run_experiment(traj_files, split_ratio=0.5, seed=42)

        report = result.report
        assert report.control.total_problems > 0
        assert report.treatment.total_problems > 0
        assert report.control.failure_ratio >= 0.0
        assert report.treatment.failure_ratio >= 0.0

    def test_treatment_has_lower_tokens(self, tmp_path):
        """Treatment group should have lower avg tokens."""
        for i in range(20):
            success = i < 8
            _make_traj_file(tmp_path, f"test__repo-{i}", success=success, num_steps=15)

        traj_files = sorted(tmp_path.glob("*.traj"))
        result = run_experiment(traj_files, split_ratio=0.5, seed=42)

        assert result.report.token_reduction > 0

    def test_reproducibility(self, tmp_path):
        for i in range(8):
            _make_traj_file(tmp_path, f"test__repo-{i}", success=i % 2 == 0, num_steps=10)

        traj_files = sorted(tmp_path.glob("*.traj"))
        r1 = run_experiment(traj_files, split_ratio=0.5, seed=42)
        r2 = run_experiment(traj_files, split_ratio=0.5, seed=42)

        assert r1.report.control.pass_at_1 == r2.report.control.pass_at_1
        assert r1.report.treatment.pass_at_1 == r2.report.treatment.pass_at_1

    def test_to_dict(self, tmp_path):
        for i in range(6):
            _make_traj_file(tmp_path, f"test__repo-{i}", success=i < 3, num_steps=10)

        traj_files = sorted(tmp_path.glob("*.traj"))
        result = run_experiment(traj_files, split_ratio=0.5, seed=42)

        d = result.to_dict()
        assert "config" in d
        assert "split" in d
        assert "control" in d
        assert "treatment" in d
        assert "comparison" in d
        assert "failure_ratio" in d["control"]["metrics"]
        assert "failure_ratio" in d["treatment"]["metrics"]
        # Verify it's JSON-serializable
        json.dumps(d)

    def test_empty_trajectories_dir(self, tmp_path):
        """Empty input should produce empty split."""
        result = run_experiment([], split_ratio=0.5, seed=42)
        assert result.split.train_count == 0
        assert result.split.test_count == 0
        assert len(result.control_results) == 0

    def test_summary_output(self, tmp_path):
        for i in range(8):
            _make_traj_file(tmp_path, f"test__repo-{i}", success=i < 4, num_steps=10)

        traj_files = sorted(tmp_path.glob("*.traj"))
        result = run_experiment(traj_files, split_ratio=0.5, seed=42)

        summary = result.report.to_summary()
        assert "pass@1" in summary.lower()
        assert "failure ratio" in summary.lower()


class TestFailureRatioMetric:
    def test_failure_ratio_all_fail(self):
        results = [
            ProblemResult("p1", [False, False, False], [100, 100, 100]),
        ]
        metrics = calculate_metrics(results)
        assert metrics.failure_ratio == 1.0

    def test_failure_ratio_all_success(self):
        results = [
            ProblemResult("p1", [True, True, True], [100, 100, 100]),
        ]
        metrics = calculate_metrics(results)
        assert metrics.failure_ratio == 0.0

    def test_failure_ratio_mixed(self):
        results = [
            ProblemResult("p1", [True, False], [100, 100]),
            ProblemResult("p2", [False, False], [100, 100]),
        ]
        metrics = calculate_metrics(results)
        # 3 failures out of 4 attempts = 0.75
        assert metrics.failure_ratio == 0.75

    def test_failure_ratio_empty(self):
        metrics = calculate_metrics([])
        assert metrics.failure_ratio == 0.0
