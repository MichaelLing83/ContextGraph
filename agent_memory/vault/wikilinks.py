"""Obsidian wikilink and tag parsing."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Set, Tuple
from urllib.parse import unquote

# [[target]], [[target|alias]], [[target#heading]], [[target#^block]]
# Targets must be single-line: broken notes with [[ ... ]] spanning paragraphs
# are ignored (they caused "file name too long" on resolve).
_MAX_WIKILINK_TARGET_LEN = 200

_WIKILINK_RE = re.compile(
    r"\[\["
    r"([^\]|#\n]{1,200})"  # target path or name
    r"(?:#([^\]|#\n]{1,120}))?"  # optional heading/block
    r"(?:\|([^\]\n]{1,120}))?"  # optional alias
    r"\]\]"
)
_TAG_RE = re.compile(r"(?<![\w/`])(#[\w/\-]+)")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def is_plausible_wikilink_target(target: str) -> bool:
    """Reject targets that cannot be Obsidian note paths."""
    if not target or len(target) > _MAX_WIKILINK_TARGET_LEN:
        return False
    if "\n" in target or "\r" in target:
        return False
    if target.startswith(".") or ".." in target.split("/"):
        return False
    # Real note titles/paths are not wall-of-text paragraphs.
    if len(target) > 80 and target.count(" ") > 12:
        return False
    return True


def extract_wikilinks(text: str) -> List[Tuple[str, Optional[str]]]:
    """Return (target, heading) pairs from wikilinks in markdown."""
    out: List[Tuple[str, Optional[str]]] = []
    for m in _WIKILINK_RE.finditer(text):
        target = m.group(1).strip()
        if not is_plausible_wikilink_target(target):
            continue
        heading = m.group(2).strip() if m.group(2) else None
        if heading and not is_plausible_wikilink_target(heading):
            heading = None
        out.append((target, heading))
    return out


def extract_inline_tags(text: str) -> Set[str]:
    """Return #tags found in note body (Obsidian style)."""
    return {t.lstrip("#") for t in _TAG_RE.findall(text)}


def parse_frontmatter_tags(fm_text: str) -> Set[str]:
    """Parse tags from YAML frontmatter (tags: list or comma-separated)."""
    tags: Set[str] = set()
    in_tags = False
    for line in fm_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("tags:"):
            in_tags = True
            rest = stripped[5:].strip()
            if rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1]
                tags.update(_split_tag_values(inner))
                in_tags = False
            elif rest:
                tags.update(_split_tag_values(rest))
                in_tags = False
            continue
        if in_tags:
            if stripped.startswith("- "):
                tags.add(stripped[2:].strip().strip('"').strip("'"))
            elif not stripped:
                in_tags = False
            else:
                tags.update(_split_tag_values(stripped))
                in_tags = False
    return tags


def _split_tag_values(raw: str) -> Set[str]:
    parts = re.split(r"[,;]", raw)
    return {p.strip().strip('"').strip("'") for p in parts if p.strip()}


def split_frontmatter(text: str) -> Tuple[dict, str]:
    """Return (metadata dict, body). Metadata uses simple keys only."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_raw = m.group(1)
    body = text[m.end() :]
    meta: dict = {}
    for line in fm_raw.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key == "tags":
            meta["tags"] = sorted(parse_frontmatter_tags(fm_raw))
        elif key.startswith("cg_") or key in (
            "source",
            "aliases",
            "source_rel_path",
            "source_vault",
            "source_heading",
        ):
            meta[key] = val
    if "tags" not in meta:
        meta["tags"] = sorted(parse_frontmatter_tags(fm_raw))
    return meta, body


def note_title_to_filename(title: str) -> str:
    """Safe filename stem for a graph node note."""
    name = re.sub(r'[<>:"/\\|?*]', "", title).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:120] if name else "untitled"


def wikilink_for_path(rel_path: str, heading: Optional[str] = None) -> str:
    """Build Obsidian wikilink from vault-relative path (with or without .md)."""
    stem = rel_path
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    if heading:
        return f"[[{stem}#{heading}]]"
    return f"[[{stem}]]"


def resolve_wikilink_target(
    target: str,
    vault_root: Path,
    source_path: Optional[Path] = None,
) -> Optional[Path]:
    """
    Resolve wikilink target to an existing .md file in the vault.

    Tries: exact path, +.md, basename match, relative to source folder.
    """
    vault_root = vault_root.resolve()
    target = unquote(target.strip())
    if not is_plausible_wikilink_target(target):
        return None

    candidates: List[Path] = []

    if target.lower().endswith(".md"):
        candidates.append(vault_root / target)
    else:
        candidates.append(vault_root / f"{target}.md")
        candidates.append(vault_root / target)

    if source_path:
        src_dir = source_path.parent
        candidates.append(src_dir / f"{target}.md")
        candidates.append(src_dir / target)

    # Basename search (last resort)
    basename = Path(target).name
    if not basename.lower().endswith(".md"):
        basename_md = f"{basename}.md"
    else:
        basename_md = basename

    for c in candidates:
        try:
            if c.is_file():
                return c.resolve()
        except OSError:
            continue

    if len(basename_md) > _MAX_WIKILINK_TARGET_LEN + 4:
        return None

    try:
        for path in vault_root.rglob(basename_md):
            if path.is_file() and ".obsidian" not in path.parts:
                return path.resolve()
    except OSError:
        return None
    return None
