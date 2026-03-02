"""Tests for SWE-agent live experiment runner and collector."""

import json
from pathlib import Path

import pytest

# Ensure importable
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.run_swe_agent_experiment import (
    ExperimentProgress,
    ProblemRunResult,
    load_test_problems,
    parse_traj_result,
)
from scripts.collect_swe_agent_results import (
    parse_traj_file,
    collect_results_from_dir,
    build_problem_results,
)


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


def _write_traj(tmp_path, instance_id, **kwargs):
    """Write a .traj file in SWE-agent output format."""
    instance_dir = tmp_path / instance_id
    instance_dir.mkdir(parents=True, exist_ok=True)
    traj_file = instance_dir / f"{instance_id}.traj"
    traj_file.write_text(
        json.dumps(_make_traj_data(instance_id, **kwargs)),
        encoding="utf-8",
    )
    return traj_file


class TestLoadTestProblems:
    def test_load_from_test_ids(self, tmp_path):
        split_file = tmp_path / "split.json"
        split_file.write_text(json.dumps({
            "test_ids": ["django__django-12345", "sympy__sympy-67890", "flask__flask-111"],
        }))
        problems = load_test_problems(split_file, max_problems=2)
        assert problems == ["django__django-12345", "sympy__sympy-67890"]

    def test_load_from_test_files_fallback(self, tmp_path):
        split_file = tmp_path / "split.json"
        split_file.write_text(json.dumps({
            "test_files": [
                "/data/trajectories/django__django-12345.json",
                "/data/trajectories/sympy__sympy-67890.json",
            ],
        }))
        problems = load_test_problems(split_file, max_problems=10)
        assert problems == ["django__django-12345", "sympy__sympy-67890"]

    def test_max_problems_limit(self, tmp_path):
        split_file = tmp_path / "split.json"
        ids = [f"repo__project-{i}" for i in range(50)]
        split_file.write_text(json.dumps({"test_ids": ids}))
        problems = load_test_problems(split_file, max_problems=10)
        assert len(problems) == 10

    def test_empty_split(self, tmp_path):
        split_file = tmp_path / "split.json"
        split_file.write_text(json.dumps({"test_ids": []}))
        problems = load_test_problems(split_file, max_problems=200)
        assert problems == []


class TestParseTrajResult:
    def test_parse_successful(self, tmp_path):
        _write_traj(tmp_path, "django__django-12345", success=True, num_steps=15, cost=2.0)
        result = parse_traj_result(tmp_path, "django__django-12345")
        assert result is not None
        assert result.instance_id == "django__django-12345"
        assert result.success is True
        assert result.num_steps == 15
        assert result.total_cost == 2.0
        assert result.tokens_sent == 15000
        assert result.tokens_received == 1500

    def test_parse_failed(self, tmp_path):
        _write_traj(tmp_path, "sympy__sympy-99999", success=False, num_steps=20)
        result = parse_traj_result(tmp_path, "sympy__sympy-99999")
        assert result is not None
        assert result.success is False

    def test_parse_missing_traj(self, tmp_path):
        result = parse_traj_result(tmp_path, "nonexistent__repo-123")
        assert result is None


class TestExperimentProgress:
    def test_save_and_load(self, tmp_path):
        progress = ExperimentProgress()
        progress.completed["test-1"] = ProblemRunResult(
            instance_id="test-1",
            group="control",
            success=True,
            tokens_sent=1000,
            total_cost=1.5,
        )
        progress.failed["test-2"] = "Timeout"

        progress_file = tmp_path / "progress.json"
        progress.save(progress_file)

        loaded = ExperimentProgress.load(progress_file)
        assert "test-1" in loaded.completed
        assert loaded.completed["test-1"].success is True
        assert loaded.completed["test-1"].total_cost == 1.5
        assert loaded.failed["test-2"] == "Timeout"

    def test_load_nonexistent(self, tmp_path):
        progress = ExperimentProgress.load(tmp_path / "nope.json")
        assert len(progress.completed) == 0
        assert len(progress.failed) == 0

    def test_resume_skips_completed(self, tmp_path):
        """Verify progress tracking enables resume."""
        progress = ExperimentProgress()
        progress.completed["done-1"] = ProblemRunResult(
            instance_id="done-1", group="control", success=True,
        )
        all_problems = ["done-1", "todo-2", "todo-3"]
        remaining = [p for p in all_problems if p not in progress.completed]
        assert remaining == ["todo-2", "todo-3"]


class TestParseTrajFile:
    def test_parse_success(self, tmp_path):
        traj_file = _write_traj(tmp_path, "test__repo-1", success=True, num_steps=10, cost=1.0)
        result = parse_traj_file(traj_file)
        assert result is not None
        assert result["instance_id"] == "test__repo-1"
        assert result["success"] is True
        assert result["tokens"] == 11000  # 10*1000 + 10*100
        assert result["num_steps"] == 10
        assert result["cost"] == 1.0

    def test_parse_failure(self, tmp_path):
        traj_file = _write_traj(tmp_path, "test__repo-2", success=False)
        result = parse_traj_file(traj_file)
        assert result is not None
        assert result["success"] is False

    def test_parse_invalid_json(self, tmp_path):
        bad_file = tmp_path / "bad.traj"
        bad_file.write_text("not json", encoding="utf-8")
        result = parse_traj_file(bad_file)
        assert result is None


