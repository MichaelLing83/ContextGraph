"""Search an Obsidian graph vault using tags, wikilinks, and text (no Neo4j).

Designed to complement Obsidian's UI: same primitives (#tags, [[links]]),
usable from the terminal or scripts.

Usage:
    uv run python scripts/query_obsidian_graph.py --vault ~/Notes --query "cache invalidation"
    uv run python scripts/query_obsidian_graph.py --vault ~/Notes -q "api" --tag cg/fragment --hops 1
    uv run python scripts/query_obsidian_graph.py --vault ~/Notes --tag cg/fragment --list-tags

    # Merge hits into one knowledge summary (markdown)
    uv run python scripts/query_obsidian_graph.py --vault ~/Graph -q "utmärkt" --tag cg/fragment --summary
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent_memory.vault.obsidian_index import ObsidianVaultIndex
from agent_memory.vault.summarize import (
    _clean_body,
    build_knowledge_summary,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tag/link-aware Obsidian vault search")
    parser.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="Vault to search (use your graph vault when using separate vaults)",
    )
    parser.add_argument("-q", "--query", default="", help="Text query (optional if --tag set)")
    parser.add_argument(
        "--exact-phrase",
        default="",
        help="Require this exact phrase in title or body (case-insensitive)",
    )
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Require tag(s), e.g. cg/fragment (repeatable)",
    )
    parser.add_argument(
        "--tag-prefix",
        default="",
        help="Boost notes whose tags start with this prefix (e.g. cg/)",
    )
    parser.add_argument(
        "--hops",
        type=int,
        default=0,
        help="Expand results along wikilinks (0=off, 1=recommended)",
    )
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--list-tags",
        action="store_true",
        help="List cg/* tags in the vault and exit",
    )
    parser.add_argument(
        "--graph-only",
        action="store_true",
        help="Only search cg/* notes (MOC + Fragments + Sources at vault root)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Output one merged knowledge summary instead of a hit list",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        help="Write summary markdown to this file",
    )
    parser.add_argument(
        "--summary-max-chars",
        type=int,
        default=6000,
        help="Max length of merged summary (default: 6000)",
    )
    parser.add_argument(
        "--summary-meta",
        action="store_true",
        help="Include title/stats in summary output (off by default: content only)",
    )
    parser.add_argument(
        "--full-body",
        action="store_true",
        help="Output full fragment bodies (with --summary; no per-excerpt truncation)",
    )
    args = parser.parse_args()

    vault = args.vault.expanduser().resolve()
    index = ObsidianVaultIndex(vault)
    n = index.build(glob="**/*.md")
    tag_prefix = args.tag_prefix
    if args.graph_only and not tag_prefix and not args.tag:
        tag_prefix = "cg/"
    if n == 0:
        print("No markdown files indexed.", file=sys.stderr)
        sys.exit(1)

    if args.list_tags:
        cg_tags = sorted(t for t in index.tag_to_notes if t.startswith("cg/"))
        for t in cg_tags:
            print(f"{t} ({len(index.tag_to_notes[t])} notes)")
        return

    if not args.query and not args.tag and not args.exact_phrase:
        parser.error("Provide --query and/or --tag and/or --exact-phrase")

    hits = index.search(
        args.query,
        tags=args.tag or None,
        tag_prefix=tag_prefix,
        exact_phrase=args.exact_phrase,
        hops=args.hops,
        limit=args.limit,
    )

    if args.summary or args.summary_out:
        if not hits:
            print("No matches — nothing to summarize.")
            return
        text = build_knowledge_summary(
            args.query or args.exact_phrase,
            hits,
            index,
            max_chars=0 if args.full_body else args.summary_max_chars,
            full_body=args.full_body,
            include_meta=args.summary_meta,
        )
        if args.summary_out:
            args.summary_out.expanduser().write_text(text, encoding="utf-8")
            print(f"Wrote summary to {args.summary_out}")
        else:
            print(text)
        return

    if args.json:
        def hit_payload(h):
            payload = {
                "path": h.rel_path,
                "title": h.title,
                "score": h.score,
                "reasons": h.reasons,
                "tags": sorted(h.tags),
                "snippet": h.snippet,
            }
            if args.full_body:
                note = index.notes.get(h.rel_path)
                payload["body"] = _clean_body(note.body) if note else h.snippet
            return payload

        print(
            json.dumps(
                [hit_payload(h) for h in hits],
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if not hits:
        print("No matches.")
        return

    print(f"Indexed {n} notes — top {len(hits)} hits\n")
    for i, h in enumerate(hits, 1):
        tag_str = " ".join(f"#{t}" for t in sorted(h.tags)[:6])
        print(f"{i}. {h.title}")
        print(f"   {h.rel_path}  (score={h.score:.1f}  {','.join(h.reasons)})")
        if tag_str:
            print(f"   {tag_str}")
        if args.full_body:
            note = index.notes.get(h.rel_path)
            if note:
                print()
                print(_clean_body(note.body))
        elif h.snippet:
            print(f"   … {h.snippet}")
        print()


if __name__ == "__main__":
    main()
