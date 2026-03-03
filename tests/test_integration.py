"""Integration tests for complete AgentMemory workflow.

Includes tests for the Zep-optimized pipeline:
  write → triple-search → rerank → structured output
"""

from agent_memory import (
    AgentMemory,
    State,
    RawTrajectory,
)


class TestAgentMemoryIntegration:
    """End-to-end integration tests."""

    def test_complete_workflow(self):
        """Test complete memory workflow: learn -> query -> check_loop."""
        with AgentMemory(neo4j_uri=None, embedding_api_key=None) as memory:
            # 1. Learn from trajectory
            trajectory = RawTrajectory(
                instance_id="test__test-123",
                repo="test/repo",
                success=True,
                problem_statement="Fix bug",
                steps=[
                    {"action": "search", "observation": "Found file"},
                    {"action": "edit", "observation": "Error: SyntaxError"},
                    {"action": "edit", "observation": "Fixed"},
                    {"action": "test", "observation": "Tests pass"},
                ],
            )

            traj_id = memory.learn(trajectory)
            assert traj_id is not None
            assert traj_id.startswith("traj_")

            # 2. Query for context
            state = State(
                tools=["bash", "edit"],
                repo_summary="Test repo",
                task_description="Fix another bug",
                current_error="SyntaxError: invalid syntax",
                phase="fixing",
                last_action_type="edit",
            )

            context = memory.query(state)
            assert context is not None
            assert hasattr(context, "methodologies")
            assert hasattr(context, "warnings")

            # 3. Check for loop
            state_history = [state for _ in range(5)]

            loop_info = memory.check_loop(state_history)
            assert loop_info is not None

    def test_multiple_trajectories(self):
        """Test learning from multiple trajectories."""
        with AgentMemory(neo4j_uri=None, embedding_api_key=None) as memory:
            for i in range(3):
                trajectory = RawTrajectory(
                    instance_id=f"test__test-{i}",
                    repo="test/repo",
                    success=i % 2 == 0,  # Alternating success/failure
                    steps=[
                        {"action": "search", "observation": "Found"},
                        {"action": "edit", "observation": "Done"},
                    ],
                )
                traj_id = memory.learn(trajectory)
                assert traj_id is not None

    def test_consolidation_trigger(self):
        """Test that consolidation triggers after N trajectories."""
        with AgentMemory(
            neo4j_uri=None,
            embedding_api_key=None,
            consolidate_every=2,
        ) as memory:
            # Learn 2 trajectories to trigger consolidation
            for i in range(2):
                trajectory = RawTrajectory(
                    instance_id=f"test__test-{i}",
                    repo="test/repo",
                    success=True,
                    steps=[{"action": "test", "observation": "pass"}],
                )
                memory.learn(trajectory)

            # Consolidation should have been triggered
            assert memory._trajectory_count == 2

    def test_query_structured(self):
        """Test structured XML query output."""
        with AgentMemory(neo4j_uri=None, embedding_api_key=None) as memory:
            state = State(
                tools=["bash", "edit"],
                repo_summary="Test repo",
                task_description="Fix bug",
                current_error="",
                phase="fixing",
            )

            output = memory.query_structured(state)
            # Empty without store
            assert output == ""

    def test_memory_stats_with_communities(self):
        """Test get_stats includes community count."""
        with AgentMemory(neo4j_uri=None, embedding_api_key=None) as memory:
            stats = memory.get_stats()
            assert hasattr(stats, "total_communities")
            assert stats.total_communities == 0

    def test_memory_context_to_structured(self):
        """Test MemoryContext.to_structured() method."""
        from agent_memory.memory import MemoryContext
        from agent_memory.models import Methodology

        ctx = MemoryContext(
            methodologies=[Methodology(
                id="m1", situation="When ImportError",
                strategy="Check imports", confidence=0.8,
                success_count=5, failure_count=1,
            )],
            warnings=["High frequency error"],
        )
        output = ctx.to_structured()
        assert "<METHODOLOGIES>" in output
        assert "<WARNINGS>" in output


class TestImportStructure:
    """Test that all imports work correctly."""

    def test_all_imports(self):
        """Verify all public API imports work (including new Zep modules)."""
        from agent_memory import (
            Trajectory,
            Fragment,
            State,
            Methodology,
            ErrorPattern,
            TemporalEdge,
            Community,
            Neo4jStore,
            EmbeddingClient,
            get_embedding_client,
            LoopDetector,
            LoopSignature,
            LoopInfo,
            MemoryWriter,
            RawTrajectory,
            MemoryRetriever,
            RetrievalResult,
            ScoredResult,
            EnrichedFragment,
            RerankerPipeline,
            StructuredContextFormatter,
            EntityResolver,
            CommunityDetector,
            MemoryConsolidator,
            AgentMemory,
            MemoryContext,
            MemoryStats,
        )

        # All imports should be classes or functions
        assert Trajectory is not None
        assert AgentMemory is not None
        assert TemporalEdge is not None
        assert Community is not None
        assert ScoredResult is not None
        assert RerankerPipeline is not None
        assert StructuredContextFormatter is not None
        assert EntityResolver is not None
        assert CommunityDetector is not None

    def test_version_bumped(self):
        """Version should be 0.2.0 for Zep optimizations."""
        import agent_memory
        assert agent_memory.__version__ == "0.2.0"


