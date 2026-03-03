"""Extract strategies from trajectories in Neo4j using LLM.

Reads all Trajectory nodes from Neo4j, calls the LLM to extract
reusable strategies, embeds them, and stores them back in Neo4j
as Strategy nodes linked to their source trajectories.

Supports resuming from a progress file.

Usage:
    python scripts/extract_strategies.py [--dry-run] [--limit N]
"""

import json
import os
import sys
import time
import argparse
import logging
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

from agent_memory.neo4j_store import Neo4jStore
from agent_memory.strategy_extractor import StrategyExtractor
from agent_memory.embeddings import get_embedding_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "contextgraph123")
NEO4J_AUTH = (NEO4J_USER, NEO4J_PASSWORD)
PROGRESS_PATH = project_root / "results" / "live_experiment" / "strategy_extraction_progress.json"
STATS_PATH = project_root / "results" / "live_experiment" / "strategy_extraction_stats.json"

# Rate limiting
DELAY_BETWEEN_CALLS = 0.5  # seconds


def load_progress() -> dict:
    """Load progress file for resume support."""
    if PROGRESS_PATH.exists():
        with open(PROGRESS_PATH) as f:
            return json.load(f)
    return {}


def save_progress(progress: dict) -> None:
    """Save progress file."""
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_PATH, "w") as f:
        json.dump(progress, f)


def fetch_all_trajectories(store: Neo4jStore) -> list:
    """Fetch all trajectory IDs and metadata from Neo4j."""
    query = """
    MATCH (t:Trajectory)
    RETURN t.id AS id, t.repo AS repo, t.summary AS summary,
           t.success AS success, t.total_steps AS total_steps,
           t.instance_id AS instance_id
    ORDER BY t.id
    """
    return store.execute_query(query)


def fetch_trajectory_details(store: Neo4jStore, traj_id: str) -> dict:
    """Fetch trajectory fragments and error patterns for LLM input."""
    # Get fragments
    frag_query = """
    MATCH (t:Trajectory {id: $traj_id})-[:HAS_FRAGMENT]->(f:Fragment)
    RETURN f.description AS description, f.fragment_type AS type,
           f.action_sequence AS actions, f.outcome AS outcome
    ORDER BY f.step_range[0]
    """
    fragments = store.execute_query(frag_query, {"traj_id": traj_id})

    # Get error types
    error_query = """
    MATCH (t:Trajectory {id: $traj_id})-[:HAS_FRAGMENT]->(f:Fragment)-[:CAUSED_ERROR]->(e:ErrorPattern)
    RETURN DISTINCT e.error_type AS error_type
    """
    errors = store.execute_query(error_query, {"traj_id": traj_id})
    error_types = [e["error_type"] for e in errors if e.get("error_type")]

    return {
        "fragments": fragments,
        "error_types": error_types,
    }


def main():
    parser = argparse.ArgumentParser(description="Extract strategies from trajectories")
    parser.add_argument("--dry-run", action="store_true", help="Print prompts without calling LLM")
    parser.add_argument("--limit", type=int, default=0, help="Process only N trajectories (0=all)")
    parser.add_argument("--reset", action="store_true", help="Reset progress and start over")
    args = parser.parse_args()

    # Load API config (OpenAI-compatible proxy)
    api_key = os.environ.get("OPENAI_API_KEY")
    api_base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
    if not api_key and not args.dry_run:
        logger.error("OPENAI_API_KEY not set in environment")
        sys.exit(1)

    embedding_api_key = api_key

    # Connect to Neo4j
    store = Neo4jStore(uri=NEO4J_URI, auth=NEO4J_AUTH)
    if not store.verify_connectivity():
        logger.error("Cannot connect to Neo4j at %s", NEO4J_URI)
        sys.exit(1)

    # Initialize strategy indexes (idempotent)
    embedder = None
    if embedding_api_key:
        embedder = get_embedding_client("openai", api_key=embedding_api_key)
        store.init_schema(vector_dimensions=embedder.dimensions)
    else:
        logger.warning("No OPENAI_API_KEY — strategies will not be embedded")
        store.init_schema()

    # Load progress
    progress = {} if args.reset else load_progress()
    if args.reset and PROGRESS_PATH.exists():
        PROGRESS_PATH.unlink()
        logger.info("Reset progress file")

    # Initialize extractor (also for dry-run, to use build_prompt)
    extractor = StrategyExtractor(
        api_base=api_base,
        api_key=api_key or "dry-run-placeholder",
        model="claude-sonnet-4-20250514",
    )

    # Fetch all trajectories
    trajectories = fetch_all_trajectories(store)
    logger.info("Found %d trajectories in Neo4j", len(trajectories))

    if args.limit:
        trajectories = trajectories[:args.limit]
        logger.info("Limited to %d trajectories", len(trajectories))

    # Process trajectories
    total_strategies = 0
    processed = 0
    skipped = 0
    errors = []
    start_time = time.time()

    for i, traj in enumerate(trajectories):
        traj_id = traj["id"]

        # Skip already processed
        if traj_id in progress:
            skipped += 1
            continue

        try:
            # Fetch details
            details = fetch_trajectory_details(store, traj_id)

            # Build trajectory data for extractor
            traj_data = {
                "trajectory_id": traj_id,
                "repo": traj.get("repo", ""),
                "problem_statement": traj.get("summary", ""),
                "success": traj.get("success", False),
                "total_steps": traj.get("total_steps", 0),
                "error_types": details["error_types"],
                "fragments": details["fragments"],
            }

            if args.dry_run:
                prompt = extractor.build_prompt(traj_data)
                logger.info("--- Trajectory %s (%s) ---\n%s\n", traj_id, traj.get("repo"), prompt)
                processed += 1
                continue

            # Extract strategies via LLM
            strategies = extractor.extract(traj_data)

            # Embed and store each strategy
            for strat in strategies:
                if embedder:
                    strat.embedding = embedder.embed(strat.rule_text)

                store.create_strategy(strat)
                store.link_strategy_to_trajectory(strat.id, traj_id)
                total_strategies += 1

            # Mark as done
            progress[traj_id] = {
                "n_strategies": len(strategies),
                "timestamp": time.time(),
            }
            save_progress(progress)
            processed += 1

            if (processed) % 50 == 0:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                logger.info(
                    "Progress: %d/%d processed (%d skipped), %d strategies, %.1f traj/sec",
                    processed, len(trajectories) - skipped, skipped, total_strategies, rate,
                )

            # Rate limiting
            time.sleep(DELAY_BETWEEN_CALLS)

        except Exception as e:
            error_msg = f"Failed trajectory {traj_id}: {e}"
            logger.warning(error_msg)
            errors.append(error_msg)

            # Exponential backoff on rate limit errors
            if "429" in str(e) or "rate" in str(e).lower():
                wait = min(60, 2 ** len([e for e in errors[-5:] if "429" in str(e)]))
                logger.info("Rate limited, waiting %ds...", wait)
                time.sleep(wait)

    elapsed = time.time() - start_time

    # Save stats
    stats = {
        "trajectories_total": len(trajectories),
        "trajectories_processed": processed,
        "trajectories_skipped": skipped,
        "strategies_created": total_strategies,
        "errors": errors[:50],
        "elapsed_seconds": round(elapsed, 1),
    }

    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2)

    logger.info(
        "Done: %d processed, %d skipped, %d strategies created, %d errors, %.1fs",
        processed, skipped, total_strategies, len(errors), elapsed,
    )
    logger.info("Stats saved to %s", STATS_PATH)

    store.close()


if __name__ == "__main__":
    main()
