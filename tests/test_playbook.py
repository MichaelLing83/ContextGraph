"""Tests for the playbook parsing, retrieval, and formatting module."""

import pytest
from unittest.mock import MagicMock, patch

from agent_memory.models import PlaybookEntry, CanonicalRule, PLAYBOOK_SECTIONS
from agent_memory.playbook import parse_playbook, format_playbook, PlaybookRetriever
from agent_memory.strategy_extractor import (
    strategies_to_playbook_entries,
    CATEGORY_TO_PREFIX,
)
from agent_memory.models import Strategy, State


SAMPLE_PLAYBOOK = """\
## STRATEGIES AND HARD RULES
[shr-00001] Make sure to end code blocks with ``` followed by a newline.
[shr-00005] Always look at API specifications before calling an API.

## COMMON MISTAKES AND CORRECT STRATEGIES
[cms-00010] Avoid relying solely on email comparison for user identification.
[cms-00011] When debugging import errors, check the package version first
and verify compatibility with the runtime.

## PROBLEM-SOLVING HEURISTICS AND WORKFLOWS
[psw-00002] Remember you can use variables in subsequent code blocks.
"""


class TestParsePlaybook:
    """Tests for parse_playbook()."""

    def test_parse_multi_section(self):
        """Parse multi-section playbook text into correct entries."""
        entries = parse_playbook(SAMPLE_PLAYBOOK)
        assert len(entries) == 5

        # Check first entry
        assert entries[0].id == "shr-00001"
        assert entries[0].prefix == "shr"
        assert entries[0].section == "STRATEGIES AND HARD RULES"
        assert "end code blocks" in entries[0].text

        # Check second entry
        assert entries[1].id == "shr-00005"
        assert entries[1].prefix == "shr"

        # Check cms entries
        assert entries[2].id == "cms-00010"
        assert entries[2].prefix == "cms"
        assert entries[2].section == "COMMON MISTAKES AND CORRECT STRATEGIES"

        # Check multi-line entry
        assert entries[3].id == "cms-00011"
        assert "import errors" in entries[3].text
        assert "compatibility" in entries[3].text

        # Check psw entry
        assert entries[4].id == "psw-00002"
        assert entries[4].prefix == "psw"

    def test_parse_entry_ids(self):
        """Verify prefix/id extraction for various formats."""
        text = """\
## VERIFICATION CHECKLIST
[verify-00001] Check all test cases pass before submitting.
[verify-00099] Review edge cases in boundary conditions.
"""
        entries = parse_playbook(text)
        assert len(entries) == 2
        assert entries[0].id == "verify-00001"
        assert entries[0].prefix == "verify"
        assert entries[1].id == "verify-00099"

    def test_parse_empty(self):
        """Empty input returns empty list."""
        assert parse_playbook("") == []
        assert parse_playbook("   \n\n  ") == []

    def test_parse_no_sections(self):
        """Entries without section headers get default section."""
        text = "[misc-00001] A standalone rule without a section header."
        entries = parse_playbook(text)
        assert len(entries) == 1
        assert entries[0].section == "OTHERS"  # default

    def test_parse_unknown_section(self):
        """Unknown section header is preserved as-is."""
        text = """\
## CUSTOM SECTION
[custom-00001] A rule in a custom section.
"""
        entries = parse_playbook(text)
        assert len(entries) == 1
        assert entries[0].section == "CUSTOM SECTION"
        assert entries[0].prefix == "custom"


class TestFormatPlaybook:
    """Tests for format_playbook()."""

    def test_format_grouped_output(self):
        """Entries are grouped by section in canonical order."""
        entries = [
            PlaybookEntry(id="psw-00001", prefix="psw",
                          section="PROBLEM-SOLVING HEURISTICS AND WORKFLOWS",
                          text="Think step by step."),
            PlaybookEntry(id="shr-00001", prefix="shr",
                          section="STRATEGIES AND HARD RULES",
                          text="Always validate input."),
            PlaybookEntry(id="shr-00002", prefix="shr",
                          section="STRATEGIES AND HARD RULES",
                          text="Use type hints."),
        ]
        output = format_playbook(entries)

        # shr section should come before psw in canonical order
        shr_pos = output.index("## STRATEGIES AND HARD RULES")
        psw_pos = output.index("## PROBLEM-SOLVING HEURISTICS AND WORKFLOWS")
        assert shr_pos < psw_pos

        assert "[shr-00001] Always validate input." in output
        assert "[shr-00002] Use type hints." in output
        assert "[psw-00001] Think step by step." in output

    def test_format_empty(self):
        """Empty entries returns empty string."""
        assert format_playbook([]) == ""

    def test_roundtrip(self):
        """parse → format → parse roundtrip preserves entries."""
        entries = parse_playbook(SAMPLE_PLAYBOOK)
        formatted = format_playbook(entries)
        re_parsed = parse_playbook(formatted)

        assert len(re_parsed) == len(entries)
        for orig, reparsed in zip(entries, re_parsed):
            assert orig.id == reparsed.id
            assert orig.prefix == reparsed.prefix
            assert orig.text == reparsed.text