class TestCollectResultsFromDir:
    def test_collect_from_traj_files(self, tmp_path):
        _write_traj(tmp_path, "p1", success=True, num_steps=10)
        _write_traj(tmp_path, "p2", success=False, num_steps=20)
        _write_traj(tmp_path, "p3", success=True, num_steps=5)

        results = collect_results_from_dir(tmp_path)
        assert len(results) == 3
        assert results["p1"]["success"] is True
        assert results["p2"]["success"] is False
        assert results["p3"]["success"] is True

    def test_collect_from_progress_file(self, tmp_path):
        progress_data = {
            "completed": {
                "p1": {
                    "instance_id": "p1",
                    "group": "control",
                    "success": True,
                    "tokens_sent": 5000,
                    "tokens_received": 500,
                    "total_cost": 1.0,
                    "num_steps": 8,
                },
            },
            "failed": {},
        }
        (tmp_path / "progress.json").write_text(
            json.dumps(progress_data), encoding="utf-8"
        )
        results = collect_results_from_dir(tmp_path)
        assert len(results) == 1
        assert results["p1"]["success"] is True

    def test_collect_empty_dir(self, tmp_path):
        results = collect_results_from_dir(tmp_path)
        assert len(results) == 0

    def test_collect_nonexistent_dir(self, tmp_path):
        results = collect_results_from_dir(tmp_path / "nope")
        assert len(results) == 0


class TestBuildProblemResults:
    def test_build_from_results(self):
        from agent_memory.evaluation.metrics import ProblemResult

        raw = {
            "p1": {"instance_id": "p1", "success": True, "tokens": 5000},
            "p2": {"instance_id": "p2", "success": False, "tokens": 8000},
        }
        problem_results = build_problem_results(raw)
        assert len(problem_results) == 2
        assert all(isinstance(r, ProblemResult) for r in problem_results)

        # Results should be sorted by ID
        assert problem_results[0].problem_id == "p1"
        assert problem_results[0].pass_at_1 is True
        assert problem_results[0].total_tokens == 5000
        assert problem_results[1].problem_id == "p2"
        assert problem_results[1].pass_at_1 is False

    def test_build_empty(self):
        result = build_problem_results({})
        assert result == []


class TestEndToEnd:
    """Integration test: collect from two output dirs and compare."""

    def test_collect_and_compare(self, tmp_path):
        from scripts.collect_swe_agent_results import collect_and_analyze

        control_dir = tmp_path / "control"
        treatment_dir = tmp_path / "treatment"

        # Create control results
        _write_traj(control_dir, "p1", success=True, num_steps=15, cost=2.0)
        _write_traj(control_dir, "p2", success=False, num_steps=25, cost=3.0)
        _write_traj(control_dir, "p3", success=True, num_steps=10, cost=1.5)

        # Create treatment results (better outcomes)
        _write_traj(treatment_dir, "p1", success=True, num_steps=10, cost=1.5)
        _write_traj(treatment_dir, "p2", success=True, num_steps=15, cost=2.0)
        _write_traj(treatment_dir, "p3", success=True, num_steps=8, cost=1.0)

        (
            control_results,
            treatment_results,
            report,
            raw_control,
            raw_treatment,
        ) = collect_and_analyze(control_dir, treatment_dir)

        assert len(control_results) == 3
        assert len(treatment_results) == 3

        # Treatment should have better pass@1
        assert report.treatment.pass_at_1 >= report.control.pass_at_1

        # Treatment should use fewer tokens on average
        assert report.treatment.avg_tokens_per_problem <= report.control.avg_tokens_per_problem

        # Summary should be printable
        summary = report.to_summary()
        assert "pass@1" in summary.lower()

    def test_output_json_format(self, tmp_path):
        """Verify output JSON matches analyst's expected schema."""
        from scripts.collect_swe_agent_results import collect_and_analyze

        control_dir = tmp_path / "control"
        treatment_dir = tmp_path / "treatment"

        _write_traj(control_dir, "p1", success=True, num_steps=10, cost=1.0)
        _write_traj(control_dir, "p2", success=False, num_steps=20, cost=2.0)
        _write_traj(treatment_dir, "p1", success=True, num_steps=8, cost=0.8)
        _write_traj(treatment_dir, "p2", success=True, num_steps=12, cost=1.2)

        (
            control_results,
            treatment_results,
            report,
            raw_control,
            raw_treatment,
        ) = collect_and_analyze(control_dir, treatment_dir)

        # Build the same output_data as the main() function does
        output_data = {
            "agent": "swe-agent",
            "n_problems": len(control_results),
            "control": {
                "problems": [
                    {
                        "id": r.problem_id,
                        "attempts": r.attempts,
                        "tokens": r.tokens,
                    }
                    for r in control_results
                ],
            },
            "treatment": {
                "problems": [
                    {
                        "id": r.problem_id,
                        "attempts": r.attempts,
                        "tokens": r.tokens,
                    }
                    for r in treatment_results
                ],
            },
        }

        # Validate schema
        assert output_data["agent"] == "swe-agent"
        assert output_data["n_problems"] == 2
        assert len(output_data["control"]["problems"]) == 2
        assert len(output_data["treatment"]["problems"]) == 2

        # Validate problem structure
        for group in ("control", "treatment"):
            for prob in output_data[group]["problems"]:
                assert "id" in prob
                assert "attempts" in prob
                assert "tokens" in prob
                assert isinstance(prob["attempts"], list)
                assert isinstance(prob["tokens"], list)
                assert len(prob["attempts"]) == len(prob["tokens"])

        # Verify JSON-serializable
        json.dumps(output_data)
