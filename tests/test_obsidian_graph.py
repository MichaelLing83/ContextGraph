"""Tests for Obsidian-native graph build and search."""

from pathlib import Path

from agent_memory.vault.obsidian_graph import (
    ObsidianGraphBuilder,
    related_section_indices,
)
from agent_memory.vault.obsidian_index import ObsidianVaultIndex
from agent_memory.vault.parser import parse_vault_note
from agent_memory.vault.wikilinks import (
    extract_wikilinks,
    note_title_to_filename,
    source_rel_to_stub_stem,
    wikilink_for_path,
)


def test_wikilink_extraction():
    text = "See [[Note A]] and [[Note B#Heading|alias]]."
    links = extract_wikilinks(text)
    assert ("Note A", None) in links
    assert ("Note B", "Heading") in links


def test_ignore_spanning_false_wikilinks():
    """Broken [[ without a nearby ]] must not swallow whole chapters."""
    paragraph = "word " * 200
    text = f"[[{paragraph}]]"
    assert extract_wikilinks(text) == []
    text2 = "[[start\n\nstill going]]"
    assert extract_wikilinks(text2) == []


def test_resolve_skips_absurd_targets(tmp_path: Path):
    from agent_memory.vault.wikilinks import resolve_wikilink_target

    long_target = "être sortis " + ("x" * 500)
    assert resolve_wikilink_target(long_target, tmp_path) is None


def test_build_graph_embedded_subfolder(tmp_path: Path):
    src = tmp_path / "Projects"
    src.mkdir()
    note = src / "My Idea.md"
    note.write_text(
        "# My Idea\n\n## Problem\n\nCache staleness.\n\n## Solution\n\nInvalidate on write.\n",
        encoding="utf-8",
    )

    raw = parse_vault_note(note, tmp_path)
    builder = ObsidianGraphBuilder(tmp_path, graph_dir="ContextGraph")
    builder.ingest_note(raw)
    builder.write_moc()

    frag_dir = tmp_path / "ContextGraph" / "Fragments"
    assert frag_dir.is_dir()
    frag_files = list(frag_dir.glob("*.md"))
    assert len(frag_files) >= 2
    body = frag_files[0].read_text(encoding="utf-8")
    assert "[[Projects/My Idea" in body
    assert "cg/fragment" in body

    index = ObsidianVaultIndex(tmp_path)
    index.build()
    hits = index.search("cache", tags=["cg/fragment"], hops=1, limit=5)
    assert hits
    assert any("cache" in h.snippet.lower() or "Cache" in h.title for h in hits)


def test_build_graph_separate_vaults_frontmatter_default(tmp_path: Path):
    """Default link_mode is frontmatter: no Sources/ stubs or source wikilinks."""
    source = tmp_path / "SourceVault"
    graph = tmp_path / "GraphVault"
    source.mkdir()
    graph.mkdir()
    note = source / "Projects" / "My Idea.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "# My Idea\n\n## Alpha\n\nFirst.\n\n## Beta\n\nSecond.\n",
        encoding="utf-8",
    )

    raw = parse_vault_note(note, source)
    builder = ObsidianGraphBuilder(graph, source_vault=source)
    builder.ingest_note(raw)
    builder.write_moc()

    assert (graph / "MOC.md").is_file()
    assert (graph / "Fragments").is_dir()
    assert not (graph / "Sources").exists()

    frag = next((graph / "Fragments").glob("*.md"))
    text = frag.read_text(encoding="utf-8")
    assert "source_vault:" in text
    assert "source_rel_path:" in text
    assert "[[Sources/" not in text
    assert "cg/source/" in text

    moc = (graph / "MOC.md").read_text(encoding="utf-8")
    assert "[[Sources/" not in moc