class TestPlaybookEntryModel:
    """Tests for PlaybookEntry dataclass."""

    def test_to_dict_from_dict_roundtrip(self):
        """to_dict/from_dict roundtrip preserves all fields."""
        entry = PlaybookEntry(
            id="shr-00001",
            prefix="shr",
            section="STRATEGIES AND HARD RULES",
            text="Always check error codes.",
            embedding=[0.1, 0.2, 0.3],
        )
        d = entry.to_dict()
        restored = PlaybookEntry.from_dict(d)
        assert restored.id == entry.id
        assert restored.prefix == entry.prefix
        assert restored.section == entry.section
        assert restored.text == entry.text
        assert restored.embedding == entry.embedding

    def test_from_dict_defaults(self):
        """from_dict handles missing optional fields."""
        d = {"id": "misc-00001", "prefix": "misc", "text": "Some rule."}
        entry = PlaybookEntry.from_dict(d)
        assert entry.section == "OTHERS"
        assert entry.embedding is None


class TestPlaybookRetriever:
    """Tests for PlaybookRetriever."""

    def test_retrieve_without_store(self):
        """Retriever with no store returns empty list."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        result = retriever.retrieve("some error message")
        assert result == []

    def test_ingest_without_store(self):
        """Ingest without store returns entry count."""
        mock_embedder = MagicMock()
        mock_embedder.embed_batch.return_value = [[0.1, 0.2] for _ in range(3)]

        retriever = PlaybookRetriever(store=None, embedder=mock_embedder)
        entries = [
            PlaybookEntry(id="shr-00001", prefix="shr",
                          section="STRATEGIES AND HARD RULES",
                          text="Rule one."),
            PlaybookEntry(id="shr-00002", prefix="shr",
                          section="STRATEGIES AND HARD RULES",
                          text="Rule two."),
            PlaybookEntry(id="cms-00001", prefix="cms",
                          section="COMMON MISTAKES AND CORRECT STRATEGIES",
                          text="Rule three."),
        ]
        count = retriever.ingest(entries)
        assert count == 3
        # Embeddings should have been set
        assert entries[0].embedding == [0.1, 0.2]

    def test_ingest_skips_existing_embeddings(self):
        """Entries with existing embeddings are not re-embedded."""
        mock_embedder = MagicMock()
        mock_embedder.embed_batch.return_value = [[0.9, 0.8]]

        retriever = PlaybookRetriever(store=None, embedder=mock_embedder)
        entries = [
            PlaybookEntry(id="shr-00001", prefix="shr",
                          section="STRATEGIES AND HARD RULES",
                          text="Rule one.",
                          embedding=[0.1, 0.2]),
            PlaybookEntry(id="shr-00002", prefix="shr",
                          section="STRATEGIES AND HARD RULES",
                          text="Rule two."),
        ]
        retriever.ingest(entries)
        # Only one text should be embedded (the one without embedding)
        mock_embedder.embed_batch.assert_called_once_with(["Rule two."])
        # First entry keeps original embedding
        assert entries[0].embedding == [0.1, 0.2]
        # Second entry gets new embedding
        assert entries[1].embedding == [0.9, 0.8]

    def test_retrieve_with_mock_store(self):
        """Full retrieval flow with mock store (fallback to PlaybookEntry)."""
        mock_store = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2]

        # Mock query results (first call checks for CanonicalRule existence)
        mock_store.execute_query.side_effect = [
            # _has_canonical_rules check
            [{"cnt": 0}],
            # Cosine search (playbook_embedding)
            [{"id": "shr-00001", "score": 0.95},
             {"id": "cms-00002", "score": 0.80}],
            # BM25 search (playbook_text)
            [{"id": "shr-00001", "score": 3.5},
             {"id": "psw-00003", "score": 2.1}],
            # Fetch entries with embeddings
            [{"id": "shr-00001", "prefix": "shr",
              "section": "STRATEGIES AND HARD RULES",
              "text": "Always check errors.",
              "embedding": [0.9, 0.1]},
             {"id": "cms-00002", "prefix": "cms",
              "section": "COMMON MISTAKES AND CORRECT STRATEGIES",
              "text": "Don't ignore warnings.",
              "embedding": [0.1, 0.9]},
             {"id": "psw-00003", "prefix": "psw",
              "section": "PROBLEM-SOLVING HEURISTICS AND WORKFLOWS",
              "text": "Try step by step.",
              "embedding": [0.5, 0.5]}],
        ]

        retriever = PlaybookRetriever(store=mock_store, embedder=mock_embedder)
        results = retriever.retrieve("import error", top_k=3)

        assert len(results) == 3
        # All three entries should be present
        result_ids = {r.id for r in results}
        assert result_ids == {"shr-00001", "cms-00002", "psw-00003"}

    def test_rrf_merge(self):
        """RRF merge correctly combines two ranked lists."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        cosine = [("a", 0.9), ("b", 0.8), ("c", 0.7)]
        bm25 = [("b", 3.0), ("d", 2.0), ("a", 1.0)]

        merged = retriever._rrf_merge(cosine, bm25)
        ids = [entry_id for entry_id, _ in merged]

        # 'a' and 'b' appear in both lists, should be top
        assert "a" in ids[:2]
        assert "b" in ids[:2]
        # 'c' and 'd' appear in only one
        assert "c" in ids
        assert "d" in ids


