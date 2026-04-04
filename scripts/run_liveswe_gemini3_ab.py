#!/usr/bin/env python3
"""
live-SWE-agent + Gemini 3 Pro A/B experiment on SWE-bench Verified.

Treatment: ContextGraph memory injected via instance_template
Control: standard live-SWE-agent config

Requires:
  - mini-swe-agent: /tmp/mini-swe-venv/bin/mini
  - OPENROUTER_API_KEY env var
  - Neo4j running on port 7690 (online graph with repo-specific strategies)

Usage:
  python scripts/run_liveswe_gemini3_ab.py --n 500 --concurrency 10
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("liveswe_gemini3_ab")

MINI = os.environ.get("MINI_BIN", "/tmp/mini-swe-venv/bin/mini")
MODEL = os.environ.get("LIVESWE_MODEL", "openrouter/google/gemini-3.1-pro-preview")
NEO4J_PORT = os.environ.get("NEO4J_PORT", "7690")
CONTROL_CONFIG = _REPO_ROOT / "configs" / "liveswe_gemini3_control.yaml"
TREATMENT_CONFIG = _REPO_ROOT / "configs" / "liveswe_gemini3_treatment.yaml"


@dataclass
class RunResult:
    instance_id: str
    group: str
    run_k: int
    has_patch: bool = False
    duration: float = 0.0
    error: str = ""


def get_memory_context(instance_id: str, problem_text: str) -> str:
    """Query ContextGraph for memory context."""
    try:
        from agent_memory import AgentMemory
        from agent_memory.evaluation.swe_agent_tool import QueryMemoryInput, QueryMemoryTool

        memory = AgentMemory(
            neo4j_uri=f"bolt://localhost:{NEO4J_PORT}",
            neo4j_auth=("neo4j", os.environ.get("NEO4J_PASSWORD", "contextgraph123")),
            embedding_api_key=os.environ.get("OPENAI_API_KEY", ""),
            embedding_base_url=os.environ.get("OPENAI_API_BASE", ""),
            embedding_model="text-embedding-3-large",
        )
        try:
            tool = QueryMemoryTool(memory)
            output = tool.invoke(QueryMemoryInput(
                current_error=problem_text[:500],
                task_description=f"Fix bug {instance_id}",
                phase="fixing",
            ))
            return output.to_structured()
        finally:
            memory.close()
    except Exception as e:
        logger.warning("Memory query failed for %s: %s", instance_id, e)
        return ""


def make_treatment_config(instance_id: str, problem_text: str, output_dir: Path) -> Path:
    """Generate treatment config with memory context injected."""
    memory = get_memory_context(instance_id, problem_text)
    if not memory:
        memory = "No relevant past experiences found."

    # Read template and replace {{memory_context}}
    config_text = TREATMENT_CONFIG.read_text()
    config_text = config_text.replace("{{memory_context}}", memory)

    out = output_dir / f"treatment_config_{instance_id}.yaml"
    out.write_text(config_text)
    return out


def run_single(instance_id: str, problem_text: str, group: str,
               run_k: int, output_dir: Path) -> RunResult:
    """Run a single instance through mini-swe-agent."""
    result = RunResult(instance_id=instance_id, group=group, run_k=run_k)
    run_dir = output_dir / f"{instance_id}_{group}_k{run_k}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Both groups use the same config (control). Memory is injected into task text.
    config = CONTROL_CONFIG

    # Build task text
    if group == "treatment":
        memory = get_memory_context(instance_id, problem_text)
        if memory:
            task_text = (
                f"<memory_context>\n"
                f"The following strategies from past debugging experiences may help:\n"
                f"{memory}\n"
                f"</memory_context>\n\n"
                f"{problem_text[:4000]}"
            )
        else:
            task_text = problem_text[:5000]
    else:
        task_text = problem_text[:5000]

    # Build command
    traj_file = run_dir / "trajectory.json"
    cmd = [
        MINI,
        "--config", str(config),
        "-m", MODEL,
        "--task", task_text,
        "--output", str(traj_file),
        "--yolo",  # non-interactive
    ]

    env = {
        **os.environ,
        "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY", ""),
        "MSWEA_CONFIGURED": "1",  # skip mini-swe-agent setup wizard
    }

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=1800, env=env, cwd=str(run_dir),
        )
        result.error = proc.stderr[-500:] if proc.returncode != 0 else ""

        # Check trajectory for patch
        if traj_file.exists():
            try:
                traj = json.loads(traj_file.read_text())
                # mini-swe-agent stores patch in trajectory metadata
                if isinstance(traj, dict):
                    patch = traj.get("patch", "") or traj.get("git_patch", "")
                    result.has_patch = bool(patch and patch.strip())
                elif isinstance(traj, list):
                    # Check last events for submit action
                    result.has_patch = any("COMPLETE_TASK" in str(e) for e in traj[-5:])
            except Exception:
                pass

    except subprocess.TimeoutExpired:
        result.error = "timeout"
    except Exception as e:
        result.error = str(e)[:500]

    result.duration = round(time.monotonic() - start, 1)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=500, help="Number of problems")
    parser.add_argument("--k", type=int, default=1, help="Runs per problem per group")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--output", type=str, default="/tmp/liveswe-gemini3-ab")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load SWE-bench Verified
    from datasets import load_dataset
    ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    problems = {r["instance_id"]: r["problem_statement"] for r in ds}

    selected = list(problems.keys())[:args.n]
    total = len(selected) * 2 * args.k
    logger.info("Running %d problems x 2 groups x k=%d = %d (concurrency=%d)",
                len(selected), args.k, total, args.concurrency)

    results = []
    status = {"total": total, "completed": 0, "errors": 0}
    status_file = output_dir / "status.json"

    def run_and_track(pid, group, k):
        r = run_single(pid, problems[pid], group, k, output_dir)
        results.append(r)
        status["completed"] += 1
        if r.error:
            status["errors"] += 1
        with open(status_file, "w") as f:
            json.dump(status, f, indent=2)
        logger.info("[%s] %s k%d — %.1fs, patch=%s%s",
                    pid, group, k, r.duration, r.has_patch,
                    f", err={r.error[:50]}" if r.error else "")
        return r

    # Build tasks
    tasks = []
    for pid in selected:
        for k in range(1, args.k + 1):
            for group in ["treatment", "control"]:
                tasks.append((pid, group, k))

    # Run with concurrency
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {executor.submit(run_and_track, pid, group, k): (pid, group, k)
                   for pid, group, k in tasks}
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                pid, group, k = futures[f]
                logger.error("Fatal: %s %s k%d — %s", pid, group, k, e)

    # Save results
    with open(output_dir / "results.json", "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)

    t_patch = sum(1 for r in results if r.group == "treatment" and r.has_patch)
    c_patch = sum(1 for r in results if r.group == "control" and r.has_patch)
    logger.info("DONE: %d runs, treatment=%d patches, control=%d patches, errors=%d",
                len(results), t_patch, c_patch, status["errors"])


if __name__ == "__main__":
    main()
