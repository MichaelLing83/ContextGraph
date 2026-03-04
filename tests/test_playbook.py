"""Tests for the playbook parsing, retrieval, and formatting module."""

import pytest
from unittest.mock import MagicMock

from agent_memory.models import PlaybookEntry, PLAYBOOK_SECTIONS
from agent_memory.playbook import parse_playbook, format_playbook, PlaybookRetriever
from agent_memory.strategy_extractor import (
    strategies_to_playbook_entries,
    CATEGORY_TO_PREFIX,
)
from agent_memory.models import Strategy


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
        """Full retrieval flow with mock store."""
        mock_store = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2]

        # Mock cosine search results
        mock_store.execute_query.side_effect = [
            # Cosine search
            [{"id": "shr-00001", "score": 0.95},
             {"id": "cms-00002", "score": 0.80}],
            # BM25 search
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
