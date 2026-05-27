"""Repair wikilinks and Source stub filenames in an existing graph vault.

Fixes:
  - Sources/*.md.md → Sources/*.md (double extension bug)
  - [[Sources/...foo.md]] → [[Sources/...foo]]
  - [[Fragments/x]] → [[x]] inside Fragments/ notes

Usage:
    uv run python scripts/repair_obsidian_graph_links.py --vault ~/Documents/uvGraph
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(#[^\]|]+)?\]\]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair Obsidian wikilinks in graph vault")
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    vault = args.vault.expanduser().resolve()
    renamed = 0
    files_patched = 0

    sources = vault / "Sources"
    if sources.is_dir():
        for path in sorted(sources.glob("*.md.md")):
            target = path.with_suffix("")  # drop one .md
            if target.exists() and target != path:
                print(f"Skip rename (exists): {path.name}")
                continue
            print(f"Rename: {path.name} -> {target.name}")
            if not args.dry_run:
                path.rename(target)
            renamed += 1

    for md in vault.rglob("*.md"):
        if ".obsidian" in md.parts:
            continue
        text = md.read_text(encoding="utf-8")
        new_text = text
        rel = str(md.relative_to(vault)).replace("\\", "/")
        in_fragments = rel.startswith("Fragments/")

        def repl(m: re.Match) -> str:
            target = m.group(1).strip()
            heading = m.group(2) or ""
            if target.lower().endswith(".md"):
                target = target[:-3]
            if in_fragments and target.startswith("Fragments/"):
                target = target[len("Fragments/") :]
            return f"[[{target}{heading}]]"

        new_text = _WIKILINK_RE.sub(repl, new_text)
        if new_text != text:
            files_patched += 1
            if not args.dry_run:
                md.write_text(new_text, encoding="utf-8")

    print(f"Done: renamed={renamed}, files_patched={files_patched}")


if __name__ == "__main__":
    main()
