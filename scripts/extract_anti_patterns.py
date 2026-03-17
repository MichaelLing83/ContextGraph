"""Extract anti-patterns from failed trajectories in Neo4j using LLM.

Reads failed Trajectory nodes from Neo4j, calls the LLM to extract
anti-patterns (what to AVOID), embeds them, and stores them back in Neo4j
as Strategy nodes with category='anti_pattern'.

Supports resuming from a progress file.

Usage:
    python scripts/extract_anti_patterns.py [--dry-run] [--limit N] [--reset]
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
from agent_memory.embeddings import get_embedding_client
from agent_memory.models import Strategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")
if not NEO4J_PASSWORD:
    logger.error("NEO4J_PASSWORD environment variable is required (set it in .env)")
    sys.exit(1)
NEO4J_AUTH = (NEO4J_USER, NEO4J_PASSWORD)
PROGRESS_PATH = project_root / "results" / "live_experiment" / "anti_pattern_extraction_progress.json"
STATS_PATH = project_root / "results" / "live_experiment" / "anti_pattern_extraction_stats.json"

# Rate limiting
DELAY_BETWEEN_CALLS = 0.5  # seconds

# Prompt template for anti-pattern extraction
_ANTI_PATTERN_PROMPT = """You are analyzing a FAILED coding agent trajectory to extract anti-patterns — things to AVOID.

Repository: {repo}
Task: {problem_statement}
Outcome: FAILED after {n_steps} steps
Key errors encountered: {error_types}
Key actions taken: {action_summary}
Detected loops (repeated actions): {loop_info}

This trajectory failed. Analyze what went wrong and extract 3-5 concise anti-patterns.
Each anti-pattern should describe:
- A specific mistake or bad strategy that an agent should AVOID
- Be abstract (not tied to specific file names or variables)
- Be actionable (tells an agent what NOT to do)
- One sentence

Format each on its own line as: [anti_pattern] Rule text

