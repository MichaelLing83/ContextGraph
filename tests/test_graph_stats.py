"""Tests for graph vault statistics."""

from pathlib import Path

from agent_memory.vault.graph_stats import compute_graph_vault_stats


def test_graph_stats_counts(tmp_path: Path):
    vault = tmp_path / "g"
    frag = vault / "Fragments"
    src = vault / "Sources"
    frag.mkdir(parents=True)
    src.mkdir()
    (vault / "MOC.md").write_text("# MOC\n", encoding="utf-8")
    (frag / "a.md").write_text(
        "---\ntags: [cg/fragment]\n---\n\n# A\n\n" + "x" * 100 + "\n\n## Related\n\n- [[b]]\n",
        encoding="utf-8",
    )
    (frag / "b.md").write_text(
        "---\ntags: [cg/fragment]\n---\n\n# B\n\nshort\n\n- [[a]]\n",
        encoding="utf-8",
    )
    (src / "src-a.md").write_text("# Source\n", encoding="utf-8")

    stats = compute_graph_vault_stats(vault)
    assert stats.nodes["fragments"] == 2
    assert stats.nodes["source_stubs"] == 1
    assert stats.fragment_body_chars.count == 2
    assert stats.fragment_body_chars.min <= stats.fragment_body_chars.max
    assert stats.fragment_body_chars.max >= 100
    assert stats.edges["wikilinks_total"] >= 2
    assert stats.edges["wikilinks_resolved"] >= 2