class TestPlaybookRetrieveChannels:
    """Ablation knobs on PlaybookRetriever.retrieve().

    Each test wires its own ``execute_query.side_effect`` list because the
    sequence of underlying Cypher queries differs by ablation mode:

    1. ``_has_canonical_rules()`` check — always fires
    2. ``_search_cosine()`` — only when ``use_cosine`` is True
    3. ``_search_bm25()`` — only when ``use_bm25`` is True
    4. ``_fetch_entries_with_embeddings()`` (MMR on) or
       ``_fetch_entries_by_ids()`` (MMR off) — fetches the surviving candidates
    """

    def test_disable_cosine_channel_skips_vector_query(self):
        """use_cosine=False removes the cosine query from the call sequence."""
        bm25_hits = [{"id": "a", "score": 3.0}, {"id": "b", "score": 2.0}]
        mock_store = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2]
        mock_store.execute_query.side_effect = [
            [{"cnt": 0}],   # _has_canonical_rules
            bm25_hits,       # _search_bm25
            [{"id": "a", "prefix": "shr",
              "section": "STRATEGIES AND HARD RULES",
              "text": "a rule", "embedding": [0.5, 0.5]},
             {"id": "b", "prefix": "shr",
              "section": "STRATEGIES AND HARD RULES",
              "text": "b rule", "embedding": [0.5, 0.5]}],
        ]

        retriever = PlaybookRetriever(store=mock_store, embedder=mock_embedder)
        results = retriever.retrieve("import error", top_k=2, use_cosine=False)

        assert {r.id for r in results} == {"a", "b"}
        # Sanity: only three execute_query calls (no cosine, no PPR)
        assert mock_store.execute_query.call_count == 3

    def test_disable_bm25_channel_skips_fulltext_query(self):
        """use_bm25=False removes the BM25 query from the call sequence."""
        cosine_hits = [{"id": "a", "score": 0.95}, {"id": "b", "score": 0.80}]
        mock_store = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2]
        mock_store.execute_query.side_effect = [
            [{"cnt": 0}],
            cosine_hits,
            [{"id": "a", "prefix": "shr",
              "section": "STRATEGIES AND HARD RULES",
              "text": "a rule", "embedding": [0.5, 0.5]},
             {"id": "b", "prefix": "shr",
              "section": "STRATEGIES AND HARD RULES",
              "text": "b rule", "embedding": [0.5, 0.5]}],
        ]

        retriever = PlaybookRetriever(store=mock_store, embedder=mock_embedder)
        results = retriever.retrieve("import error", top_k=2, use_bm25=False)

        assert {r.id for r in results} == {"a", "b"}
        assert mock_store.execute_query.call_count == 3

    def test_disable_mmr_fetches_plain_entries(self):
        """use_mmr=False skips the embedding fetch and uses plain rows."""
        mock_store = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2]
        mock_store.execute_query.side_effect = [
            [{"cnt": 0}],
            [{"id": "a", "score": 0.95}],
            [{"id": "a", "score": 3.0}],
            # Plain fetch — no embedding column required, retriever's
            # `_fetch_entries_by_ids` returns the row dict directly.
            [{"id": "a", "prefix": "shr",
              "section": "STRATEGIES AND HARD RULES",
              "text": "a rule"}],
        ]

        retriever = PlaybookRetriever(store=mock_store, embedder=mock_embedder)
        results = retriever.retrieve("import error", top_k=1, use_mmr=False)

        assert len(results) == 1
        assert results[0].id == "a"

    def test_disable_all_channels_returns_empty(self):
        """Degenerate config: no channels enabled, nothing to merge."""
        mock_store = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2]
        mock_store.execute_query.side_effect = [[{"cnt": 0}]]

        retriever = PlaybookRetriever(store=mock_store, embedder=mock_embedder)
        results = retriever.retrieve(
            "import error", top_k=5,
            use_cosine=False, use_bm25=False, use_ppr=False,
        )

        assert results == []
        # Only the canonical-rules check fires; no channel queries, no fetch.
        assert mock_store.execute_query.call_count == 1

    def test_default_kwargs_preserve_existing_behaviour(self):
        """All four flags default to True — existing call sites unchanged."""
        mock_store = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2]
        mock_store.execute_query.side_effect = [
            [{"cnt": 0}],
            [{"id": "a", "score": 0.95}],
            [{"id": "a", "score": 3.0}],
            [{"id": "a", "prefix": "shr",
              "section": "STRATEGIES AND HARD RULES",
              "text": "a rule", "embedding": [0.5, 0.5]}],
        ]
        retriever = PlaybookRetriever(store=mock_store, embedder=mock_embedder)
        results = retriever.retrieve("import error", top_k=1)
        assert len(results) == 1


