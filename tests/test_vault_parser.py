"""Tests for Obsidian vault parsing and segmentation."""

from pathlib import Path

from agent_memory.vault.parser import markdown_to_plain_text, parse_vault_note
from agent_memory.vault.segmenter import segment_note


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
    sections = segment_note(note)
    # H1 + H2s: segmenter emits one block per heading (incl. document title)
    assert len(sections) >= 2
    headings = [s.heading for s in sections]
    assert "One" in headings
    assert "Two" in headings
    one = next(s for s in sections if s.heading == "One")
    assert "First section" in one.body


def test_segment_paragraphs_when_no_headings(tmp_path: Path):
    md = tmp_path / "flat.md"
    body = "\n\n".join(f"Paragraph {i}. " + ("word " * 40) for i in range(8))
    md.write_text(body, encoding="utf-8")
    note = parse_vault_note(md, tmp_path)
    sections = segment_note(note)
    assert len(sections) >= 1
