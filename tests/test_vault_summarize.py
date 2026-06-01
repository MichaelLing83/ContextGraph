"""Tests for vault search summary formatting."""

from agent_memory.vault.obsidian_index import ObsidianVaultIndex, SearchHit
from agent_memory.vault.summarize import build_knowledge_summary, _clean_body, _excerpt


def test_clean_body_strips_graph_section():
    body = "# Title\n\nMain text here.\n\n## Graph\n\n- links\n\n## Related\n\n- x"
    assert "Main text" in _clean_body(body)
    assert "Graph" not in _clean_body(body)


def test_build_summary_groups(tmp_path):
    vault = tmp_path / "g"
    frag = vault / "Fragments" / "a.md"
    frag.parent.mkdir(parents=True)
    frag.write_text(
        """---
source_rel_path: Books/Ch1.md
tags:
  - cg/fragment
  - cg/source/ch1
---

# Alpha section

The cache is invalid.

## Graph

- x
""",
        encoding="utf-8",
    )
    index = ObsidianVaultIndex(vault)
    index.build()
    hits = [
        SearchHit(
            rel_path=str(frag.relative_to(vault)),
            title="Alpha section",
            score=5.0,
            reasons=["body"],
            snippet="cache",
            tags={"cg/fragment", "cg/source/ch1"},
        )
    ]
    out = build_knowledge_summary("cache", hits, index, max_chars=3000)
    assert "cache" in out.lower()
    assert "基于图库" not in out
    assert "来源：" not in out
    out_meta = build_knowledge_summary(
        "cache", hits, index, max_chars=3000, include_meta=True
    )
    assert "知识摘要" in out_meta


def test_excerpt_finds_query():
    text = "aaa " * 50 + "utmärkt" + " bbb" * 50
    ex = _excerpt(text, "utmärkt", 100)
    assert "utmärkt" in ex


def test_build_summary_full_body_no_truncation(tmp_path):
    vault = tmp_path / "g"
    frag = vault / "Fragments" / "a.md"
    frag.parent.mkdir(parents=True)
    long_body = "uv run " + ("detail " * 200)
    frag.write_text(
        f"""---
source_rel_path: docs/tools.md
tags:
  - cg/fragment
  - cg/source/tools
---

# Tools

{long_body}

## Graph

- x
""",
        encoding="utf-8",
    )
    index = ObsidianVaultIndex(vault)
    index.build()
    hits = [
        SearchHit(
            rel_path=str(frag.relative_to(vault)),
            title="Tools",
            score=5.0,
            reasons=["body"],
            snippet="uv run",
            tags={"cg/fragment", "cg/source/tools"},
        )
    ]
    out = build_knowledge_summary("uv run", hits, index, full_body=True, max_chars=0)
    assert "detail detail" in out
    assert not out.rstrip().endswith("…")
    out_short = build_knowledge_summary("uv run", hits, index, per_excerpt=80)
    assert out_short.rstrip().endswith("…")