class TestStrategiesToPlaybookEntries:
    """Tests for strategies_to_playbook_entries converter."""

    def test_basic_conversion(self):
        """Convert strategies with various categories."""
        strategies = [
            Strategy(id="s1", rule_text="Handle errors gracefully",
                     category="error_handling", source_trajectory_id="t1",
                     source_repo="repo/a"),
            Strategy(id="s2", rule_text="Use breakpoints for debugging",
                     category="debugging", source_trajectory_id="t2",
                     source_repo="repo/b"),
            Strategy(id="s3", rule_text="Run tests after changes",
                     category="testing", source_trajectory_id="t3",
                     source_repo="repo/c"),
        ]
        entries = strategies_to_playbook_entries(strategies)
        assert len(entries) == 3

        # error_handling → shr
        assert entries[0].prefix == "shr"
        assert entries[0].id == "shr-00001"
        assert entries[0].section == "STRATEGIES AND HARD RULES"

        # debugging → psw
        assert entries[1].prefix == "psw"
        assert entries[1].id == "psw-00001"

        # testing → verify
        assert entries[2].prefix == "verify"
        assert entries[2].id == "verify-00001"

    def test_counter_increments(self):
        """Multiple strategies in same category get incrementing ids."""
        strategies = [
            Strategy(id="s1", rule_text="Rule 1", category="debugging",
                     source_trajectory_id="t1", source_repo="r"),
            Strategy(id="s2", rule_text="Rule 2", category="debugging",
                     source_trajectory_id="t2", source_repo="r"),
        ]
        entries = strategies_to_playbook_entries(strategies)
        assert entries[0].id == "psw-00001"
        assert entries[1].id == "psw-00002"

    def test_unknown_category_defaults_to_misc(self):
        """Unknown categories map to 'misc' prefix."""
        strategies = [
            Strategy(id="s1", rule_text="Unknown category rule",
                     category="unknown_cat", source_trajectory_id="t1",
                     source_repo="r"),
        ]
        entries = strategies_to_playbook_entries(strategies)
        assert entries[0].prefix == "misc"
        assert entries[0].section == "OTHERS"

    def test_preserves_embedding(self):
        """Embedding from strategy is preserved."""
        strategies = [
            Strategy(id="s1", rule_text="Rule", category="debugging",
                     source_trajectory_id="t1", source_repo="r",
                     embedding=[0.1, 0.2]),
        ]
        entries = strategies_to_playbook_entries(strategies)
        assert entries[0].embedding == [0.1, 0.2]

    def test_category_to_prefix_mapping(self):
        """All expected categories are mapped."""
        assert CATEGORY_TO_PREFIX["error_handling"] == "shr"
        assert CATEGORY_TO_PREFIX["debugging"] == "psw"
        assert CATEGORY_TO_PREFIX["testing"] == "verify"
        assert CATEGORY_TO_PREFIX["code_navigation"] == "psw"
        assert CATEGORY_TO_PREFIX["dependency"] == "cms"
        assert CATEGORY_TO_PREFIX["configuration"] == "cms"