def test_build_graph_separate_vaults_stub(tmp_path: Path):
    source = tmp_path / "SourceVault"
    graph = tmp_path / "GraphVault"
    source.mkdir()
    graph.mkdir()
    note = source / "Projects" / "My Idea.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "# My Idea\n\n## Alpha\n\nFirst.\n\n## Beta\n\nSecond.\n",
        encoding="utf-8",
    )

    raw = parse_vault_note(note, source)
    builder = ObsidianGraphBuilder(graph, source_vault=source, link_mode="stub")
    builder.ingest_note(raw)
    builder.write_moc()

    assert (graph / "MOC.md").is_file()
    assert (graph / "Fragments").is_dir()
    assert (graph / "Sources").is_dir()
    stub_files = list((graph / "Sources").glob("*.md"))
    assert stub_files

    frag = next((graph / "Fragments").glob("*.md"))
    text = frag.read_text(encoding="utf-8")
    assert "source_vault:" in text
    assert "source_rel_path:" in text
    assert "[[Sources/" in text

    index = ObsidianVaultIndex(graph)
    index.build()
    hits = index.search("first", tags=["cg/fragment"], limit=5)
    assert hits


def test_recreate_missing_source_stub_from_stale_registry_cache(tmp_path: Path):
    source = tmp_path / "SourceVault"
    graph = tmp_path / "GraphVault"
    source.mkdir()
    graph.mkdir()
    note = source / "Docs" / "Cache.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Cache\n\n## Invalidation\n\nInvalidate on write.\n", encoding="utf-8")

    raw = parse_vault_note(note, source)
    first_builder = ObsidianGraphBuilder(graph, source_vault=source, link_mode="stub")
    first_builder.ingest_note(raw)
    first_builder.save_registry()
    first_builder.write_moc()

    stub_file = next((graph / "Sources").glob("*.md"))
    stub_file.unlink()
    assert not stub_file.exists()

    second_builder = ObsidianGraphBuilder(graph, source_vault=source, link_mode="stub")
    second_builder.load_registry()
    second_builder.ingest_note(raw)
    second_builder.save_registry()

    recreated_stub = graph / "Sources" / stub_file.name
    assert recreated_stub.is_file()
    assert "[[Sources/" in (graph / "MOC.md").read_text(encoding="utf-8")


def test_wikilink_for_path():
    assert wikilink_for_path("a/b.md") == "[[a/b]]"
    assert wikilink_for_path("a/b.md", "H") == "[[a/b#H]]"


def test_source_stub_stem_no_double_md():
    assert source_rel_to_stub_stem("docs/concepts/cache.md") == "docs--concepts--cache"


def test_note_title_to_filename_strips_square_brackets():
    assert note_title_to_filename("coiled--Managing script dependencies with uv – ]") == (
        "coiled--Managing script dependencies with uv –"
    )


def test_related_section_indices():
    assert related_section_indices(0, 1, mode="all") == []
    assert related_section_indices(2, 5, mode="all") == [0, 1, 3, 4]
    assert related_section_indices(2, 5, mode="none") == []
    assert related_section_indices(2, 5, mode="adjacent") == [1, 3]
    assert related_section_indices(0, 5, mode="topk", topk=2) == [1, 2]
    assert related_section_indices(4, 5, mode="topk", topk=2) == [3, 2]
    assert related_section_indices(2, 5, mode="topk", topk=2) == [1, 3]


def test_related_mode_none_on_build(tmp_path: Path):
    note = tmp_path / "Doc.md"
    note.write_text(
        "# Doc\n\n## One\n\na\n\n## Two\n\nb\n\n## Three\n\nc\n",
        encoding="utf-8",
    )
    raw = parse_vault_note(note, tmp_path)
    builder = ObsidianGraphBuilder(
        tmp_path, graph_dir="ContextGraph", related_mode="none"
    )
    builder.ingest_note(raw)
    for frag in (tmp_path / "ContextGraph" / "Fragments").glob("*.md"):
        related_block = frag.read_text(encoding="utf-8").split("## Related", 1)[1]
        assert "_None._" in related_block
        assert "[[Fragments/" not in related_block


