"""Tests for Obsidian-native graph build and search."""

from pathlib import Path

from agent_memory.vault.obsidian_graph import ObsidianGraphBuilder
from agent_memory.vault.obsidian_index import ObsidianVaultIndex
from agent_memory.vault.parser import parse_vault_note
from agent_memory.vault.wikilinks import extract_wikilinks, wikilink_for_path


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


def test_build_graph_separate_vaults(tmp_path: Path):
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


def test_wikilink_for_path():
    assert wikilink_for_path("a/b.md") == "[[a/b]]"
    assert wikilink_for_path("a/b.md", "H") == "[[a/b#H]]"