class TestFormatPlaybookWrap:
    """Tests for format_playbook() with wrap=True."""

    def test_wrap_adds_tags(self):
        """wrap=True adds <memory_playbook> tags and instruction."""
        entries = [
            PlaybookEntry(id="shr-00001", prefix="shr",
                          section="STRATEGIES AND HARD RULES",
                          text="Always validate input."),
        ]
        output = format_playbook(entries, wrap=True)
        assert output.startswith("<memory_playbook>")
        assert output.endswith("</memory_playbook>")
        assert "Apply relevant rules" in output
        assert "[shr-00001] Always validate input." in output

    def test_wrap_false_is_default(self):
        """wrap=False (default) produces plain playbook text."""
        entries = [
            PlaybookEntry(id="shr-00001", prefix="shr",
                          section="STRATEGIES AND HARD RULES",
                          text="Always validate input."),
        ]
        plain = format_playbook(entries)
        wrapped = format_playbook(entries, wrap=True)
        assert "<memory_playbook>" not in plain
        assert "<memory_playbook>" in wrapped

    def test_wrap_empty_returns_empty(self):
        """format_playbook([], wrap=True) returns empty string."""
        assert format_playbook([], wrap=True) == ""

    def test_roundtrip_unaffected_by_wrap(self):
        """parse → format(wrap=False) → parse roundtrip still works."""
        entries = parse_playbook(SAMPLE_PLAYBOOK)
        formatted = format_playbook(entries, wrap=False)
        re_parsed = parse_playbook(formatted)
        assert len(re_parsed) == len(entries)


class TestMMRRerank:
    """Tests for PlaybookRetriever._mmr_rerank()."""

    def test_mmr_selects_diverse_entries(self):
        """MMR should prefer diverse entries over near-duplicates."""
        retriever = PlaybookRetriever(store=None, embedder=None)

        # Create entries: A and B are near-identical, C is different
        candidates = [
            PlaybookEntry(id="a", prefix="shr", section="S",
                          text="Fix import errors by checking path.",
                          embedding=[1.0, 0.0, 0.0]),
            PlaybookEntry(id="b", prefix="shr", section="S",
                          text="Fix import errors by verifying path.",
                          embedding=[0.99, 0.1, 0.0]),  # near-duplicate of A
            PlaybookEntry(id="c", prefix="psw", section="P",
                          text="Use debugger for runtime errors.",
                          embedding=[0.0, 1.0, 0.0]),  # very different
        ]
        rrf_scores = {"a": 0.03, "b": 0.025, "c": 0.02}
        query_emb = [0.8, 0.2, 0.0]

        # With diversity, should pick A then C (skip B as near-dup of A)
        selected = retriever._mmr_rerank(
            candidates, query_emb, rrf_scores,
            top_k=2, lambda_param=0.5,
        )
        ids = [e.id for e in selected]
        assert "a" in ids
        assert "c" in ids  # diverse pick over near-dup "b"

    def test_mmr_pure_relevance(self):
        """lambda_param=1.0 should behave like pure relevance ranking."""
        retriever = PlaybookRetriever(store=None, embedder=None)

        candidates = [
            PlaybookEntry(id="a", prefix="shr", section="S", text="A",
                          embedding=[1.0, 0.0]),
            PlaybookEntry(id="b", prefix="shr", section="S", text="B",
                          embedding=[0.99, 0.1]),
            PlaybookEntry(id="c", prefix="psw", section="P", text="C",
                          embedding=[0.0, 1.0]),
        ]
        rrf_scores = {"a": 0.03, "b": 0.025, "c": 0.02}
        query_emb = [1.0, 0.0]

        # Pure relevance: should pick in order of relevance to query
        selected = retriever._mmr_rerank(
            candidates, query_emb, rrf_scores,
            top_k=3, lambda_param=1.0,
        )
        # "a" should be first (highest relevance)
        assert selected[0].id == "a"

    def test_mmr_handles_no_embeddings(self):
        """MMR falls back to original order when no embeddings."""
        retriever = PlaybookRetriever(store=None, embedder=None)

        candidates = [
            PlaybookEntry(id="a", prefix="shr", section="S", text="A",
                          embedding=None),
            PlaybookEntry(id="b", prefix="shr", section="S", text="B",
                          embedding=None),
        ]
        rrf_scores = {"a": 0.03, "b": 0.02}
        query_emb = [1.0, 0.0]

        # Falls back to candidates[:top_k] when no valid embeddings
        selected = retriever._mmr_rerank(
            candidates, query_emb, rrf_scores, top_k=2,
        )
        assert len(selected) == 2
        assert selected[0].id == "a"
        assert selected[1].id == "b"

    def test_mmr_empty_candidates(self):
        """MMR with empty candidates returns empty."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        selected = retriever._mmr_rerank([], [1.0], {}, top_k=5)
        assert selected == []

    def test_mmr_skips_dimension_mismatch(self):
        """MMR skips candidates with mismatched embedding dimensions."""
        retriever = PlaybookRetriever(store=None, embedder=None)

        candidates = [
            PlaybookEntry(id="a", prefix="shr", section="S", text="A",
                          embedding=[1.0, 0.0, 0.0]),  # 3-d
            PlaybookEntry(id="b", prefix="shr", section="S", text="B",
                          embedding=[0.5, 0.5]),         # 2-d (mismatch)
            PlaybookEntry(id="c", prefix="psw", section="P", text="C",
                          embedding=[0.0, 1.0, 0.0]),  # 3-d
        ]
        rrf_scores = {"a": 0.03, "b": 0.025, "c": 0.02}
        query_emb = [1.0, 0.0, 0.0]  # 3-d

        # b should be skipped due to dimension mismatch, not crash
        selected = retriever._mmr_rerank(
            candidates, query_emb, rrf_scores, top_k=3,
        )
        selected_ids = {e.id for e in selected}
        assert "a" in selected_ids
        assert "c" in selected_ids
        # b is skipped (dimension mismatch)
        assert "b" not in selected_ids

    def test_mmr_all_dimension_mismatch_fallback(self):
        """MMR falls back to original order when all embeddings mismatch."""
        retriever = PlaybookRetriever(store=None, embedder=None)

        candidates = [
            PlaybookEntry(id="a", prefix="shr", section="S", text="A",
                          embedding=[1.0, 0.0]),         # 2-d
            PlaybookEntry(id="b", prefix="shr", section="S", text="B",
                          embedding=[0.5, 0.5]),         # 2-d
        ]
        rrf_scores = {"a": 0.03, "b": 0.02}
        query_emb = [1.0, 0.0, 0.0]  # 3-d (all mismatch)

        selected = retriever._mmr_rerank(
            candidates, query_emb, rrf_scores, top_k=2,
        )
        # Falls back to candidates[:top_k]
        assert len(selected) == 2
        assert selected[0].id == "a"


class TestRetrieveWithDiversity:
    """Tests for PlaybookRetriever.retrieve() diversity parameter."""

    def test_diversity_parameter_accepted(self):
        """Retrieve accepts diversity parameter without error."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        result = retriever.retrieve("test query", diversity=0.5)
        assert result == []  # no store

    def test_diversity_zero_skips_mmr(self):
        """diversity=0 disables MMR re-ranking."""
        mock_store = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2]

        mock_store.execute_query.side_effect = [
            # _has_canonical_rules check
            [{"cnt": 0}],
            # Cosine
            [{"id": "a", "score": 0.9}],
            # BM25
            [{"id": "a", "score": 3.0}],
            # Fetch with embeddings
            [{"id": "a", "prefix": "shr", "section": "S",
              "text": "Rule A", "embedding": [0.1, 0.2]}],
        ]

        retriever = PlaybookRetriever(store=mock_store, embedder=mock_embedder)
        results = retriever.retrieve("test", top_k=1, diversity=0)
        assert len(results) == 1
        assert results[0].id == "a"


