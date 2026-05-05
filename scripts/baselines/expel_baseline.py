#!/usr/bin/env python3
"""ExpeL-style rule extraction baseline for SWE-bench.

Implements ExpeL's insight extraction mechanism adapted for software engineering:
1. Load trajectory pairs (success + failure for same/similar problems)
2. Compare success vs failure via LLM critique
3. Extract general rules via ADD/EDIT/REMOVE/AGREE operations
4. Maintain a flat list of max ~20 rules with confidence counters

This deliberately uses NO graph structure, NO PPR retrieval, NO multi-channel
fusion — just a flat rule list to compare against ContextGraph's rich retrieval.

Usage:
    uv run python scripts/baselines/expel_baseline.py extract
    uv run python scripts/baselines/expel_baseline.py extract --max-rules 20 --max-pairs 50
    uv run python scripts/baselines/expel_baseline.py show-rules --rules-file data/baselines/expel_rules.json
"""

import json
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import typer
from dotenv import load_dotenv

# Project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.baselines.expel_prompts import (
    SYSTEM_CRITIQUE_COMPARE,
    SYSTEM_CRITIQUE_ALL_SUCCESS,
    SYSTEM_CRITIQUE_ALL_FAIL,
    HUMAN_CRITIQUE_COMPARE,
    HUMAN_CRITIQUE_ALL_SUCCESS,
    HUMAN_CRITIQUE_ALL_FAIL,
    OPERATIONS_FORMAT,
    SUFFIX_FULL,
    SUFFIX_NOT_FULL,
    TRAJECTORY_SUMMARY_TEMPLATE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv(PROJECT_ROOT / ".env")

app = typer.Typer(help="ExpeL baseline: flat rule extraction from trajectories.")

# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------


@dataclass
class Rule:
    """A single ExpeL rule with confidence counter."""

    text: str
    count: int = 2  # ADD starts at 2 (ExpeL convention)

    def to_dict(self) -> dict:
        return {"text": self.text, "count": self.count}

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        return cls(text=d["text"], count=d["count"])


@dataclass
class TrajectoryPair:
    """A pair of success/failure trajectories for comparison."""

    task_description: str
    success_summary: str
    fail_summary: str
    instance_id: str = ""


@dataclass
class TrajectoryGroup:
    """A group of trajectories (all success or all failure) for batch critique."""

    summaries: List[str]
    task_description: str = ""


# --------------------------------------------------------------------------
# LLM Client
# --------------------------------------------------------------------------


