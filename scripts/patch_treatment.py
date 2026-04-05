"""Monkey-patch mini-swe-agent to inject ContextGraph memory into task text.

Usage: Run this BEFORE mini-extra swebench to enable treatment mode.

  import scripts.patch_treatment
  scripts.patch_treatment.enable()

Or as env var entrypoint:
  PYTHONPATH=. python -c "import scripts.patch_treatment; scripts.patch_treatment.enable()" && mini-extra swebench ...

Or simpler — set CONTEXTGRAPH_TREATMENT=1 and use the wrapper:
  CONTEXTGRAPH_TREATMENT=1 python scripts/run_liveswe_treatment.py [same args as mini-extra swebench]
"""

import logging
import os
import sys

logger = logging.getLogger("patch_treatment")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEO4J_PORT = os.environ.get("NEO4J_PORT", "7690")

_memory_cache = {}


def _get_memory(task_text: str) -> str:
    """Query ContextGraph, with caching."""
    cache_key = task_text[:200]
    if cache_key in _memory_cache:
        return _memory_cache[cache_key]

    try:
        if _REPO_ROOT not in sys.path:
            sys.path.insert(0, _REPO_ROOT)
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
                current_error=task_text[:500],
                task_description="Fix the bug",
                phase="fixing",
            ))
            result = output.to_structured()
            _memory_cache[cache_key] = result
            return result
        finally:
            memory.close()
    except Exception as e:
        logger.warning("Memory query failed: %s", e)
        _memory_cache[cache_key] = ""
        return ""


def enable():
    """Monkey-patch ProgressTrackingAgent.run to inject memory."""
    from minisweagent.run.benchmarks.swebench import ProgressTrackingAgent

    _original_run = ProgressTrackingAgent.run

    def _patched_run(self, task: str) -> dict:
        memory = _get_memory(task)
        if memory:
            augmented = (
                "<memory_context>\n"
                "Strategies from past debugging experiences:\n"
                f"{memory}\n"
                "</memory_context>\n\n"
                f"{task}"
            )
            logger.info("Injected %d chars of memory for %s", len(memory), self.instance_id)
        else:
            augmented = task
        return _original_run(self, augmented)

    ProgressTrackingAgent.run = _patched_run
    logger.info("ContextGraph treatment mode enabled (monkey-patched ProgressTrackingAgent.run)")
