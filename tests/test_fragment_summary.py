"""Tests for fragment LLM summary cache and build integration."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_memory.vault.fragment_summary import (
    FragmentSummarizer,
    FragmentSummaryCache,
    fragment_body_hash,
)
from agent_memory.vault.obsidian_graph import ObsidianGraphBuilder
from agent_memory.vault.obsidian_index import ObsidianVaultIndex
from agent_memory.vault.parser import parse_vault_note


def test_fragment_summary_cache_hit_on_unchanged_body(tmp_path: Path):
    cache_path = tmp_path / ".llm_summary_cache.json"
    cache = FragmentSummaryCache(cache_path, autosave=False)
    body = "Invalidate cache entries on write.\n"
    cache.put(body, "Cached summary.", model="test-model")
    cache.save()

    cache2 = FragmentSummaryCache(cache_path)
    cache2.load()
    assert cache2.get(body, model="test-model") == "Cached summary."


def test_cache_autosaves_on_put(tmp_path: Path):
    cache_path = tmp_path / ".llm_summary_cache" / "test-model.json"
    cache = FragmentSummaryCache(cache_path, autosave=True)
    cache.put("body text", "Fresh summary.", model="test-model")
    assert cache_path.is_file()
    cache2 = FragmentSummaryCache(cache_path)
    cache2.load()
    assert cache2.get("body text", model="test-model") == "Fresh summary."


def test_fragment_summary_cache_miss_when_body_changes(tmp_path: Path):
    cache = FragmentSummaryCache(tmp_path / ".llm_summary_cache.json")
    cache.put("old body", "Old summary.", model="test-model")
    assert cache.get("new body", model="test-model") is None


def test_fragment_summary_cache_miss_when_model_changes(tmp_path: Path):
    cache = FragmentSummaryCache(tmp_path / ".llm_summary_cache.json")
    body = "Same text."
    cache.put(body, "Summary.", model="model-a")
    assert cache.get(body, model="model-b") is None


def test_summarize_invokes_progress_callback(tmp_path: Path):
    cache = FragmentSummaryCache(tmp_path / ".llm_summary_cache.json")
    cache.put("Same text.", "Cached summary.", model="test-model")
    progress_calls: list[int] = []

    summarizer = FragmentSummarizer(
        api_base="http://localhost:4000/v1",
        api_key="test-key",
        model="test-model",
        cache=cache,
        on_progress=lambda stats: progress_calls.append(stats.cache_hits),
    )
    summarizer.summarize("Heading", "Same text.")
    summarizer.summarize("Empty", "   ")

    assert len(progress_calls) == 2


def test_fragment_body_hash_stable():
    body = "Same content"
    assert fragment_body_hash(body) == fragment_body_hash(body)
    assert fragment_body_hash(body) != fragment_body_hash(body + " ")


@patch("agent_memory.vault.fragment_summary.FragmentSummarizer._get_client")
def test_summarize_disables_reasoning_by_default(mock_get_client, tmp_path: Path):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Short summary."))]
    )

    cache = FragmentSummaryCache(tmp_path / ".llm_summary_cache.json")
    summarizer = FragmentSummarizer(
        api_base="http://localhost:4000/v1",
        api_key="test-key",
        model="deepseek-r1:1.5b",
        cache=cache,
    )
    assert summarizer.disable_reasoning is True
    summarizer.summarize("Heading", "Body text.")
    kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"] == {"reasoning_effort": "none"}


def test_build_writes_cg_llm_summary_from_cache(tmp_path: Path):
    source = tmp_path / "SourceVault"
    graph = tmp_path / "GraphVault"
    source.mkdir()
    graph.mkdir()
    note = source / "Doc.md"
    note.write_text(
        "# Doc\n\n## Alpha\n\nFirst paragraph about caching.\n",
        encoding="utf-8",
    )

    cache = FragmentSummaryCache(graph / ".llm_summary_cache.json")
    cache.put(
        "First paragraph about caching.",
        "This section explains caching behavior.",
        model="test-model",
    )

    summarizer = FragmentSummarizer(
        api_base="http://localhost:4000/v1",
        api_key="test-key",
        model="test-model",
        cache=cache,
    )

    raw = parse_vault_note(note, source)
    builder = ObsidianGraphBuilder(
        graph, source_vault=source, summarizer=summarizer, chunk_mode="heading"
    )
    builder.ingest_note(raw)

    frag = graph / "Fragments" / "Doc--Alpha.md"
    text = frag.read_text(encoding="utf-8")
    assert 'cg_llm_summary: "This section explains caching behavior."' in text
    assert "# Alpha\n\nFirst paragraph about caching." in text
    assert summarizer.stats.cache_hits == 1
    assert summarizer.stats.llm_calls == 0


@patch("agent_memory.vault.fragment_summary.FragmentSummarizer._get_client")
def test_build_calls_llm_once_then_reuses_cache(mock_get_client, tmp_path: Path):
    source = tmp_path / "SourceVault"
    graph = tmp_path / "GraphVault"
    source.mkdir()
    graph.mkdir()

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Fresh LLM summary."))]
    )

    note = source / "Doc.md"
    note.write_text(
        "## One\n\nShared body text.\n\n## Two\n\nShared body text.\n",
        encoding="utf-8",
    )

    cache = FragmentSummaryCache(graph / ".llm_summary_cache.json")
    summarizer = FragmentSummarizer(
        api_base="http://localhost:4000/v1",
        api_key="test-key",
        model="test-model",
        cache=cache,
    )

    raw = parse_vault_note(note, source)
    builder = ObsidianGraphBuilder(
        graph, source_vault=source, summarizer=summarizer, chunk_mode="heading"
    )
    builder.ingest_note(raw)

    assert summarizer.stats.llm_calls == 1
    assert summarizer.stats.cache_hits == 1
    assert mock_client.chat.completions.create.call_count == 1


def test_index_includes_cg_llm_summary_in_tokens(tmp_path: Path):
    (tmp_path / "frag.md").write_text(
        """---
cg_type: fragment
cg_llm_summary: "embedding keywords about dependency injection"
tags:
  - cg/fragment
---

# Title

Original body about something else.
""",
        encoding="utf-8",
    )
    index = ObsidianVaultIndex(tmp_path)
    index.build()
    hits = index.search("dependency injection", tags=["cg/fragment"], limit=5)
    assert hits
    assert hits[0].rel_path == "frag.md"