class LLMClient:
    """OpenAI-compatible client for rule extraction via LiteLLM proxy."""

    def __init__(
        self,
        api_base: str = "http://localhost:4000/v1",
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
    ):
        from openai import OpenAI

        self.api_key = api_key or os.environ.get("LITELLM_MASTER_KEY", "sk-placeholder")
        self.client = OpenAI(base_url=api_base, api_key=self.api_key)
        self.model = model

    def chat(self, system: str, user: str, max_tokens: int = 2000) -> str:
        """Send a chat completion request."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                temperature=0.3,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"LLM call failed: {e}")
            return ""


# --------------------------------------------------------------------------
# Rule parsing and updating (reimplemented from ExpeL)
# --------------------------------------------------------------------------


def parse_operations(llm_output: str) -> List[Tuple[str, str]]:
    """Parse LLM output into (operation, text) tuples.

    Expected format lines:
        ADD 21: New rule text here.
        AGREE 3: Existing rule text.
        EDIT 5: Improved rule text.
        REMOVE 7: Rule to remove.
    """
    pattern = r"((?:REMOVE|EDIT|ADD|AGREE)(?: \d+|)): (?:[a-zA-Z\s\d]+: |)(.*)"
    matches = re.findall(pattern, llm_output)

    results = []
    banned_words = ["ADD", "AGREE", "EDIT"]
    for operation, text in matches:
        text = text.strip()
        # Skip empty, contains banned words (formatting issues), or doesn't end with period
        if not text:
            continue
        if any(w in text for w in banned_words):
            continue
        if not text.endswith("."):
            continue

        if "ADD" in operation:
            results.append(("ADD", text))
        else:
            results.append((operation.strip(), text))

    return results


def is_existing_rule(rules: List[Rule], text: str) -> bool:
    """Check if text matches (is contained in) any existing rule."""
    for rule in rules:
        if rule.text in text or text in rule.text:
            return True
    return False


def find_rule_index(rules: List[Rule], text: str) -> Optional[int]:
    """Find index of rule whose text is contained in the given text."""
    for i, rule in enumerate(rules):
        if rule.text in text:
            return i
    return None


def update_rules(
    rules: List[Rule],
    operations: List[Tuple[str, str]],
    list_full: bool = False,
) -> List[Rule]:
    """Apply ExpeL operations to update the rule list.

    Operations:
        ADD: append new rule with count=2
        AGREE: +1 to matching rule's counter
        EDIT: replace matching rule text, +1 to counter
        REMOVE: -1 to matching rule's counter (-3 if list is full)

    Rules with count <= 0 are removed. List is sorted by count descending.
    """
    # First pass: validate and fix operations
    valid_ops = []
    for operation, op_text in operations:
        op_type = operation.split(" ")[0]
        op_num = int(operation.split(" ")[1]) if " " in operation else None

        if op_type == "ADD":
            if is_existing_rule(rules, op_text):
                continue  # Skip duplicate adds
            valid_ops.append((op_type, op_num, op_text))

        elif op_type == "EDIT":
            if is_existing_rule(rules, op_text):
                # Text already exists -> treat as AGREE
                idx = find_rule_index(rules, op_text)
                if idx is not None:
                    valid_ops.append(("AGREE", idx, rules[idx].text))
            elif op_num is not None and op_num <= len(rules):
                valid_ops.append((op_type, op_num - 1, op_text))  # 1-indexed to 0-indexed
            # else: invalid reference, skip

        elif op_type == "REMOVE":
            idx = find_rule_index(rules, op_text)
            if idx is not None:
                valid_ops.append((op_type, idx, op_text))

        elif op_type == "AGREE":
            idx = find_rule_index(rules, op_text)
            if idx is not None:
                valid_ops.append((op_type, idx, op_text))

    # Second pass: apply operations in ExpeL order
    for op_type, op_idx, op_text in valid_ops:
        if op_type == "REMOVE":
            remove_strength = 3 if list_full else 1
            rules[op_idx] = Rule(text=rules[op_idx].text, count=rules[op_idx].count - remove_strength)
        elif op_type == "AGREE":
            rules[op_idx] = Rule(text=rules[op_idx].text, count=rules[op_idx].count + 1)
        elif op_type == "EDIT":
            rules[op_idx] = Rule(text=op_text, count=rules[op_idx].count + 1)
        elif op_type == "ADD":
            rules.append(Rule(text=op_text, count=2))

    # Remove rules with count <= 0
    rules = [r for r in rules if r.count > 0]

    # Sort by count descending
    rules.sort(key=lambda r: r.count, reverse=True)

    return rules


# --------------------------------------------------------------------------
# Trajectory loading and pairing
# --------------------------------------------------------------------------


def load_trajectories_from_split(split_path: Path) -> Tuple[List[dict], List[dict]]:
    """Load trajectory data from the split file, returning (successes, failures).

    Each trajectory dict has: instance_id, repo, success, problem_statement, steps.
    """
    with open(split_path) as f:
        split_data = json.load(f)

    train_files = [Path(p) for p in split_data["train_files"]]
    logger.info(f"Found {len(train_files)} training trajectory files")

    successes = []
    failures = []

    for path in train_files:
        if not path.exists():
            continue
        try:
            traj = _parse_trajectory_file(path)
            if traj["success"]:
                successes.append(traj)
            else:
                failures.append(traj)
        except Exception as e:
            logger.debug(f"Failed to parse {path.name}: {e}")

    logger.info(f"Loaded {len(successes)} successes, {len(failures)} failures")
    return successes, failures


def _parse_trajectory_file(path: Path) -> dict:
    """Parse a single trajectory file into a summary dict."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    instance_id = data.get("instance_id", path.stem)
    exit_status = data.get("exit_status", "")
    success = exit_status == "submitted"

    # Extract repo
    parts = instance_id.split("__")
    repo = f"{parts[0]}/{re.sub(r'-[0-9]+$', '', parts[1])}" if len(parts) >= 2 else "unknown"

    # Extract problem statement
    problem_statement = ""
    trajectory = data.get("trajectory", [])
    for entry in trajectory:
        if entry.get("role") == "user" and entry.get("text"):
            text = entry["text"]
            issue_match = re.search(r"ISSUE:\n(.*?)(?:\n\nINSTRUCTIONS:|\Z)", text, re.DOTALL)
            if issue_match:
                problem_statement = issue_match.group(1).strip()[:400]
                break
            if not problem_statement:
                problem_statement = text[:400]
                break

    # Summarize actions
    actions = []
    for entry in trajectory:
        if entry.get("role") == "ai" and entry.get("text"):
            text = entry["text"]
            # Extract action from code blocks
            code_match = re.search(r"```\n?(.*?)\n?```", text, re.DOTALL)
            if code_match:
                action = code_match.group(1).strip()
                if len(action) < 200:
                    actions.append(action)
                else:
                    actions.append(action[:100] + "...")

    # Limit summary length
    action_summary = "\n".join(f"  - {a}" for a in actions[:10])
    n_steps = len([e for e in trajectory if e.get("role") == "ai"])

    return {
        "instance_id": instance_id,
        "repo": repo,
        "success": success,
        "problem_statement": problem_statement,
        "action_summary": action_summary,
        "n_steps": n_steps,
    }