Anti-patterns:"""


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


def fetch_failed_trajectories(store: Neo4jStore) -> list:
    """Fetch only failed trajectory IDs and metadata from Neo4j."""
    query = """
    MATCH (t:Trajectory)
    WHERE t.success = false
    RETURN t.id AS id, t.repo AS repo, t.summary AS summary,
           t.success AS success, t.total_steps AS total_steps,
           t.instance_id AS instance_id
    ORDER BY t.id
    """
    return store.execute_query(query)


def fetch_trajectory_details(store: Neo4jStore, traj_id: str) -> dict:
    """Fetch trajectory fragments, error patterns, and loop info for LLM input."""
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

    # Get loop fragments (repeated action patterns)
    loop_query = """
    MATCH (t:Trajectory {id: $traj_id})-[:HAS_FRAGMENT]->(f:Fragment)
    WHERE f.fragment_type = 'loop'
    RETURN f.description AS description, f.action_sequence AS actions
    """
    loops = store.execute_query(loop_query, {"traj_id": traj_id})

    return {
        "fragments": fragments,
        "error_types": error_types,
        "loops": loops,
    }


def build_prompt(traj_data: dict) -> str:
    """Build LLM prompt from trajectory data."""
    repo = traj_data.get("repo", "unknown")
    problem = traj_data.get("problem_statement", "")[:300]
    n_steps = traj_data.get("total_steps", 0)
    error_types = ", ".join(traj_data.get("error_types", [])) or "none"

    # Build action summary from fragments
    action_summary = ""
    if traj_data.get("fragments"):
        parts = []
        for frag in traj_data["fragments"][:5]:
            desc = frag.get("description", "")
            if desc:
                parts.append(desc[:100])
        action_summary = "; ".join(parts)
    action_summary = action_summary[:500] or "no details available"

    # Build loop info
    loop_info = "none detected"
    if traj_data.get("loops"):
        loop_parts = []
        for loop in traj_data["loops"][:3]:
            desc = loop.get("description", "")
            if desc:
                loop_parts.append(desc[:150])
        if loop_parts:
            loop_info = "; ".join(loop_parts)

    return _ANTI_PATTERN_PROMPT.format(
        repo=repo,
        problem_statement=problem or "no description available",
        n_steps=n_steps,
        error_types=error_types,
        action_summary=action_summary,
        loop_info=loop_info,
    )


import re
import uuid


def parse_response(content: str, traj_data: dict) -> list[Strategy]:
    """Parse LLM output into Strategy objects with category='anti_pattern'."""
    strategies = []
    trajectory_id = traj_data.get("trajectory_id", "")
    repo = traj_data.get("repo", "")

    for line in content.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        # Strip leading bullet/number markers
        line = re.sub(r"^[\d]+[.)]\s*", "", line)
        line = re.sub(r"^[-*]\s*", "", line)

        # Match [anti_pattern] Rule text or [category] Rule text
        match = re.match(r"\[(\w+)\]\s+(.+)", line)
        if not match:
            continue

        rule_text = match.group(2).strip()

        # Skip very short rules
        if len(rule_text) < 15:
            continue

        strategy_id = f"strat_{uuid.uuid4().hex[:12]}"
        strategies.append(Strategy(
            id=strategy_id,
            rule_text=rule_text,
            category="anti_pattern",
            source_trajectory_id=trajectory_id,
            source_repo=repo,
            confidence=0.5,  # Lower default confidence for anti-patterns from failed runs
        ))

    return strategies


def call_llm(client, model: str, prompt: str) -> str:
    """Call LLM and return content."""
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        temperature=0.3,
    )
    return response.choices[0].message.content or ""


def main():
    parser = argparse.ArgumentParser(description="Extract anti-patterns from failed trajectories")
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

    # Initialize indexes (idempotent)
    embedder = None
    if embedding_api_key:
        embedder = get_embedding_client("openai", api_key=embedding_api_key, base_url=api_base)
        store.init_schema(vector_dimensions=embedder.dimensions)
    else:
        logger.warning("No OPENAI_API_KEY — anti-patterns will not be embedded")
        store.init_schema()

    # Load progress
    progress = {} if args.reset else load_progress()
    if args.reset and PROGRESS_PATH.exists():
        PROGRESS_PATH.unlink()
        logger.info("Reset progress file")

    # Initialize LLM client
    client = None
    model = "minimax-m2.5"
    if not args.dry_run:
        from openai import OpenAI
        client = OpenAI(base_url=api_base, api_key=api_key)

    # Fetch failed trajectories
    trajectories = fetch_failed_trajectories(store)
    logger.info("Found %d failed trajectories in Neo4j", len(trajectories))

    if args.limit:
        trajectories = trajectories[:args.limit]
        logger.info("Limited to %d trajectories", len(trajectories))

    # Process trajectories
    total_anti_patterns = 0
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
            # Fetch details (including loops)
            details = fetch_trajectory_details(store, traj_id)

            # Build trajectory data
            traj_data = {
                "trajectory_id": traj_id,
                "repo": traj.get("repo", ""),
                "problem_statement": traj.get("summary", ""),
                "success": False,
                "total_steps": traj.get("total_steps", 0),
                "error_types": details["error_types"],
                "fragments": details["fragments"],
                "loops": details["loops"],
            }

            prompt = build_prompt(traj_data)

            if args.dry_run:
                logger.info("--- Trajectory %s (%s) ---\n%s\n", traj_id, traj.get("repo"), prompt)
                processed += 1
                continue

            # Call LLM
            content = call_llm(client, model, prompt)

            # Parse anti-patterns
            anti_patterns = parse_response(content, traj_data)

            # Embed and store each anti-pattern
            for ap in anti_patterns:
                if embedder:
                    ap.embedding = embedder.embed(ap.rule_text)

                store.create_strategy(ap)
                store.link_strategy_to_trajectory(ap.id, traj_id)
                total_anti_patterns += 1

            # Mark as done
            progress[traj_id] = {
                "n_anti_patterns": len(anti_patterns),
                "timestamp": time.time(),
            }
            save_progress(progress)
            processed += 1

            if processed % 50 == 0:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                logger.info(
                    "Progress: %d/%d processed (%d skipped), %d anti-patterns, %.1f traj/sec",
                    processed, len(trajectories) - skipped, skipped, total_anti_patterns, rate,
                )

            # Rate limiting
            time.sleep(DELAY_BETWEEN_CALLS)

        except Exception as e:
            error_msg = f"Failed trajectory {traj_id}: {e}"
            logger.warning(error_msg)
            errors.append(error_msg)

            # Exponential backoff on rate limit errors
            if "429" in str(e) or "rate" in str(e).lower():
                wait = min(60, 2 ** len([x for x in errors[-5:] if "429" in str(x)]))
                logger.info("Rate limited, waiting %ds...", wait)
                time.sleep(wait)

    elapsed = time.time() - start_time

    # Save stats
    stats = {
        "trajectories_total": len(trajectories),
        "trajectories_processed": processed,
        "trajectories_skipped": skipped,
        "anti_patterns_created": total_anti_patterns,
        "errors": errors[:50],
        "elapsed_seconds": round(elapsed, 1),
    }

    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2)

    logger.info(
        "Done: %d processed, %d skipped, %d anti-patterns created, %d errors, %.1fs",
        processed, skipped, total_anti_patterns, len(errors), elapsed,
    )
    logger.info("Stats saved to %s", STATS_PATH)

    store.close()


if __name__ == "__main__":
    main()
