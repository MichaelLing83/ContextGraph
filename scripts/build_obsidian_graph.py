"""Build a context graph as Obsidian notes (no Neo4j).

Recommended: separate source and graph vaults.

    uv run python scripts/build_obsidian_graph.py \\
        --source-vault ~/Notes \\
        --graph-vault ~/NotesGraph

Legacy (graph as subfolder inside one vault):

    uv run python scripts/build_obsidian_graph.py --vault ~/Notes

Usage:
    uv run python scripts/build_obsidian_graph.py --source-vault ~/Notes --graph-vault ~/NotesGraph
    uv run python scripts/build_obsidian_graph.py --source-vault ~/Notes --graph-vault ~/NotesGraph --link-mode symlink
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent_memory.vault import iter_vault_notes
from agent_memory.vault.graph_stats import (
    compute_graph_vault_stats,
    format_graph_stats,
)
from agent_memory.vault.obsidian_graph import (
    DEFAULT_GRAPH_DIR,
    ObsidianGraphBuilder,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Obsidian-native context graph (wikilinks + tags)"
    )
    parser.add_argument(
        "--source-vault",
        type=Path,
        help="Vault to read original markdown from",
    )
    parser.add_argument(
        "--graph-vault",
        type=Path,
        help="Separate vault to write the knowledge graph into",
    )
    parser.add_argument(
        "--vault",
        type=Path,
        help="(Legacy) Single vault: read notes and write under --graph-dir/",
    )
    parser.add_argument(
        "--graph-dir",
        default="",
        help=f"Subfolder inside graph vault when using --vault (default: {DEFAULT_GRAPH_DIR})",
    )
    parser.add_argument(
        "--link-mode",
        choices=("stub", "symlink", "frontmatter"),
        default="stub",
        help="How fragments link back to source vault (default: stub index cards)",
    )
    parser.add_argument("--max-notes", type=int, default=0)
    parser.add_argument("--glob", default="**/*.md")
    parser.add_argument(
        "--chunk-mode",
        choices=("heading", "adaptive", "chapter"),
        default="heading",
        help="Fragment granularity: heading (per ##), adaptive (greedy target size), chapter (one per note)",
    )
    parser.add_argument(
        "--fragment-chars",
        type=int,
        default=500,
        help="Target max plain-text chars per fragment when --chunk-mode=adaptive (default: 500)",
    )
    parser.add_argument(
        "--fragment-max-chars",
        type=int,
        default=3000,
        help="Hard cap: split longer fragments by markdown paragraphs (default: 3000, 0=off)",
    )
    parser.add_argument(
        "--strip-markup",
        action="store_true",
        help="Strip code blocks and links to plain text (legacy lightweight mode)",
    )
    parser.add_argument(
        "--related-mode",
        choices=("all", "none", "adjacent", "topk"),
        default="all",
        help=(
            "How fragments from the same source note link in ## Related: "
            "all (default), none, adjacent (prev/next only), topk (nearest by order)"
        ),
    )
    parser.add_argument(
        "--related-topk",
        type=int,
        default=2,
        metavar="K",
        help=(
            "With --related-mode=topk: max sibling links per fragment, "
            "nearest in document order (default: 2)"
        ),
    )
    args = parser.parse_args()

    if args.vault and not args.source_vault:
        source_vault = args.vault.expanduser().resolve()
        graph_vault = source_vault
        graph_dir = args.graph_dir or DEFAULT_GRAPH_DIR
    elif args.source_vault and args.graph_vault:
        source_vault = args.source_vault.expanduser().resolve()
        graph_vault = args.graph_vault.expanduser().resolve()
        graph_dir = args.graph_dir
    else:
        parser.error(
            "Provide --source-vault and --graph-vault, or legacy --vault"
        )

    if not source_vault.is_dir():
        logger.error("Source vault not found: %s", source_vault)
        sys.exit(1)

    graph_vault.mkdir(parents=True, exist_ok=True)

    max_notes = args.max_notes if args.max_notes > 0 else None
    builder = ObsidianGraphBuilder(
        graph_vault,
        source_vault=source_vault,
        graph_dir=graph_dir,
        link_mode=args.link_mode,
        chunk_mode=args.chunk_mode,
        fragment_chars=args.fragment_chars,
        fragment_max_chars=args.fragment_max_chars,
        preserve_markup=not args.strip_markup,
        related_mode=args.related_mode,
        related_topk=args.related_topk,
    )
    builder.load_registry()
    builder.setup_cross_vault_links()

    ingested = 0
    errors: list[str] = []
    start = time.time()

    for note in iter_vault_notes(source_vault, glob=args.glob, max_notes=max_notes):
        try:
            builder.ingest_note(note)
            ingested += 1
        except Exception as e:
            errors.append(f"{note.rel_path}: {e}")
            logger.warning("Failed %s: %s", note.rel_path, e)

    moc = builder.write_moc()
    builder.save_registry()

    graph_stats = compute_graph_vault_stats(
        graph_vault, graph_root=builder.graph_root
    )

    report = {
        "source_vault": str(source_vault),
        "graph_vault": str(graph_vault),
        "separate_vaults": builder.separate_vaults,
        "link_mode": args.link_mode,
        "chunk_mode": args.chunk_mode,
        "fragment_chars": args.fragment_chars,
        "fragment_max_chars": args.fragment_max_chars,
        "preserve_markup": not args.strip_markup,
        "related_mode": args.related_mode,
        "related_topk": args.related_topk,
        "notes_ingested": ingested,
        "errors": errors[:20],
        "moc": str(moc.relative_to(graph_vault)),
        "elapsed_sec": round(time.time() - start, 2),
        "graph_stats": graph_stats.to_dict(),
    }
    out = builder.graph_root / "build_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Build finished in %.1fs — %d source notes ingested", report["elapsed_sec"], ingested)
    print(format_graph_stats(graph_stats))
    if errors:
        logger.warning("%d ingest errors (see build_report.json)", len(errors))


if __name__ == "__main__":
    main()
