"""Tests for MemoryRetriever."""

from agent_memory.retriever import MemoryRetriever, RetrievalResult
from agent_memory.models import State, Methodology


class TestMemoryRetriever:
    def test_retrieval_result_structure(self):
        """Test RetrievalResult has correct structure."""
        result = RetrievalResult(
            methodologies=[],
            similar_fragments=[],
            error_solutions=[],
            warnings=[],
        )
        assert hasattr(result, "methodologies")
        assert hasattr(result, "similar_fragments")
        assert hasattr(result, "warnings")

    def test_by_error_query_generation(self):
        """Test error-based query generation."""
        retriever = MemoryRetriever(store=None, embedder=None)

        query, params = retriever._build_error_query("ImportError")

        assert "error_type" in query
        assert "ErrorPattern" in query or "Methodology" in query
        assert params.get("error_type") == "ImportError"

    def test_retrieval_result_is_empty(self):
        """Test RetrievalResult.is_empty method."""
        empty_result = RetrievalResult()
        assert empty_result.is_empty()

        non_empty = RetrievalResult(warnings=["some warning"])
        assert non_empty.is_empty()  # Warnings don't count

        non_empty2 = RetrievalResult(methodologies=[
            Methodology(id="m1", situation="test", strategy="test", confidence=0.8)
        ])
        assert not non_empty2.is_empty()

    def test_extract_error_type(self):
        """Test error type extraction."""
        retriever = MemoryRetriever(store=None, embedder=None)

        assert retriever._extract_error_type("ImportError: cannot import X") == "ImportError"
        assert retriever._extract_error_type("TypeError: expected int") == "TypeError"
        assert retriever._extract_error_type("No error here") is None

    def test_retrieve_without_store(self):
        """Test retrieve returns empty result without store."""
        retriever = MemoryRetriever(store=None, embedder=None)
        state = State(
            tools=["bash"],
            repo_summary="Test repo",
            task_description="Fix bug",
            current_error="ImportError: test",
            phase="fixing",
        )

        result = retriever.retrieve(state)
        assert result.is_empty()

    def test_by_task_uses_full_query_inputs(self):
        """Test by_task passes normalized full text to query parameters."""
        captured = {}

        class DummyStore:
            def execute_query(self, query, parameters=None):
                captured["query"] = query
                captured["parameters"] = parameters or {}
                return []

        retriever = MemoryRetriever(store=DummyStore(), embedder=None)
        retriever.by_task(
            task_description="Fix import error in parser module",
            repo_summary="A python parser repository",
        )

        params = captured["parameters"]
        assert params["task_description"] == "fix import error in parser module"
        assert params["repo_summary"] == "a python parser repository"
