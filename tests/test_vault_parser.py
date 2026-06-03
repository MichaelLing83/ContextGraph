"""Tests for Obsidian vault parsing and segmentation."""

from pathlib import Path

from agent_memory.vault.parser import markdown_to_plain_text, parse_vault_note
from agent_memory.vault.segmenter import (
    cap_fragment_sizes,
    segment_note,
    segment_note_adaptive,
)


def test_strip_frontmatter_and_wikilinks():
    raw = """---
tags: [a]
---
# Title

See [[Other Note|other]] and `code`.

```python
print(1)
```

Body **bold** text.
"""
    plain = markdown_to_plain_text(raw)
    assert "tags:" not in plain
    assert "other" in plain
    assert "print(1)" not in plain
    assert "bold" in plain


def test_segment_by_headings(tmp_path: Path):
    md = tmp_path / "note.md"
    md.write_text(
        "# Root\n\nIntro.\n\n## One\n\nFirst section.\n\n## Two\n\nSecond section.\n",
        encoding="utf-8",
    )
    note = parse_vault_note(md, tmp_path)
    sections = segment_note(note, mode="heading")
    # H1 + H2s: segmenter emits one block per heading (incl. document title)
    assert len(sections) >= 2
    headings = [s.heading for s in sections]
    assert "One" in headings
    assert "Two" in headings
    one = next(s for s in sections if s.heading == "One")
    assert "First section" in one.body


def test_ignore_headings_inside_fenced_code_blocks(tmp_path: Path):
    md = tmp_path / "fenced.md"
    md.write_text(
        """# Root

## Real section

Before code.

```python
# Not a heading
## Still not a heading
print("ok")
```

After code.
""",
        encoding="utf-8",
    )
    note = parse_vault_note(md, tmp_path)
    sections = segment_note(note, mode="heading")
    headings = [s.heading for s in sections]
    assert "Real section" in headings
    assert "Not a heading" not in headings
    assert "Still not a heading" not in headings


def test_fragment_preserves_code_blocks_and_links(tmp_path: Path):
    md = tmp_path / "cache.md"
    md.write_text(
        """# Cache

## Dynamic metadata

See [tool.uv.cache-keys](https://docs.astral.sh/uv/reference/settings/#cache-keys).

```toml
[tool.uv]
cache-keys = [{ file = "pyproject.toml" }]
```
""",
        encoding="utf-8",
    )
    note = parse_vault_note(md, tmp_path)
    sections = segment_note(note, mode="heading", preserve_markup=True)
    dyn = next(s for s in sections if s.heading == "Dynamic metadata")
    assert "```toml" in dyn.body
    assert "cache-keys" in dyn.body
    assert "tool.uv.cache-keys" in dyn.body
    assert "https://docs.astral.sh" in dyn.body

    plain_sections = segment_note(note, mode="heading", preserve_markup=False)
    dyn_plain = next(s for s in plain_sections if s.heading == "Dynamic metadata")
    assert "```" not in dyn_plain.body


def test_cap_fragment_sizes_splits_by_paragraph():
    from agent_memory.vault.models import VaultSection

    big = "para one.\n\n" + ("word " * 800) + "\n\n" + ("word " * 800)
    sec = VaultSection(index=0, heading="Big section", body=big, level=2)
    parts = cap_fragment_sizes([sec], max_chars=1000)
    assert len(parts) >= 2
    merged = "\n\n".join(p.body for p in parts)
    assert "para one" in merged
    assert sum(len(p.body) for p in parts) >= len(big) - 20
    assert all(len(p.body) <= 1000 or "word" in p.body for p in parts)


def test_adaptive_short_note_one_fragment(tmp_path: Path):
    md = tmp_path / "short.md"
    md.write_text("# Title\n\nShort body under limit.\n", encoding="utf-8")
    note = parse_vault_note(md, tmp_path)
    sections = segment_note_adaptive(note, target_chars=500)
    assert len(sections) == 1
    assert "Short body" in sections[0].body


def test_adaptive_greedy_merge(tmp_path: Path):
    md = tmp_path / "long.md"
    parts = [f"Paragraph {i}. " + ("word " * 60) for i in range(6)]
    md.write_text("# Ch\n\n" + "\n\n".join(parts), encoding="utf-8")
    note = parse_vault_note(md, tmp_path)
    sections = segment_note_adaptive(note, target_chars=500)
    assert len(sections) < len(parts)
    merged = "\n\n".join(s.body for s in sections)
    for i in range(6):
        assert f"Paragraph {i}" in merged


def test_segment_paragraphs_when_no_headings(tmp_path: Path):
    md = tmp_path / "flat.md"
    body = "\n\n".join(f"Paragraph {i}. " + ("word " * 40) for i in range(8))
    md.write_text(body, encoding="utf-8")
    note = parse_vault_note(md, tmp_path)
    sections = segment_note(note, mode="heading")
    assert len(sections) >= 1