def summarize_trajectory(traj: dict) -> str:
    """Create a text summary of a trajectory for the critique prompt."""
    return TRAJECTORY_SUMMARY_TEMPLATE.format(
        repo=traj["repo"],
        problem_statement=traj["problem_statement"][:300],
        outcome="SUCCESS" if traj["success"] else "FAILURE",
        n_steps=traj["n_steps"],
        action_summary=traj["action_summary"][:600],
    )


def create_pairs(
    successes: List[dict],
    failures: List[dict],
    max_pairs: int = 50,
    seed: int = 42,
) -> List[TrajectoryPair]:
    """Create success/failure pairs for comparison critique.

    Strategy: pair successes and failures from the same repo when possible,
    otherwise pair randomly.
    """
    rng = random.Random(seed)

    # Group by repo
    success_by_repo: Dict[str, List[dict]] = {}
    failure_by_repo: Dict[str, List[dict]] = {}
    for s in successes:
        success_by_repo.setdefault(s["repo"], []).append(s)
    for f in failures:
        failure_by_repo.setdefault(f["repo"], []).append(f)

    pairs = []

    # Same-repo pairs first (strongest signal)
    common_repos = set(success_by_repo.keys()) & set(failure_by_repo.keys())
    for repo in sorted(common_repos):
        repo_successes = success_by_repo[repo]
        repo_failures = failure_by_repo[repo]
        for s in repo_successes:
            for f in repo_failures:
                pairs.append(TrajectoryPair(
                    task_description=f"Repository: {repo}",
                    success_summary=summarize_trajectory(s),
                    fail_summary=summarize_trajectory(f),
                    instance_id=f"{s['instance_id']} vs {f['instance_id']}",
                ))
                if len(pairs) >= max_pairs * 2:
                    break
            if len(pairs) >= max_pairs * 2:
                break
        if len(pairs) >= max_pairs * 2:
            break

    # If not enough same-repo pairs, add cross-repo pairs
    if len(pairs) < max_pairs:
        remaining = max_pairs - len(pairs)
        all_successes = list(successes)
        all_failures = list(failures)
        rng.shuffle(all_successes)
        rng.shuffle(all_failures)
        for s, f in zip(all_successes[:remaining], all_failures[:remaining]):
            pairs.append(TrajectoryPair(
                task_description=f"Success repo: {s['repo']}, Failure repo: {f['repo']}",
                success_summary=summarize_trajectory(s),
                fail_summary=summarize_trajectory(f),
                instance_id=f"{s['instance_id']} vs {f['instance_id']}",
            ))

    rng.shuffle(pairs)
    return pairs[:max_pairs]


def create_success_groups(
    successes: List[dict],
    group_size: int = 3,
    max_groups: int = 10,
    seed: int = 42,
) -> List[TrajectoryGroup]:
    """Create groups of successful trajectories for batch analysis."""
    rng = random.Random(seed)
    shuffled = list(successes)
    rng.shuffle(shuffled)

    groups = []
    for i in range(0, min(len(shuffled), max_groups * group_size), group_size):
        chunk = shuffled[i : i + group_size]
        if len(chunk) < 2:
            continue
        summaries = [summarize_trajectory(t) for t in chunk]
        groups.append(TrajectoryGroup(summaries=summaries))

    return groups[:max_groups]


def create_failure_groups(
    failures: List[dict],
    max_groups: int = 5,
    seed: int = 42,
) -> List[TrajectoryGroup]:
    """Create groups of failed trajectories by repo for pattern analysis."""
    rng = random.Random(seed)

    # Group failures by repo
    by_repo: Dict[str, List[dict]] = {}
    for f in failures:
        by_repo.setdefault(f["repo"], []).append(f)

    groups = []
    # Pick repos with multiple failures
    multi_fail_repos = [(repo, trajs) for repo, trajs in by_repo.items() if len(trajs) >= 2]
    rng.shuffle(multi_fail_repos)

    for repo, trajs in multi_fail_repos[:max_groups]:
        summaries = [summarize_trajectory(t) for t in trajs[:4]]
        groups.append(TrajectoryGroup(
            summaries=summaries,
            task_description=f"Repository: {repo}",
        ))

    return groups


