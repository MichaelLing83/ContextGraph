"""Parse SWE-agent trajectory files into RawTrajectory format."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import json
import re

from agent_memory.writer import RawTrajectory


@dataclass
class SWEAgentStep:
    """Parsed step from SWE-agent trajectory."""

    thought: str
    action: str
    observation: str
    state: Optional[str] = None


def parse_swe_agent_trajectory(path: Path) -> RawTrajectory:
    """
    Parse a SWE-agent trajectory file into RawTrajectory format.

    Args:
        path: Path to .traj file

    Returns:
        RawTrajectory ready for ingestion into Agent Memory
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Extract instance_id from filename (e.g., "django__django-12345.traj")
    instance_id = path.stem

    # Extract repo from instance_id (e.g., "django__django-12345" -> "django/django")
    repo = _extract_repo(instance_id)

    # Check success from exit_status
    info = data.get("info", {})
    exit_status = data.get("exit_status") or info.get("exit_status", "")
    success = exit_status == "submitted"

    # Parse trajectory steps
    trajectory = data.get("trajectory", [])
    steps = []
    for step in trajectory:
        steps.append({
            "action": step.get("action", ""),
            "observation": step.get("observation", ""),
            "thought": step.get("thought", ""),
        })

    # Extract problem statement if available
    problem_statement = data.get("problem_statement", "")

    return RawTrajectory(
        instance_id=instance_id,
        repo=repo,
        success=success,
        steps=steps,
        problem_statement=problem_statement,
    )


def _extract_repo(instance_id: str) -> str:
    """
    Extract repository name from SWE-bench instance ID.

    Examples:
        "django__django-12345" -> "django/django"
        "scikit-learn__scikit-learn-9876" -> "scikit-learn/scikit-learn"
        "psf__requests-5432" -> "psf/requests"
    """
    # Pattern: owner__repo-issue_number
    match = re.match(r'^([^_]+(?:__[^_]+)?)__([^-]+(?:-[^-]+)*)-\d+$', instance_id)
    if match:
        owner_part = match.group(1)
        repo_part = match.group(2)

        # Handle double-underscore in owner (e.g., scikit-learn)
        owner = owner_part.replace("__", "-") if "__" in owner_part else owner_part

        return f"{owner}/{repo_part}"

    # Fallback: split on double underscore
    parts = instance_id.split("__")
    if len(parts) >= 2:
        owner = parts[0]
        # Remove issue number from repo
        repo = re.sub(r'-\d+$', '', parts[1])
        return f"{owner}/{repo}"

    return "unknown/unknown"
