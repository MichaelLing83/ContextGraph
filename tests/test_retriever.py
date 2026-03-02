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
        assert hasattr(result, "enriched_fragments")
        assert hasattr(result, "warnings")

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

    def test_extract_keywords(self):
        """Test keyword extraction skips stop words."""
        retriever = MemoryRetriever(store=None, embedder=None)

        kw = retriever._extract_keywords("Fix the import error in parser module")
        assert "fix" in kw
        assert "import" in kw
        assert "parser" in kw
        assert "module" in kw
        assert "the" not in kw
        assert "in" not in kw

    def test_extract_keywords_empty(self):
        retriever = MemoryRetriever(store=None, embedder=None)
        assert retriever._extract_keywords("") == set()
        assert retriever._extract_keywords(None) == set()

    def test_summarize_actions_edits(self):
        """Test action summarization extracts edits."""
        retriever = MemoryRetriever(store=None, embedder=None)

        actions = ["bash", "edit src/foo.py:10:20", "bash", "edit src/bar.py:5:5"]
        summary = retriever._summarize_actions(actions)
        assert "foo.py" in summary
        assert "bar.py" in summary
        assert "Edited" in summary

    def test_summarize_actions_empty(self):
        retriever = MemoryRetriever(store=None, embedder=None)
        assert retriever._summarize_actions(None) == ""
        assert retriever._summarize_actions([]) == ""

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

    def test_by_error_without_store(self):
        """Test by_error returns empty list without store."""
        retriever = MemoryRetriever(store=None, embedder=None)
        assert retriever.by_error("ImportError: test") == []

    def test_by_task_without_store(self):
        """Test by_task returns empty list without store."""
        retriever = MemoryRetriever(store=None, embedder=None)
        assert retriever.by_task("Fix bug", "some repo") == []

    def test_by_task_keyword_query(self):
        """Test by_task builds keyword-based query."""
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

        # Should have keyword parameters
        params = captured["parameters"]
        # Check that keyword params exist (kw0, kw1, etc.)
        kw_params = {k: v for k, v in params.items() if k.startswith("kw")}
        assert len(kw_params) > 0
        # All values should be lowercase
        for v in kw_params.values():
            assert v == v.lower()

    def test_by_error_keyword_ranking(self):
        """Test by_error ranks results by keyword overlap."""
        from agent_memory.models import Fragment

        class DummyStore:
            def execute_query(self, query, parameters=None):
                return [
                    {
                        "f": {
                            "id": "f1",
                            "step_range": [1, 5],
                            "fragment_type": "error_recovery",
                            "description": "recovery",
                            "action_sequence": ["bash", "edit foo.py:1:2"],
                            "outcome": "success",
                        },
                        "instance_id": "inst1",
                        "repo": "django/django",
                        "summary": "Fixed import issue",
                        "keywords": ["django", "import", "settings"],
                        "etype": "ImportError",
                        "actions": ["bash", "edit foo.py:1:2"],
                    },
                    {
                        "f": {
                            "id": "f2",
                            "step_range": [1, 3],
                            "fragment_type": "error_recovery",
                            "description": "recovery",
                            "action_sequence": ["bash"],
                            "outcome": "success",
                        },
                        "instance_id": "inst2",
                        "repo": "flask/flask",
                        "summary": "Fixed random thing",
                        "keywords": ["flask", "route"],
                        "etype": "ImportError",
                        "actions": ["bash"],
                    },
                ]

        retriever = MemoryRetriever(store=DummyStore(), embedder=None)
        results = retriever.by_error("ImportError: cannot import 'settings' from django.conf")

        assert len(results) == 2
        # f1 should rank higher (keywords overlap: django, import, settings)
        assert results[0].fragment.id == "f1"
        assert results[0].relevance_score > results[1].relevance_score