# --------------------------------------------------------------------------
# Main extraction logic
# --------------------------------------------------------------------------


def format_existing_rules(rules: List[Rule]) -> str:
    """Format rules as numbered list for the prompt."""
    if not rules:
        return "(no existing rules yet)"
    return "\n".join(f"{i}. {r.text}" for i, r in enumerate(rules, 1))


def run_extraction(
    llm: LLMClient,
    pairs: List[TrajectoryPair],
    success_groups: List[TrajectoryGroup],
    failure_groups: List[TrajectoryGroup],
    max_rules: int = 20,
) -> List[Rule]:
    """Run the full ExpeL-style rule extraction pipeline.

    Phases:
    1. Compare critique: success vs failure pairs
    2. Success critique: patterns from successful trajectories
    3. Failure critique: anti-patterns from failed trajectories
    """
    rules: List[Rule] = []

    # Phase 1: Compare critiques (success vs failure)
    logger.info(f"Phase 1: Processing {len(pairs)} comparison pairs...")
    for i, pair in enumerate(pairs):
        list_full = len(rules) >= max_rules + 5
        suffix = SUFFIX_FULL if list_full else SUFFIX_NOT_FULL

        user_msg = HUMAN_CRITIQUE_COMPARE.format(
            task_description=pair.task_description,
            success_summary=pair.success_summary,
            fail_summary=pair.fail_summary,
            existing_rules=format_existing_rules(rules),
            operations_format=OPERATIONS_FORMAT,
            suffix=suffix,
        )

        llm_output = llm.chat(SYSTEM_CRITIQUE_COMPARE, user_msg)
        if llm_output:
            operations = parse_operations(llm_output)
            rules = update_rules(rules, operations, list_full=list_full)
            logger.info(
                f"  Pair {i + 1}/{len(pairs)}: {len(operations)} ops -> {len(rules)} rules"
            )
        else:
            logger.warning(f"  Pair {i + 1}/{len(pairs)}: empty LLM response")

        # Rate limiting
        time.sleep(0.5)

    # Phase 2: Success critiques
    logger.info(f"Phase 2: Processing {len(success_groups)} success groups...")
    for i, group in enumerate(success_groups):
        list_full = len(rules) >= max_rules + 5
        suffix = SUFFIX_FULL if list_full else SUFFIX_NOT_FULL

        success_text = "\n\n---\n\n".join(group.summaries)
        user_msg = HUMAN_CRITIQUE_ALL_SUCCESS.format(
            success_summaries=success_text,
            existing_rules=format_existing_rules(rules),
            operations_format=OPERATIONS_FORMAT,
            suffix=suffix,
        )

        llm_output = llm.chat(SYSTEM_CRITIQUE_ALL_SUCCESS, user_msg)
        if llm_output:
            operations = parse_operations(llm_output)
            rules = update_rules(rules, operations, list_full=list_full)
            logger.info(
                f"  Success group {i + 1}/{len(success_groups)}: "
                f"{len(operations)} ops -> {len(rules)} rules"
            )

        time.sleep(0.5)

    # Phase 3: Failure critiques
    logger.info(f"Phase 3: Processing {len(failure_groups)} failure groups...")
    for i, group in enumerate(failure_groups):
        list_full = len(rules) >= max_rules + 5
        suffix = SUFFIX_FULL if list_full else SUFFIX_NOT_FULL

        fail_text = "\n\n---\n\n".join(group.summaries)
        user_msg = HUMAN_CRITIQUE_ALL_FAIL.format(
            task_description=group.task_description,
            fail_summaries=fail_text,
            existing_rules=format_existing_rules(rules),
            operations_format=OPERATIONS_FORMAT,
            suffix=suffix,
        )

        llm_output = llm.chat(SYSTEM_CRITIQUE_ALL_FAIL, user_msg)
        if llm_output:
            operations = parse_operations(llm_output)
            rules = update_rules(rules, operations, list_full=list_full)
            logger.info(
                f"  Failure group {i + 1}/{len(failure_groups)}: "
                f"{len(operations)} ops -> {len(rules)} rules"
            )

        time.sleep(0.5)

    # Final: trim to max_rules (keep highest confidence)
    rules.sort(key=lambda r: r.count, reverse=True)
    rules = rules[:max_rules]

    return rules


