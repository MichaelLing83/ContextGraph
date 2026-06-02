"""Build a context graph as Obsidian notes (no Neo4j).

Separate source and graph vaults:

    uv run python scripts/build_obsidian_graph.py \\
        --source-vault ~/Notes \\
        --graph-vault ~/NotesGraph

Usage:
    uv run python scripts/build_obsidian_graph.py --source-vault ~/Notes --graph-vault ~/NotesGraph
    uv run python scripts/build_obsidian_graph.py --source-vault ~/Notes --graph-vault ~/NotesGraph --link-mode stub
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent_memory.vault import iter_vault_notes
from agent_memory.vault.models import RawVaultNote
from agent_memory.vault.graph_stats import (
    compute_graph_vault_stats,
    format_graph_stats,
)
from agent_memory.vault.fragment_summary import (
    DEFAULT_MODEL,
    FragmentSummarizer,
    FragmentSummaryCache,
    cache_path_for_model,
)
from agent_memory.vault.obsidian_graph import ObsidianGraphBuilder
from agent_memory.vault.segmenter import segment_note

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
# Keep third-party HTTP client chatter out of progress output.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _clear_generated_graph_outputs(graph_root: Path) -> None:
    """Remove previously generated graph artifacts while keeping summary cache."""
    if not graph_root.exists():
        return
    targets = [
        graph_root / "Fragments",
        graph_root / "Sources",
        graph_root / "MOC.md",
        graph_root / ".graph_registry.json",
        graph_root / "build_report.json",
    ]
    removed = 0
    for path in targets:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
            removed += 1
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    if removed:
        logger.info(
            "Cleared %d previous generated graph outputs under %s",
            removed,
            graph_root,
        )


def _count_note_fragments(note: RawVaultNote, builder: ObsidianGraphBuilder) -> int:
    sections = segment_note(
        note,
        mode=builder.chunk_mode,
        target_chars=builder.fragment_chars,
        max_fragment_chars=builder.fragment_max_chars,
        preserve_markup=builder.preserve_markup,
    )
    if not sections and note.body.strip():
        return 1
    return len(sections)


def _format_progress(done: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return f"{done}/?"
    ratio = min(1.0, max(0.0, done / total))
    filled = int(width * ratio)
    bar = "#" * filled + "." * (width - filled)
    return f"[{bar}] {done}/{total} ({ratio * 100:5.1f}%)"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Obsidian-native context graph (wikilinks + tags)"
    )
    parser.add_argument(
        "--source-vault",
        type=Path,
        required=True,
        help="Vault to read original markdown from",
    )
    parser.add_argument(
        "--graph-vault",
        type=Path,
        required=True,
        help="Separate vault to write the knowledge graph into",
    )
    parser.add_argument(
        "--graph-dir",
        default="",
        help="Optional subfolder inside graph vault (default: vault root)",
    )
    parser.add_argument(
        "--link-mode",
        choices=("frontmatter", "stub", "symlink"),
        default="frontmatter",
        help=(
            "How fragments reference the source vault (default: frontmatter). "
            "frontmatter: YAML paths only — no Sources/ notes or source wikilinks "
            "(Graph view: fragment nodes only). "
            "stub: create Sources/*.md index cards with wikilinks. "
            "symlink: Sources/ symlink to source vault with wikilinks."
        ),
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
    parser.add_argument(
        "--llm-summary",
        action="store_true",
        help=(
            "Generate cg_llm_summary in fragment frontmatter via LLM "
            "(cached by body hash in .llm_summary_cache/<model>.json)"
        ),
    )
    parser.add_argument(
        "--llm-summary-model",
        default=DEFAULT_MODEL,
        help=f"LLM model for --llm-summary (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--llm-api-base",
        default=os.environ.get("OPENAI_API_BASE", "http://localhost:4000/v1"),
        help="OpenAI-compatible API base URL for --llm-summary",
    )
    parser.add_argument(
        "--llm-api-key",
        default=os.environ.get("LITELLM_MASTER_KEY", os.environ.get("OPENAI_API_KEY", "")),
        help="API key for --llm-summary (LITELLM_MASTER_KEY or OPENAI_API_KEY)",
    )
    args = parser.parse_args()

    source_vault = args.source_vault.expanduser().resolve()
    graph_vault = args.graph_vault.expanduser().resolve()
    graph_dir = args.graph_dir

    if not source_vault.is_dir():
        logger.error("Source vault not found: %s", source_vault)
        sys.exit(1)

    graph_vault.mkdir(parents=True, exist_ok=True)

    max_notes = args.max_notes if args.max_notes > 0 else None

    if args.llm_summary and not args.llm_api_key:
        logger.error(
            "Missing API key for --llm-summary "
            "(set LITELLM_MASTER_KEY or OPENAI_API_KEY, or pass --llm-api-key)"
        )
        sys.exit(1)

    builder = ObsidianGraphBuilder(
        graph_vault,
        source_vault=source_vault,
        graph_dir=graph_dir,
        link_mode=args.link_mode,
        chunk_mode=args.chunk_mode,
        fragment_chars=args.fragment_chars,
        fragment_max_chars=args.fragment_max_chars,
        related_mode=args.related_mode,
        related_topk=args.related_topk,
    )
    _clear_generated_graph_outputs(builder.graph_root)

    builder.load_registry()
    builder.setup_cross_vault_links()

    notes = list(iter_vault_notes(source_vault, glob=args.glob, max_notes=max_notes))
    estimated_fragments = 0
    if args.llm_summary:
        estimated_fragments = sum(_count_note_fragments(n, builder) for n in notes)
        logger.info(
            "LLM summary enabled: %d notes, %d fragments estimated (model=%s)",
            len(notes),
            estimated_fragments,
            args.llm_summary_model,
        )
        logger.info(
            "Summary cache: %s",
            cache_path_for_model(builder.graph_root, args.llm_summary_model),
        )
        summary_cache_path = cache_path_for_model(
            builder.graph_root,
            args.llm_summary_model,
        )
        summary_cache = FragmentSummaryCache(summary_cache_path)
        summary_cache.load()

    fragment_bar = None
    if args.llm_summary and estimated_fragments > 0:
        try:
            from tqdm import tqdm

            fragment_bar = tqdm(
                total=estimated_fragments,
                desc="LLM summary",
                unit="frag",
                file=sys.stderr,
                dynamic_ncols=True,
            )
        except ImportError:
            logger.warning(
                "tqdm not installed; using log progress instead "
                "(uv pip install tqdm)"
            )

    def _on_summary_progress(stats) -> None:
        if fragment_bar is not None:
            fragment_bar.update(1)
            fragment_bar.set_postfix(
                llm=stats.llm_calls,
                cache=stats.cache_hits,
                fail=stats.failures,
                refresh=False,
            )
            return
        done = stats.cache_hits + stats.llm_calls + stats.skipped_empty + stats.failures
        if done == estimated_fragments or done % 25 == 0:
            logger.info(
                "LLM summary progress %s | calls=%d cache_hits=%d failures=%d",
                _format_progress(done, estimated_fragments),
                stats.llm_calls,
                stats.cache_hits,
                stats.failures,
            )

    if args.llm_summary:
        builder.summarizer = FragmentSummarizer(
            api_base=args.llm_api_base,
            api_key=args.llm_api_key,
            model=args.llm_summary_model,
            cache=summary_cache,
            on_progress=_on_summary_progress,
        )

    ingested = 0
    errors: list[str] = []
    start = time.time()

    for note in notes:
        try:
            builder.ingest_note(note)
            ingested += 1
        except Exception as e:
            errors.append(f"{note.rel_path}: {e}")
            logger.warning("Failed %s: %s", note.rel_path, e)

    if fragment_bar is not None:
        fragment_bar.close()

    semantic_notes = 0
    if args.llm_summary:
        semantic_notes = builder.build_semantic_links_from_summaries()
        logger.info(
            "Semantic links from cg_llm_summary written for %d fragment notes",
            semantic_notes,
        )

    moc = builder.write_moc()
    builder.save_registry()
    if args.llm_summary and builder.summarizer is not None:
        builder.summarizer.cache.save()

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
        "preserve_markup": True,
        "related_mode": args.related_mode,
        "related_topk": args.related_topk,
        "llm_summary": args.llm_summary,
        "semantic_links_from_summary_notes": semantic_notes,
        "notes_ingested": ingested,
        "errors": errors[:20],
        "moc": str(moc.relative_to(graph_vault)),
        "elapsed_sec": round(time.time() - start, 2),
        "graph_stats": graph_stats.to_dict(),
    }
    if args.llm_summary and builder.summarizer is not None:
        report["llm_summary_stats"] = builder.summarizer.stats.to_dict()
        report["llm_summary_cache"] = str(
            cache_path_for_model(builder.graph_root, args.llm_summary_model).relative_to(
                graph_vault
            )
        )
    out = builder.graph_root / "build_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Build finished in %.1fs — %d source notes ingested", report["elapsed_sec"], ingested)
    if args.llm_summary and builder.summarizer is not None:
        s = builder.summarizer.stats
        logger.info(
            "LLM summary done: calls=%d cache_hits=%d skipped_empty=%d failures=%d",
            s.llm_calls,
            s.cache_hits,
            s.skipped_empty,
            s.failures,
        )
    print(format_graph_stats(graph_stats))
    if errors:
        logger.warning("%d ingest errors (see build_report.json)", len(errors))


if __name__ == "__main__":
    main()
