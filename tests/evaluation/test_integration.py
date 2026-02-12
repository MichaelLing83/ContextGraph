"""Integration tests for evaluation module."""

from agent_memory import AgentMemory
from agent_memory.evaluation import (
    random_split,
    build_graph,
    calculate_metrics,
    compare_results,
    ProblemResult,
    QueryMemoryTool,
    QueryMemoryInput,
)


class TestEvaluationIntegration:
    def test_full_pipeline(self, tmp_path):
        """Test the complete evaluation pipeline."""
        # 1. Create sample trajectory files
        for i in range(4):
            success = i % 2 == 0
            traj_file = tmp_path / f"test__test-{i}.traj"
            traj_file.write_text(f'''{{
                "environment": "swe_main",
                "trajectory": [
                    {{"thought": "Exploring", "action": "ls", "observation": "file.py"}},
                    {{"thought": "Editing", "action": "edit file.py", "observation": "Done"}}
                ],
                "info": {{"exit_status": "{'submitted' if success else 'failed'}"}}
            }}''')

        # 2. Split data
        files = list(tmp_path.glob("*.traj"))
        split = random_split(files, ratio=0.5, seed=42)

        assert len(split.train) == 2
        assert len(split.test) == 2

        # 3. Build graph from training data
        memory = AgentMemory(neo4j_uri=None, embedding_api_key=None)
        build_result = build_graph(split.train, memory)

        assert build_result.trajectories_loaded == 2
        assert build_result.errors == []

        # 4. Use query_memory tool
        tool = QueryMemoryTool(memory)
        output = tool.invoke(QueryMemoryInput(
            current_error="ImportError",
            task_description="Fix import",
            phase="fixing",
        ))

        assert output is not None

        # 5. Calculate metrics (simulated results)
        control_results = [
            ProblemResult("p1", [False, True], [100, 100]),
            ProblemResult("p2", [False, False, False], [100, 100, 100]),
        ]
        treatment_results = [
            ProblemResult("p1", [True], [80]),
            ProblemResult("p2", [False, True], [100, 90]),
        ]

        control = calculate_metrics(control_results)
        treatment = calculate_metrics(treatment_results)

        # 6. Compare results
        report = compare_results(control, treatment)

        assert report.pass_at_1_improvement > 0
        summary = report.to_summary()
        assert "pass@1" in summary.lower()

        memory.close()