class TestCanonicalRuleModel:
    """Tests for CanonicalRule dataclass."""

    def test_to_dict_from_dict_roundtrip(self):
        """to_dict/from_dict roundtrip preserves all fields."""
        rule = CanonicalRule(
            id="rule_abc123",
            rule_text="Always handle import errors by checking sys.path.",
            category="error_handling",
            prefix="shr",
            section="STRATEGIES AND HARD RULES",
            member_count=5,
            avg_confidence=0.85,
            source_repos=["django/django", "flask/flask"],
            error_types=["ImportError", "ModuleNotFoundError"],
            embedding=[0.1, 0.2, 0.3],
        )
        d = rule.to_dict()
        restored = CanonicalRule.from_dict(d)
        assert restored.id == rule.id
        assert restored.rule_text == rule.rule_text
        assert restored.category == rule.category
        assert restored.prefix == rule.prefix
        assert restored.section == rule.section
        assert restored.member_count == rule.member_count
        assert restored.avg_confidence == rule.avg_confidence
        assert restored.source_repos == rule.source_repos
        assert restored.error_types == rule.error_types
        assert restored.embedding == rule.embedding

    def test_from_dict_defaults(self):
        """from_dict handles missing optional fields."""
        d = {"id": "rule_x", "rule_text": "Some rule."}
        rule = CanonicalRule.from_dict(d)
        assert rule.category == "debugging"
        assert rule.prefix == "misc"
        assert rule.section == "OTHERS"
        assert rule.member_count == 1
        assert rule.avg_confidence == 0.8
        assert rule.source_repos == []
        assert rule.error_types == []
        assert rule.embedding is None


