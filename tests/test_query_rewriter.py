"""Tests for QueryRewriter."""

import pytest
from unittest.mock import MagicMock

from agent_memory.query_rewriter import QueryRewriter


@pytest.fixture
def rewriter():
    """A rewriter with a mocked OpenAI client."""
    rw = QueryRewriter(
        api_base="http://fake",
        api_key="fake-key",
        model="test-model",
        enabled=True,
        cache_size=4,
    )
    return rw


def _mock_response(content: str):
    """Build a mock ChatCompletion response."""
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestDisabledAndEmpty:
    def test_disabled_returns_original(self):
        rw = QueryRewriter(
            api_base="http://fake", api_key="k", enabled=False,
        )
        assert rw.rewrite("some query") == "some query"

    def test_empty_query_returns_original(self, rewriter):
        assert rewriter.rewrite("") == ""
        assert rewriter.rewrite("   ") == "   "


class TestCacheBehavior:
    def test_cache_hit(self, rewriter):
        """Same query should not trigger a second LLM call."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_response(
            "field validation\ntype coercion"
        )
        rewriter._client = mock_client

        result1 = rewriter.rewrite("Fix SplitArrayField")
        result2 = rewriter.rewrite("Fix SplitArrayField")

        assert result1 == result2
        assert mock_client.chat.completions.create.call_count == 1

    def test_cache_eviction(self, rewriter):
        """LRU eviction when cache is full (cache_size=4)."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_response("kw")
        rewriter._client = mock_client

        # Fill cache with 4 entries
        for i in range(4):
            rewriter.rewrite(f"query {i}")
        assert len(rewriter._cache) == 4

        # 5th entry evicts the oldest
        rewriter.rewrite("query 4")
        assert len(rewriter._cache) == 4

        # query 0 was evicted, calling it again triggers LLM
        call_count_before = mock_client.chat.completions.create.call_count
        rewriter.rewrite("query 0")
        assert mock_client.chat.completions.create.call_count == call_count_before + 1


class TestLLMInteraction:
    def test_successful_rewrite_prepends_keywords(self, rewriter):
        mock_client = MagicMock()
        keywords = "field validation\nshared reference mutation\ntype coercion"
        mock_client.chat.completions.create.return_value = _mock_response(keywords)
        rewriter._client = mock_client

        original = "Fix SplitArrayField with BooleanField"
        result = rewriter.rewrite(original)

        # Keywords prepended, original preserved
        assert result.startswith(keywords)
        assert result.endswith(original)
        assert keywords + "\n\n" + original == result

    def test_llm_failure_returns_original(self, rewriter):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")
        rewriter._client = mock_client

        result = rewriter.rewrite("some query")
        assert result == "some query"

    def test_empty_llm_response_returns_original(self, rewriter):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_response("")
        rewriter._client = mock_client

        result = rewriter.rewrite("some query")
        assert result == "some query"

    def test_none_llm_content_returns_original(self, rewriter):
        choice = MagicMock()
        choice.message.content = None
        resp = MagicMock()
        resp.choices = [choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        rewriter._client = mock_client

        result = rewriter.rewrite("some query")
        assert result == "some query"

    def test_input_truncated_to_1000_chars(self, rewriter):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_response("kw")
        rewriter._client = mock_client

        long_query = "x" * 2000
        rewriter.rewrite(long_query)

        call_args = mock_client.chat.completions.create.call_args
        user_msg = call_args.kwargs["messages"][1]["content"]
        assert len(user_msg) == 1000
        assert user_msg == long_query[:1000]


class TestRetrieverIntegration:
    def test_retriever_accepts_rewriter(self):
        from agent_memory.retriever import MemoryRetriever
        rw = QueryRewriter(
            api_base="http://fake", api_key="k", enabled=False,
        )
        retriever = MemoryRetriever(store=None, embedder=None, query_rewriter=rw)
        assert retriever.query_rewriter is rw

    def test_playbook_accepts_rewriter(self):
        from agent_memory.playbook import PlaybookRetriever
        rw = QueryRewriter(
            api_base="http://fake", api_key="k", enabled=False,
        )
        pr = PlaybookRetriever(store=None, embedder=None, query_rewriter=rw)
        assert pr.query_rewriter is rw