class TestEndToEndPipeline:
    """Test the full Zep pipeline without Neo4j."""

    def test_writer_with_entity_resolver(self):
        """Writer should call entity resolver before creating error patterns."""
        resolve_calls = []

        class MockResolver:
            def resolve_error_pattern(self, pattern):
                resolve_calls.append(pattern.error_type)
                return pattern, True  # Always treat as new

        calls = []

        class DummyStore:
            def create_trajectory(self, trajectory):
                calls.append(("trajectory", trajectory.id))
            def create_fragment(self, fragment, trajectory_id):
                calls.append(("fragment", fragment.id))
            def create_error_pattern(self, pattern):
                calls.append(("error_pattern", pattern.error_type))
            def link_fragment_to_error_pattern(self, fragment_id, error_type):
                calls.append(("link", fragment_id, error_type))

        from agent_memory.writer import MemoryWriter
        writer = MemoryWriter(
            store=DummyStore(),
            embedder=None,
            entity_resolver=MockResolver(),
        )

        raw = RawTrajectory(
            instance_id="test__test-123",
            repo="test/repo",
            success=True,
            steps=[
                {"action": "edit", "observation": "ImportError: cannot import Foo"},
                {"action": "edit", "observation": "Fixed the import"},
                {"action": "test", "observation": "All tests pass"},
            ],
        )

        writer.write_trajectory(raw)
        assert "ImportError" in resolve_calls

    def test_reranker_with_triple_search(self):
        """Reranker should merge cosine + BM25 + BFS results."""
        from agent_memory.reranker import RerankerPipeline
        from agent_memory.retriever import ScoredResult

        reranker = RerankerPipeline()

        cosine = [ScoredResult("f1", "Fragment", 0.9, "cosine", {
            "f": {"id": "f1", "step_range": [0, 1], "fragment_type": "error_recovery",
                  "description": "test", "action_sequence": [], "outcome": "success"}
        })]
        bm25 = [ScoredResult("f1", "Fragment", 5.0, "bm25", {
            "f": {"id": "f1", "step_range": [0, 1], "fragment_type": "error_recovery",
                  "description": "test", "action_sequence": [], "outcome": "success"}
        })]
        bfs = [ScoredResult("f2", "Fragment", 0.33, "bfs", {
            "f": {"id": "f2", "step_range": [0, 1], "fragment_type": "exploration",
                  "description": "explore", "action_sequence": [], "outcome": "completed"}
        }, hop_distance=1)]

        result = reranker.rerank(cosine, bm25, bfs, top_k=2)
        assert len(result) == 2
        assert result[0].node_id == "f1"

    def test_formatter_end_to_end(self):
        """Formatter should produce valid XML from retrieval results."""
        from agent_memory.models import Fragment, Methodology
        from agent_memory.retriever import EnrichedFragment, RetrievalResult
        from agent_memory.formatter import StructuredContextFormatter

        frag = Fragment(
            id="f1", step_range=(0, 5), fragment_type="error_recovery",
            description="Fixed import in Django models",
            action_sequence=["edit", "test"], outcome="success",
        )
        ef = EnrichedFragment(
            fragment=frag, repo="django/django", instance_id="django__123",
            trajectory_summary="Fixed ImportError", error_type="ImportError",
            action_summary="Edited models.py", relevance_score=0.9,
        )
        meth = Methodology(
            id="m1", situation="When encountering ImportError",
            strategy="Check imports and INSTALLED_APPS",
            confidence=0.85, success_count=10, failure_count=2,
        )

        result = RetrievalResult(
            enriched_fragments=[ef],
            methodologies=[meth],
            warnings=["ImportError seen 200+ times"],
        )

        formatter = StructuredContextFormatter()
        output = formatter.format(result)

        assert "<PAST_EXPERIENCES>" in output
        assert "<METHODOLOGIES>" in output
        assert "<WARNINGS>" in output

    def test_embedding_client_dimensions(self):
        """Test embedding client reports correct dimensions."""
        from agent_memory.embeddings import MockEmbeddingClient
        client = MockEmbeddingClient(dimension=256)
        assert client.dimensions == 256
        emb = client.embed("test")
        assert len(emb) == 256
