"""Tests for MemoryRetriever."""

from agent_memory.retriever import MemoryRetriever, RetrievalResult, ScoredResult
from agent_memory.models import State, Methodology, Strategy


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


class TestTripleSearch:
    """Tests for the triple-search channels."""

    def test_check_vector_index_false_without_store(self):
        """Vector index check returns False without store."""
        retriever = MemoryRetriever(store=None, embedder=None)
        assert retriever._check_vector_index() is False

    def test_check_vector_index_caches_result(self):
        """Vector index check should cache the result."""
        call_count = 0

        class DummyStore:
            def execute_query(self, query, parameters=None):
                nonlocal call_count
                call_count += 1
                return []  # No indexes

        retriever = MemoryRetriever(store=DummyStore(), embedder=None)
        assert retriever._check_vector_index() is False
        assert retriever._check_vector_index() is False
        assert call_count == 1

    def test_build_query_text(self):
        """Test query text construction from state."""
        retriever = MemoryRetriever(store=None, embedder=None)
        state = State(
            tools=["bash"],
            repo_summary="Django web framework",
            task_description="Fix import error",
            current_error="ImportError: no module named foo",
            phase="fixing",
        )

        text = retriever._build_query_text(state)
        assert "ImportError" in text
        assert "Fix import error" in text
        assert "Django" in text

    def test_escape_lucene(self):
        """Test Lucene special character escaping."""
        retriever = MemoryRetriever(store=None, embedder=None)

        assert retriever._escape_lucene("hello world") == "hello world"
        assert "\\" in retriever._escape_lucene("hello+world")
        assert "\\" in retriever._escape_lucene('test "quoted"')

    def test_simple_merge_deduplicates(self):
        """Simple merge should keep highest-scored entry per node_id."""
        retriever = MemoryRetriever(store=None, embedder=None)

        results = [
            ScoredResult("f1", "Fragment", 0.9, "cosine"),
            ScoredResult("f1", "Fragment", 0.7, "bm25"),
            ScoredResult("f2", "Fragment", 0.8, "cosine"),
        ]

        merged = retriever._simple_merge(results, top_k=2)
        assert len(merged) == 2
        assert merged[0].node_id == "f1"
        assert merged[0].score == 0.9

    def test_search_cosine_without_store(self):
        """Cosine search returns empty without store."""
        retriever = MemoryRetriever(store=None, embedder=None)
        assert retriever._search_cosine([1.0, 0.0]) == []

    def test_search_cosine_without_embedding(self):
        """Cosine search returns empty without embedding."""
        retriever = MemoryRetriever(store=None, embedder=None)
        assert retriever._search_cosine(None) == []

    def test_search_bm25_without_store(self):
        """BM25 search returns empty without store."""
        retriever = MemoryRetriever(store=None, embedder=None)
        assert retriever._search_bm25("test query") == []

    def test_search_bfs_without_store(self):
        """BFS search returns empty without store."""
        retriever = MemoryRetriever(store=None, embedder=None)
        assert retriever._search_bfs(["f1", "f2"]) == []

    def test_search_community_without_store(self):
        """Community search returns empty without store."""
        retriever = MemoryRetriever(store=None, embedder=None)
        assert retriever._search_community([1.0, 0.0]) == []

    def test_fallback_to_legacy_without_vector_indexes(self):
        """Without vector indexes, should use legacy retrieval."""
        class DummyStore:
            def execute_query(self, query, parameters=None):
                if "SHOW INDEXES" in query:
                    return []  # No vector indexes
                return []

        retriever = MemoryRetriever(store=DummyStore(), embedder=None)
        state = State(
            tools=["bash"],
            repo_summary="Test repo",
            task_description="Fix bug",
            current_error="",
            phase="fixing",
        )

        result = retriever.retrieve(state)
        assert result.is_empty()

    def test_scored_to_enriched_filters_non_fragments(self):
        """Only Fragment-type ScoredResults should convert to EnrichedFragments."""
        retriever = MemoryRetriever(store=None, embedder=None)

        scored = [
            ScoredResult("f1", "Fragment", 0.9, "cosine", node_data={
                "f": {"id": "f1", "step_range": [0, 1], "fragment_type": "error_recovery",
                      "description": "test", "action_sequence": [], "outcome": "success"},
            }),
            ScoredResult("c1", "Community", 0.5, "community", node_data={}),
        ]

        enriched = retriever._scored_to_enriched(scored)
        assert len(enriched) == 1
        assert enriched[0].fragment.id == "f1"


class TestStrategySearch:
    """Tests for strategy search channel."""

    def test_retrieval_result_has_strategies(self):
        """RetrievalResult should have strategies field."""
        result = RetrievalResult()
        assert hasattr(result, "strategies")
        assert result.strategies == []

    def test_strategies_affect_is_empty(self):
        """Strategies should make is_empty return False."""
        empty = RetrievalResult()
        assert empty.is_empty()

        with_strategies = RetrievalResult(strategies=[
            Strategy(
                id="s1", rule_text="Test rule", category="debugging",
                source_trajectory_id="t1", source_repo="test/repo",
            )
        ])
        assert not with_strategies.is_empty()

    def test_search_strategies_without_store(self):
        """Strategy search returns empty without store."""
        retriever = MemoryRetriever(store=None, embedder=None)
        assert retriever._search_strategies(None, "") == []
        assert retriever._search_strategies([1.0, 0.0], "test") == []

    def test_search_strategies_bm25_channel(self):
        """Strategy BM25 search should return Strategy objects."""
        class DummyStore:
            def execute_query(self, query, parameters=None):
                if "fulltext" in query and "strategy_text" in query:
                    return [
                        {
                            "id": "strat_001",
                            "rule_text": "Always check import paths when ImportError occurs",
                            "category": "error_handling",
                            "source_trajectory_id": "t1",
                            "source_repo": "django/django",
                            "confidence": 0.8,
                            "score": 2.5,
                        },
                    ]
                if "SHOW INDEXES" in query:
                    return []  # No vector index
                return []

        retriever = MemoryRetriever(store=DummyStore(), embedder=None)
        strategies = retriever._search_strategies(None, "ImportError check")
        assert len(strategies) == 1
        assert strategies[0].id == "strat_001"
        assert strategies[0].category == "error_handling"
        assert strategies[0].source_repo == "django/django"

    def test_check_strategy_index_without_store(self):
        """Strategy index check returns False without store."""
        retriever = MemoryRetriever(store=None, embedder=None)
        assert retriever._check_strategy_index() is False

    def test_strategy_model_from_dict(self):
        """Test Strategy.from_dict round-trip."""
        strat = Strategy(
            id="s1", rule_text="Test rule", category="debugging",
            source_trajectory_id="t1", source_repo="test/repo",
            confidence=0.9,
        )
        d = strat.to_dict()
        restored = Strategy.from_dict(d)
        assert restored.id == "s1"
        assert restored.rule_text == "Test rule"
        assert restored.confidence == 0.9