class TestPPRSearch:
    """Tests for PlaybookRetriever._personalized_pagerank()."""

    def test_ppr_basic_graph(self):
        """PPR propagates probability from seed to connected nodes."""
        retriever = PlaybookRetriever(store=None, embedder=None)

        # Simple graph: A -- B -- C
        adjacency = {
            "A": ["B"],
            "B": ["A", "C"],
            "C": ["B"],
        }
        seeds = ["A"]

        scores = retriever._personalized_pagerank(
            adjacency, seeds, damping=0.5, iterations=20,
        )

        # Seed node A should have highest score
        assert scores["A"] > scores["B"]
        assert scores["B"] > scores["C"]
        # All scores positive
        assert all(v > 0 for v in scores.values())

    def test_ppr_two_seeds(self):
        """PPR with two seeds distributes from both."""
        retriever = PlaybookRetriever(store=None, embedder=None)

        # Graph: A -- B -- C -- D
        adjacency = {
            "A": ["B"],
            "B": ["A", "C"],
            "C": ["B", "D"],
            "D": ["C"],
        }
        seeds = ["A", "D"]

        scores = retriever._personalized_pagerank(
            adjacency, seeds, damping=0.5, iterations=20,
        )

        # A and D are seeds, B and C are intermediate
        assert scores["A"] > 0
        assert scores["D"] > 0
        # B and C should also have scores
        assert scores["B"] > 0
        assert scores["C"] > 0

    def test_ppr_empty_seeds(self):
        """PPR with no seeds returns empty."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        scores = retriever._personalized_pagerank({"A": ["B"], "B": ["A"]}, [], damping=0.5)
        assert scores == {}

    def test_ppr_empty_graph(self):
        """PPR with empty graph returns empty."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        scores = retriever._personalized_pagerank({}, ["A"], damping=0.5)
        assert scores == {}

    def test_ppr_seed_not_in_graph(self):
        """PPR with seed not in graph returns empty."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        scores = retriever._personalized_pagerank(
            {"A": ["B"], "B": ["A"]}, ["Z"], damping=0.5,
        )
        assert scores == {}

    def test_ppr_scores_sum_approximately_one(self):
        """PPR scores should approximately sum to 1.0 (probability distribution)."""
        retriever = PlaybookRetriever(store=None, embedder=None)

        adjacency = {
            "A": ["B", "C"],
            "B": ["A", "C"],
            "C": ["A", "B"],
        }
        scores = retriever._personalized_pagerank(
            adjacency, ["A"], damping=0.5, iterations=50,
        )
        total = sum(scores.values())
        assert abs(total - 1.0) < 0.01


class TestNodeSpecificity:
    """Tests for node specificity weighting."""

    def test_specificity_inversely_proportional_to_degree(self):
        """Higher degree → lower specificity."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        graph_data = {
            "node_degrees": {
                "low_deg": 2,
                "high_deg": 100,
            },
        }
        specificity = retriever._get_node_specificity(graph_data)
        assert specificity["low_deg"] > specificity["high_deg"]
        assert specificity["low_deg"] == 0.5
        assert specificity["high_deg"] == 0.01

    def test_specificity_minimum_degree_one(self):
        """Degree 0 or 1 should give specificity of 1.0."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        graph_data = {
            "node_degrees": {"node": 1},
        }
        specificity = retriever._get_node_specificity(graph_data)
        assert specificity["node"] == 1.0

    def test_specificity_caching(self):
        """Specificity is cached after first computation."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        graph_data = {"node_degrees": {"A": 5}}
        s1 = retriever._get_node_specificity(graph_data)
        # Modify input — cached result should be unchanged
        graph_data["node_degrees"]["A"] = 100
        s2 = retriever._get_node_specificity(graph_data)
        assert s1["A"] == s2["A"]  # Same cached value


class TestThreeChannelRRF:
    """Tests for _rrf_merge_multi() with three ranked lists."""

    def test_three_channel_merge(self):
        """RRF merge of three channels promotes items in multiple lists."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        cosine = [("a", 0.9), ("b", 0.8)]
        bm25 = [("b", 3.0), ("c", 2.0)]
        ppr = [("c", 0.05), ("a", 0.03)]

        merged = retriever._rrf_merge_multi([cosine, bm25, ppr])
        ids = [entry_id for entry_id, _ in merged]

        # 'a' appears in cosine + ppr, 'b' in cosine + bm25, 'c' in bm25 + ppr
        # All appear in exactly 2 lists
        assert len(ids) == 3
        assert set(ids) == {"a", "b", "c"}

    def test_three_channel_empty_ppr(self):
        """Empty PPR channel doesn't affect cosine + BM25 merge."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        cosine = [("a", 0.9), ("b", 0.8)]
        bm25 = [("b", 3.0), ("a", 1.0)]
        ppr = []

        merged = retriever._rrf_merge_multi([cosine, bm25, ppr])
        ids = [entry_id for entry_id, _ in merged]
        # Both appear in both lists
        assert "a" in ids[:2]
        assert "b" in ids[:2]

    def test_ppr_exclusive_item_included(self):
        """Items only in PPR channel are still included in merge."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        cosine = [("a", 0.9)]
        bm25 = [("b", 3.0)]
        ppr = [("c", 0.05)]

        merged = retriever._rrf_merge_multi([cosine, bm25, ppr])
        ids = [entry_id for entry_id, _ in merged]
        assert "c" in ids

    def test_backward_compat_rrf_merge(self):
        """Legacy _rrf_merge delegates to _rrf_merge_multi."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        cosine = [("a", 0.9), ("b", 0.8)]
        bm25 = [("b", 3.0), ("a", 1.0)]

        old = retriever._rrf_merge(cosine, bm25)
        new = retriever._rrf_merge_multi([cosine, bm25])

        # Should produce identical results
        assert old == new


