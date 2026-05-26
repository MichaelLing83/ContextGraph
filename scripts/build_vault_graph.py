"""Build a context graph from an Obsidian (markdown) vault.

Text-only ingestion: each .md file -> Trajectory (task_type=note) + section Fragments.

Usage:
    uv run python scripts/build_vault_graph.py --vault /path/to/vault
    uv run python scripts/build_vault_graph.py --vault ~/Notes --max-notes 50 --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

from agent_memory import AgentMemory
from agent_memory.vault import iter_vault_notes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build context graph from Obsidian vault")
    parser.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="Root path of the Obsidian vault",
    )
    parser.add_argument("--max-notes", type=int, default=0, help="Limit notes (0=all)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no Neo4j writes")
    parser.add_argument(
        "--stats-out",
        type=Path,
        default=project_root / "results" / "vault_build_stats.json",
    )
    args = parser.parse_args()

    vault_root = args.vault.expanduser().resolve()
    if not vault_root.is_dir():
        logger.error("Vault path does not exist: %s", vault_root)
        sys.exit(1)

    max_notes = args.max_notes if args.max_notes > 0 else None
    notes = list(iter_vault_notes(vault_root, max_notes=max_notes))
    logger.info("Found %d markdown notes under %s", len(notes), vault_root)

    if args.dry_run:
        total_chars = sum(n.char_count for n in notes)
        logger.info("Dry run: %d notes, %d total chars", len(notes), total_chars)
        for n in notes[:5]:
            logger.info("  - %s (%s chars)", n.rel_path, n.char_count)
        return

    if not NEO4J_PASSWORD:
        logger.error("NEO4J_PASSWORD is required in .env")
        sys.exit(1)

    memory = AgentMemory(
        neo4j_uri=NEO4J_URI,
        neo4j_auth=(NEO4J_USER, NEO4J_PASSWORD),
        consolidate_every=50,
    )

    loaded = 0
    errors: list[str] = []
    start = time.time()

    for i, note in enumerate(notes):
        try:
            memory.learn_note(note)
            loaded += 1
            if (i + 1) % 50 == 0:
                elapsed = time.time() - start
                logger.info("Progress: %d/%d (%.1f notes/sec)", i + 1, len(notes), (i + 1) / elapsed)
        except Exception as e:
            msg = f"{note.rel_path}: {e}"
            logger.warning(msg)
            errors.append(msg)

    try:
        memory.consolidator.consolidate()
        memory.community_detector.refresh_communities()
    except Exception as e:
        errors.append(f"Final consolidation: {e}")

    stats = memory.get_stats()
    elapsed = time.time() - start
    report = {
        "vault": str(vault_root),
        "notes_loaded": loaded,
        "notes_failed": len(errors),
        "elapsed_sec": round(elapsed, 2),
        "neo4j_trajectory_count": stats.total_trajectories,
        "errors": errors[:30],
    }

    args.stats_out.parent.mkdir(parents=True, exist_ok=True)
    args.stats_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Done: %s", report)


if __name__ == "__main__":
    main()
