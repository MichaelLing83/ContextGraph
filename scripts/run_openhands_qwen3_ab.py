#!/usr/bin/env python3
"""
OpenHands + Qwen3-Coder A/B experiment via OpenRouter.

Runs SWE-bench Verified problems through OpenHands (Docker) with
Qwen3-Coder-480B-A35B via OpenRouter API.

Treatment group: with ContextGraph memory (conversation_instructions)
Control group: standard OpenHands, no memory

Usage:
    # Set env vars first (or source .env)
    export OPENROUTER_API_KEY=...
    export NEO4J_PASSWORD=...
    export OPENAI_API_KEY=...  # for embeddings

    # Run 10 problems for testing
    python scripts/run_openhands_qwen3_ab.py --n 10

    # Run all 16 differential problems with k=5
    python scripts/run_openhands_qwen3_ab.py --n 16 --k 5
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(_REPO_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("openhands_qwen3_ab")

# ---------------------------------------------------------------------------
# OpenHands imports (requires Python 3.12 venv)
# ---------------------------------------------------------------------------
try:
    from openhands.core.config.llm_config import LLMConfig
    from openhands.core.config.openhands_config import OpenHandsConfig
    from openhands.core.config.sandbox_config import SandboxConfig
    from openhands.core.main import create_runtime, run_controller
    from openhands.events.action import MessageAction

    # Monkey-patch: force mock function calling for Qwen3-Coder via OpenRouter.
    # The SDK LLM class has its own native_tool_calling=True default that ignores
    # LLMConfig.native_tool_calling. We override at the class level.
    try:
        from openhands.sdk.llm.llm import LLM as SDKLLM
        SDKLLM.model_fields["native_tool_calling"].default = False
        logger.info("Patched SDK LLM: native_tool_calling=False")
    except Exception as e:
        logger.warning("Could not patch SDK LLM: %s", e)

except ImportError:
    logger.error(
        "OpenHands not found. Run with Python 3.12 venv:\n"
        "  /tmp/openhands-venv/bin/python scripts/run_openhands_qwen3_ab.py"
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODEL = os.environ.get("OH_MODEL", "openrouter/qwen/qwen3-coder")
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
API_BASE = "https://openrouter.ai/api/v1"
NEO4J_PORT = os.environ.get("NEO4J_PORT", "7690")  # online graph
MAX_ITERATIONS = int(os.environ.get("OH_MAX_ITER", "50"))
TIMEOUT = int(os.environ.get("OH_TIMEOUT", "600"))

# Problems: use the 16 differential problems from k5 experiment
PROBLEM_IDS = [
    "astropy__astropy-12907", "astropy__astropy-14539",
    "django__django-11490", "django__django-11532",
    "django__django-12050", "django__django-12155",
    "django__django-12419", "django__django-13933",
    "django__django-14373", "django__django-14725",
    "django__django-15375", "django__django-15382",
    "django__django-15732", "django__django-16631",
    "matplotlib__matplotlib-20488", "scikit-learn__scikit-learn-25973",
]


@dataclass
class RunResult:
    instance_id: str
    group: str  # "treatment" or "control"
    run_k: int
    has_patch: bool = False  # True if agent produced a non-empty git diff
    steps: int = 0
    duration: float = 0.0
    git_patch: str = ""
    error: str = ""


def get_memory_context(instance_id: str, problem_statement: str) -> str:
    """Query ContextGraph for memory context to inject into conversation."""
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
                current_error=problem_statement[:500],
                task_description=f"Fix bug {instance_id}",
                phase="fixing",
            ))
            return output.to_structured()
        finally:
            memory.close()
    except Exception as e:
        logger.warning("Memory query failed for %s: %s", instance_id, e)
        return ""


def build_config() -> OpenHandsConfig:
    llm_config = LLMConfig(
        model=MODEL,
        api_key=API_KEY,
        base_url=API_BASE,
        temperature=0.0,
        num_retries=3,
        retry_min_wait=5,
        retry_max_wait=30,
        # Qwen3-Coder via OpenRouter has broken native tool calling:
        # model puts tool calls inside <think> blocks or omits <tool_call> tags,
        # and OpenRouter fails to parse them into OpenAI tool_calls format.
        # Use prompt-based mock function calling instead.
        native_tool_calling=False,
        drop_params=True,
    )
    return OpenHandsConfig(
        llms={"llm": llm_config},
        default_agent="CodeActAgent",
        runtime="docker",
        max_iterations=MAX_ITERATIONS,
        sandbox=SandboxConfig(
            timeout=TIMEOUT,
            use_host_network=True,
        ),
    )


async def run_single(
    instance_id: str,
    problem_statement: str,
    group: str,
    run_k: int,
    output_dir: Path,
) -> RunResult:
    """Run a single instance."""
    config = build_config()
    config.workspace_base = str(output_dir / "workspace" / f"{instance_id}_{group}_k{run_k}")
    config.save_trajectory_path = str(
        output_dir / "trajectories" / f"{instance_id}_{group}_k{run_k}.json"
    )

    # Build prompt
    prompt = f"Fix the following bug:\n\n{problem_statement}"

    # Add memory context for treatment group
    if group == "treatment":
        memory = get_memory_context(instance_id, problem_statement)
        if memory:
            prompt = (
                f"<memory_context>\n{memory}\n</memory_context>\n\n"
                f"Use the above memory context from past debugging experiences "
                f"to guide your approach.\n\n{prompt}"
            )

    start = time.monotonic()
    result = RunResult(instance_id=instance_id, group=group, run_k=run_k)

    try:
        initial_action = MessageAction(content=prompt)
        state = await run_controller(
            config=config,
            initial_user_action=initial_action,
            fake_user_response_fn=lambda _: "Please continue.",
        )

        if state:
            result.steps = state.iteration if hasattr(state, "iteration") else 0
            result.git_patch = state.git_patch if hasattr(state, "git_patch") else ""
        else:
            result.error = "run_controller returned None"

    except Exception as e:
        result.error = str(e)[:500]
        logger.error("Error running %s %s k%d: %s", instance_id, group, run_k, e)

    result.duration = round(time.monotonic() - start, 1)
    result.has_patch = bool(result.git_patch and result.git_patch.strip())
    return result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=500, help="Number of problems")
    parser.add_argument("--k", type=int, default=1, help="Runs per problem per group")
    parser.add_argument("--output", type=str, default="/tmp/openhands-qwen3-ab")
    parser.add_argument("--concurrency", type=int, default=1, help="Max concurrent runs")
    parser.add_argument("--problems-file", type=str, default=None,
                        help="JSON file with problem IDs (list of strings). Overrides built-in list.")
    parser.add_argument("--all-verified", action="store_true",
                        help="Use all 500 SWE-bench Verified problems instead of built-in list")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "trajectories").mkdir(exist_ok=True)
    (output_dir / "workspace").mkdir(exist_ok=True)

    # Load problem IDs
    from datasets import load_dataset
    ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")

    if args.problems_file:
        with open(args.problems_file) as f:
            problem_ids = json.load(f)
        if isinstance(problem_ids, list) and problem_ids and isinstance(problem_ids[0], dict):
            problem_ids = [p["id"] for p in problem_ids]
    elif args.all_verified:
        problem_ids = [r["instance_id"] for r in ds]
    else:
        problem_ids = PROBLEM_IDS

    problems = {r["instance_id"]: r for r in ds if r["instance_id"] in problem_ids}

    selected = problem_ids[:args.n]
    total = len(selected) * 2 * args.k
    logger.info("Running %d problems x 2 groups x k=%d = %d total (concurrency=%d)",
                len(selected), args.k, total, args.concurrency)

    results: List[RunResult] = []
    results_lock = asyncio.Lock()
    status = {"total": total, "completed": 0, "errors": 0}
    status_file = output_dir / "status.json"
    sem = asyncio.Semaphore(args.concurrency)

    async def run_with_sem(pid, prob, group, k):
        async with sem:
            logger.info("[%s] %s k%d — starting", pid, group, k)
            r = await run_single(pid, prob["problem_statement"], group, k, output_dir)
            async with results_lock:
                results.append(r)
                status["completed"] += 1
                if r.error:
                    status["errors"] += 1
                with open(status_file, "w") as sf:
                    json.dump(status, sf, indent=2)
            logger.info(
                "[%s] %s k%d — %.1fs, steps=%s, patch=%s%s",
                pid, group, k, r.duration, r.steps, r.has_patch,
                f", err={r.error[:50]}" if r.error else ""
            )
            return r

    # Build all tasks
    tasks = []
    for pid in selected:
        if pid not in problems:
            logger.warning("Problem %s not found in dataset, skipping", pid)
            continue
        prob = problems[pid]
        for k in range(1, args.k + 1):
            for group in ["treatment", "control"]:
                tasks.append(run_with_sem(pid, prob, group, k))

    # Run with concurrency
    await asyncio.gather(*tasks, return_exceptions=True)

    # Save results
    with open(output_dir / "results.json", "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    with open(output_dir / "status.json", "w") as f:
        json.dump(status, f, indent=2)

    # Summary
    t_patch = sum(1 for r in results if r.group == "treatment" and r.has_patch)
    c_patch = sum(1 for r in results if r.group == "control" and r.has_patch)
    logger.info(
        "DONE: %d runs, treatment patches=%d, control patches=%d, errors=%d",
        len(results), t_patch, c_patch, status["errors"]
    )


if __name__ == "__main__":
    asyncio.run(main())
