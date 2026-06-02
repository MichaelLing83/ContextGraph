from pathlib import Path

from scripts.build_obsidian_graph import _clear_generated_graph_outputs


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_clear_generated_outputs_keeps_llm_cache(tmp_path: Path):
    graph_root = tmp_path / "Graph"
    _write(graph_root / "Fragments" / "a.md")
    _write(graph_root / "Sources" / "s.md")
    _write(graph_root / "MOC.md")
    _write(graph_root / ".graph_registry.json", "{}")
    _write(graph_root / "build_report.json", "{}")
    _write(graph_root / ".llm_summary_cache" / "model.json", "{\"entries\":{}}")
    _write(graph_root / "notes" / "keep.md", "keep")

    _clear_generated_graph_outputs(graph_root)

    assert not (graph_root / "Fragments").exists()
    assert not (graph_root / "Sources").exists()
    assert not (graph_root / "MOC.md").exists()
    assert not (graph_root / ".graph_registry.json").exists()
    assert not (graph_root / "build_report.json").exists()

    # Explicitly preserved by design.
    assert (graph_root / ".llm_summary_cache" / "model.json").is_file()
    # Unrelated user content should not be touched.
    assert (graph_root / "notes" / "keep.md").is_file()