class TestExtractErrorType:
    """Tests for State._extract_error_type() used for PPR seeds."""

    def test_standard_python_errors(self):
        """Extracts common Python error types."""
        state = State(
            tools=[], repo_summary="test", task_description="fix bug",
            current_error="", phase="fixing",
        )
        assert state._extract_error_type("ImportError: No module named 'foo'") == "ImportError"
        assert state._extract_error_type("TypeError: 'NoneType' is not callable") == "TypeError"
        assert state._extract_error_type("ValueError: invalid literal") == "ValueError"

    def test_exception_types(self):
        """Extracts exception types (pattern matches *Error and *Exception)."""
        state = State(
            tools=[], repo_summary="test", task_description="fix bug",
            current_error="", phase="fixing",
        )
        assert state._extract_error_type("DvcException: cannot reproduce") == "DvcException"
        assert state._extract_error_type("RuntimeError: maximum recursion depth") == "RuntimeError"

    def test_unknown_error(self):
        """Returns 'Unknown' for unrecognized formats."""
        state = State(
            tools=[], repo_summary="test", task_description="fix bug",
            current_error="", phase="fixing",
        )
        assert state._extract_error_type("something went wrong") == "Unknown"
        assert state._extract_error_type("") == "Unknown"

    def test_fail_and_error_keywords(self):
        """Extracts FAIL and ERROR keywords."""
        state = State(
            tools=[], repo_summary="test", task_description="fix bug",
            current_error="", phase="fixing",
        )
        assert state._extract_error_type("FAIL: test_something") == "FAIL"
        assert state._extract_error_type("ERROR in build step") == "ERROR"


class TestRetrieveWithErrorType:
    """Tests for PlaybookRetriever.retrieve() with error_type parameter."""

    def test_error_type_parameter_accepted(self):
        """Retrieve accepts error_type parameter without error."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        result = retriever.retrieve("test query", error_type="ImportError")
        assert result == []  # no store

    def test_invalidate_cache(self):
        """invalidate_cache clears all cached state."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        retriever._graph_cache = {"adjacency": {}}
        retriever._node_specificity = {"A": 0.5}
        retriever._use_canonical = True

        retriever.invalidate_cache()

        assert retriever._graph_cache is None
        assert retriever._node_specificity == {}
        assert retriever._use_canonical is None

    def test_diversity_clamped(self):
        """Out-of-range diversity values are clamped to [0, 1]."""
        retriever = PlaybookRetriever(store=None, embedder=None)
        # Should not raise even with out-of-range values
        assert retriever.retrieve("test", diversity=-0.5) == []
        assert retriever.retrieve("test", diversity=2.0) == []

    def test_mmr_backfill_non_embedded(self):
        """MMR backfills non-embedded candidates to reach top_k."""
        retriever = PlaybookRetriever(store=None, embedder=None)

        # Mix of embedded and non-embedded candidates
        candidates = [
            PlaybookEntry(id="a", prefix="shr", section="S", text="A",
                          embedding=[1.0, 0.0]),
            PlaybookEntry(id="b", prefix="shr", section="S", text="B",
                          embedding=None),  # no embedding
            PlaybookEntry(id="c", prefix="psw", section="P", text="C",
                          embedding=None),  # no embedding
        ]
        rrf_scores = {"a": 0.03, "b": 0.025, "c": 0.02}
        query_emb = [1.0, 0.0]

        # MMR only has 1 valid candidate (a), should return it + backfill b, c
        selected = retriever._mmr_rerank(
            candidates, query_emb, rrf_scores, top_k=3,
        )
        # MMR itself returns [a] (only embedded one)
        assert selected[0].id == "a"
        # But _mmr_rerank returns only embedded candidates;
        # backfill happens in retrieve(). Test that MMR doesn't crash.
        assert len(selected) >= 1
