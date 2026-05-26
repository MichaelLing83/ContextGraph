"""Parse markdown notes from an Obsidian vault (text only)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, Optional

from agent_memory.vault.models import RawVaultNote

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_SKIP_DIR_NAMES = frozenset({".obsidian", ".trash", ".git", "node_modules"})


def strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter if present."""
    return _FRONTMATTER_RE.sub("", text, count=1).lstrip()


def markdown_to_plain_text(text: str, *, strip_fm: bool = True) -> str:
    """Keep readable text; drop fenced code blocks and most markup."""
    if strip_fm:
        text = strip_frontmatter(text)
    # Remove fenced code blocks (content omitted for embedding focus)
    text = re.sub(r"```[\s\S]*?```", "\n", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Wikilinks: [[target|alias]] -> alias or target
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    # Images and links
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Headings / emphasis
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", text)
    # Normalize whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _title_from_note(text: str, path: Path) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return path.stem.replace("-", " ").replace("_", " ")


def parse_vault_note(path: Path, vault_root: Path) -> RawVaultNote:
    """Read one markdown file and return structured note data."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    structured = strip_frontmatter(raw)
    plain = markdown_to_plain_text(structured, strip_fm=False)
    rel = str(path.relative_to(vault_root))
    folder = path.parent.relative_to(vault_root)
    folder_str = str(folder) if str(folder) != "." else ""
    return RawVaultNote(
        path=path,
        rel_path=rel,
        title=_title_from_note(raw, path),
        body=plain,
        folder=folder_str,
        structured_body=structured,
    )


def iter_vault_notes(
    vault_root: Path,
    *,
    glob: str = "**/*.md",
    max_notes: Optional[int] = None,
) -> Iterator[RawVaultNote]:
    """Yield parsed notes under vault_root, skipping Obsidian internal dirs."""
    vault_root = vault_root.resolve()
    count = 0
    for path in sorted(vault_root.glob(glob)):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        yield parse_vault_note(path, vault_root)
        count += 1
        if max_notes is not None and count >= max_notes:
            return
