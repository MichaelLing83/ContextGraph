"""Build context graph from training trajectories.

The trajectory files use a role-based chat format (role/text fields)
instead of the SWE-agent .traj format (action/observation/thought).
This script adapts the parsing accordingly.
"""

import json
import os
import re
import sys
import time
import logging
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent_memory import AgentMemory
from agent_memory.writer import RawTrajectory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SPLIT_PATH = project_root / "results" / "live_experiment" / "split.json"
STATS_PATH = project_root / "results" / "live_experiment" / "graph_build_stats.json"

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")
if not NEO4J_PASSWORD:
    logger.error("NEO4J_PASSWORD environment variable is required (set it in .env)")
    sys.exit(1)
NEO4J_AUTH = (NEO4J_USER, NEO4J_PASSWORD)


def parse_chat_trajectory(path: Path) -> RawTrajectory:
    """Parse a role-based chat trajectory file into RawTrajectory format.

    The files have top-level keys: instance_id, model_name, target, trajectory,
    exit_status, generated_patch, eval_logs.

    Each trajectory entry has: role (system/user/ai), text, cutoff_date, mask, system_prompt.

    AI messages contain thoughts/actions; user messages contain observations.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    instance_id = data.get("instance_id", path.stem)
    exit_status = data.get("exit_status", "")
    success = exit_status == "submitted"

    # Extract repo from instance_id
    repo = _extract_repo(instance_id)

    # Extract problem statement from first user message
    problem_statement = ""
    trajectory = data.get("trajectory", [])
    for entry in trajectory:
        if entry.get("role") == "user" and entry.get("text"):
            text = entry["text"]
            # The first user message contains the issue text
            issue_match = re.search(r"ISSUE:\n(.*?)(?:\n\nINSTRUCTIONS:|\Z)", text, re.DOTALL)
            if issue_match:
                problem_statement = issue_match.group(1).strip()[:500]
                break
            # Fallback: use first 500 chars
            if not problem_statement:
                problem_statement = text[:500]
                break

    # Convert role-based chat to steps
    steps = []
    for i, entry in enumerate(trajectory):
        role = entry.get("role", "")
        text = entry.get("text") or ""

        if role == "ai":
            # AI message: extract thought and action
            # Format: "DISCUSSION\n...\n```\ncommand\n```"
            thought = text
            action = ""
            code_match = re.search(r"```\n?(.*?)\n?```", text, re.DOTALL)
            if code_match:
                action = code_match.group(1).strip()
                thought = text[:code_match.start()].strip()

            # Find the next user message as observation
            observation = ""
            if i + 1 < len(trajectory) and trajectory[i + 1].get("role") == "user":
                observation = (trajectory[i + 1].get("text") or "")[:2000]

            steps.append({
                "action": action,
                "observation": observation,
                "thought": thought,
            })

    return RawTrajectory(
        instance_id=instance_id,
        repo=repo,
        success=success,
        steps=steps,
        problem_statement=problem_statement,
    )


def _extract_repo(instance_id: str) -> str:
    """Extract repository name from SWE-bench instance ID."""
    parts = instance_id.split("__")
    if len(parts) >= 2:
        owner = parts[0]
        repo = re.sub(r"-\d+$", "", parts[1])
        return f"{owner}/{repo}"
    return "unknown/unknown"


def main():
    # Load split
    with open(SPLIT_PATH) as f:
        split_data = json.load(f)

    train_files = [Path(p) for p in split_data["train_files"]]
    logger.info(f"Loading {len(train_files)} training trajectories")

    # Connect to Neo4j
    memory = AgentMemory(
        neo4j_uri=NEO4J_URI,
        neo4j_auth=NEO4J_AUTH,
        consolidate_every=50,  # Consolidate less frequently for bulk loading
    )

    # Build graph
    loaded = 0
    fragments = 0
    errors = []
    start_time = time.time()

    for i, path in enumerate(train_files):
        try:
            raw = parse_chat_trajectory(path)
            memory.learn(raw)
            loaded += 1
            fragments += max(1, len(raw.steps) // 3)

            if (i + 1) % 100 == 0:
                elapsed = time.time() - start_time
                rate = (i + 1) / elapsed
                logger.info(f"Progress: {i + 1}/{len(train_files)} ({rate:.1f} files/sec)")

        except Exception as e:
            error_msg = f"Failed to load {path.name}: {e}"
            logger.warning(error_msg)
            errors.append(error_msg)

    # Final consolidation
    try:
        logger.info("Running final consolidation...")
        memory.consolidator.consolidate()
    except Exception as e:
        errors.append(f"Final consolidation failed: {e}")

    elapsed = time.time() - start_time

    # Get stats from Neo4j
    stats = memory.get_stats()

    build_stats = {
        "trajectories_loaded": loaded,
        "trajectories_failed": len(errors),
        "fragments_created_estimate": fragments,
        "neo4j_trajectory_count": stats.total_trajectories,
        "neo4j_methodology_count": stats.total_methodologies,
        "errors": errors[:50],  # Cap at 50
        "elapsed_seconds": round(elapsed, 1),
        "files_per_second": round(loaded / elapsed, 2) if elapsed > 0 else 0,
    }

    with open(STATS_PATH, "w") as f:
        json.dump(build_stats, f, indent=2)

    logger.info(f"Build complete: {loaded} loaded, {len(errors)} errors, {elapsed:.1f}s")
    logger.info(f"Stats saved to {STATS_PATH}")

    memory.close()


if __name__ == "__main__":
    main()