def test_related_mode_adjacent_on_build(tmp_path: Path):
    note = tmp_path / "Doc.md"
    note.write_text(
        "# Doc\n\n## One\n\na\n\n## Two\n\nb\n\n## Three\n\nc\n",
        encoding="utf-8",
    )
    raw = parse_vault_note(note, tmp_path)
    builder = ObsidianGraphBuilder(
        tmp_path, graph_dir="ContextGraph", related_mode="adjacent"
    )
    builder.ingest_note(raw)
    middle = tmp_path / "ContextGraph" / "Fragments" / "Doc--Two.md"
    assert middle.is_file()
    related = middle.read_text(encoding="utf-8").split("## Related", 1)[1]
    assert related.count("- [[") == 2


def test_semantic_links_built_from_cg_llm_summary(tmp_path: Path):
    source = tmp_path / "Source"
    graph = tmp_path / "Graph"
    source.mkdir()
    graph.mkdir()

    class DummySummarizer:
        def summarize(self, heading, body):
            if "cache" in body.lower():
                return "Cache invalidation strategy for dependency updates."
            if "update" in body.lower():
                return "Dependency update strategy uses cache invalidation."
            return "Completely different topic."

    (source / "A.md").write_text(
        "# A\n\nCache strategy section.\n",
        encoding="utf-8",
    )
    (source / "B.md").write_text(
        "# B\n\nUpdate flow section.\n",
        encoding="utf-8",
    )
    (source / "C.md").write_text(
        "# C\n\n## Unrelated\n\nThree.\n",
        encoding="utf-8",
    )

    builder = ObsidianGraphBuilder(
        graph,
        source_vault=source,
        summarizer=DummySummarizer(),
        chunk_mode="chapter",
    )
    for p in sorted(source.glob("*.md")):
        builder.ingest_note(parse_vault_note(p, source))

    written = builder.build_semantic_links_from_summaries(topk=1, min_similarity=0.2)
    assert written == 3

    a = (graph / "Fragments" / "A--A.md").read_text(encoding="utf-8")
    b = (graph / "Fragments" / "B--B.md").read_text(encoding="utf-8")
    c = (graph / "Fragments" / "C--C.md").read_text(encoding="utf-8")

    assert "## Semantic" in a
    assert "[[B--B]]" in a
    assert "## Semantic" in b
    assert "[[A--A]]" in b
    assert "## Semantic" in c
    assert "_None._" in c.split("## Semantic", 1)[1]


def test_wikilink_short_same_folder():
    assert (
        wikilink_for_path(
            "Fragments/cache--Caching.md",
            from_rel="Fragments/cache--Dependency caching.md",
        )
        == "[[cache--Caching]]"
    )


def test_search_exact_phrase_filters_to_contiguous_match(tmp_path: Path):
    (tmp_path / "a.md").write_text(
        "# A\n\nUse `uv run` for script execution.\n",
        encoding="utf-8",
    )
    (tmp_path / "b.md").write_text(
        "# B\n\nUse uv to run scripts locally.\n",
        encoding="utf-8",
    )
    (tmp_path / "c.md").write_text(
        "# uv run cookbook\n\nExamples.\n",
        encoding="utf-8",
    )

    index = ObsidianVaultIndex(tmp_path)
    index.build()

    hits = index.search("uv run", exact_phrase="uv run", limit=10)
    hit_paths = {h.rel_path for h in hits}
    assert "a.md" in hit_paths
    assert "c.md" in hit_paths
    assert "b.md" not in hit_paths


def test_search_exact_phrase_works_without_token_query(tmp_path: Path):
    (tmp_path / "a.md").write_text("# A\n\nRun with uv run.\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# B\n\nRun with uv.\n", encoding="utf-8")

    index = ObsidianVaultIndex(tmp_path)
    index.build()

    hits = index.search("", exact_phrase="uv run", limit=10)
    assert [h.rel_path for h in hits] == ["a.md"]