# --------------------------------------------------------------------------
# CLI Commands
# --------------------------------------------------------------------------


@app.command()
def extract(
    split_file: Path = typer.Option(
        PROJECT_ROOT / "results" / "live_experiment" / "split.json",
        help="Path to the train/test split JSON file.",
    ),
    output: Path = typer.Option(
        PROJECT_ROOT / "data" / "baselines" / "expel_rules.json",
        help="Output path for extracted rules.",
    ),
    max_rules: int = typer.Option(20, help="Maximum number of rules to maintain."),
    max_pairs: int = typer.Option(50, help="Maximum comparison pairs to process."),
    max_success_groups: int = typer.Option(10, help="Maximum success groups."),
    max_failure_groups: int = typer.Option(5, help="Maximum failure groups."),
    model: str = typer.Option(
        "claude-sonnet-4-20250514", help="LLM model name."
    ),
    api_base: str = typer.Option(
        "http://localhost:4000/v1", help="LLM API base URL."
    ),
    seed: int = typer.Option(42, help="Random seed for reproducibility."),
    dry_run: bool = typer.Option(False, help="Parse trajectories but skip LLM calls."),
):
    """Extract ExpeL-style rules from training trajectories."""
    logger.info("Loading trajectories from split file...")
    if not split_file.exists():
        typer.echo(f"Split file not found: {split_file}", err=True)
        raise typer.Exit(1)

    successes, failures = load_trajectories_from_split(split_file)

    if not successes or not failures:
        typer.echo("Need both successful and failed trajectories.", err=True)
        raise typer.Exit(1)

    # Create data for each critique type
    logger.info("Creating trajectory pairs and groups...")
    pairs = create_pairs(successes, failures, max_pairs=max_pairs, seed=seed)
    success_groups = create_success_groups(
        successes, max_groups=max_success_groups, seed=seed
    )
    failure_groups = create_failure_groups(failures, max_groups=max_failure_groups, seed=seed)

    logger.info(
        f"Prepared: {len(pairs)} pairs, {len(success_groups)} success groups, "
        f"{len(failure_groups)} failure groups"
    )

    if dry_run:
        typer.echo(f"Dry run complete. Would process {len(pairs)} pairs.")
        typer.echo(f"Sample pair: {pairs[0].instance_id}" if pairs else "No pairs")
        raise typer.Exit(0)

    # Run extraction
    llm = LLMClient(api_base=api_base, model=model)
    rules = run_extraction(
        llm=llm,
        pairs=pairs,
        success_groups=success_groups,
        failure_groups=failure_groups,
        max_rules=max_rules,
    )

    # Save results
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "metadata": {
            "method": "expel_baseline",
            "max_rules": max_rules,
            "max_pairs": max_pairs,
            "model": model,
            "seed": seed,
            "n_successes": len(successes),
            "n_failures": len(failures),
            "n_pairs_processed": len(pairs),
            "n_success_groups": len(success_groups),
            "n_failure_groups": len(failure_groups),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "rules": [r.to_dict() for r in rules],
    }

    with open(output, "w") as f:
        json.dump(result, f, indent=2)

    logger.info(f"Extracted {len(rules)} rules -> {output}")
    typer.echo(f"\nExtracted {len(rules)} rules:")
    for i, rule in enumerate(rules, 1):
        typer.echo(f"  {i}. [{rule.count}] {rule.text}")


@app.command()
def show_rules(
    rules_file: Path = typer.Option(
        PROJECT_ROOT / "data" / "baselines" / "expel_rules.json",
        help="Path to the rules JSON file.",
    ),
):
    """Display extracted rules from a JSON file."""
    if not rules_file.exists():
        typer.echo(f"Rules file not found: {rules_file}", err=True)
        raise typer.Exit(1)

    with open(rules_file) as f:
        data = json.load(f)

    rules = [Rule.from_dict(r) for r in data["rules"]]
    metadata = data.get("metadata", {})

    typer.echo(f"ExpeL Rules ({len(rules)} total)")
    typer.echo(f"  Model: {metadata.get('model', 'unknown')}")
    typer.echo(f"  Extracted: {metadata.get('timestamp', 'unknown')}")
    typer.echo(f"  From: {metadata.get('n_successes', '?')} successes, "
               f"{metadata.get('n_failures', '?')} failures")
    typer.echo("")

    for i, rule in enumerate(rules, 1):
        typer.echo(f"  {i:2d}. [{rule.count:2d}] {rule.text}")


if __name__ == "__main__":
    app()
