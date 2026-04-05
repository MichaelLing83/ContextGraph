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


def _inject_strategy(instance_id: str, task: str, submission: str):
    """Online learning: inject a strategy from a successful submission into Neo4j."""
    if not submission.strip():
        return
    try:
        if _REPO_ROOT not in sys.path:
            sys.path.insert(0, _REPO_ROOT)
        from agent_memory.neo4j_store import Neo4jStore
        from agent_memory.embeddings import get_embedding_client

        # Extract repo from instance_id (e.g., "django__django-11490" -> "django/django")
        parts = instance_id.split("__")
        repo = parts[0].replace("_", "/") if len(parts) >= 2 else instance_id

        # Build concise strategy text from the diff
        diff_lines = submission.strip().split("\n")
        files_changed = [l.split(" b/")[-1] for l in diff_lines if l.startswith("diff --git")]
        strategy_text = (
            f"In {repo}, when fixing {instance_id}: "
            f"the fix was in {', '.join(files_changed) if files_changed else 'unknown files'}. "
            f"Patch size: {len(submission)} chars."
        )

        store = Neo4jStore(
            uri=f"bolt://localhost:{NEO4J_PORT}",
            auth=("neo4j", os.environ.get("NEO4J_PASSWORD", "contextgraph123")),
        )
        embedder = get_embedding_client(
            "openai",
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_API_BASE", ""),
            model="text-embedding-3-large",
        )

        # Get next ID
        result = store.driver.execute_query(
            "MATCH (p:PlaybookEntry) WHERE p.prefix = 'repo' "
            "RETURN max(toInteger(replace(p.id, 'repo-', ''))) AS max_num"
        )
        max_num = result.records[0]["max_num"] or 25
        entry_id = f"repo-{max_num + 1:05d}"

        embedding = embedder.embed(strategy_text)
        store.driver.execute_query(
            "CREATE (p:PlaybookEntry {"
            "  id: $id, text: $text, section: $section, prefix: 'repo',"
            "  embedding: $embedding, repo: $repo, instance_id: $instance_id"
            "})",
            {"id": entry_id, "text": strategy_text,
             "section": "PROBLEM-SOLVING HEURISTICS AND WORKFLOWS",
             "embedding": embedding, "repo": repo, "instance_id": instance_id},
        )
        store.driver.close()
        logger.info("[ONLINE] Injected %s: %s", entry_id, strategy_text[:80])
    except Exception as e:
        logger.warning("[ONLINE] Injection failed for %s: %s", instance_id, e)


def enable():
    """Monkey-patch ProgressTrackingAgent.run to inject memory."""
    from minisweagent.run.benchmarks.swebench import ProgressTrackingAgent

    _original_run = ProgressTrackingAgent.run

    def _patched_run(self, task: str) -> dict:
        # Pre-run: inject memory
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

        info = _original_run(self, augmented)

        # Post-run: online learning — inject strategy from successful submissions
        submission = info.get("submission", "")
        if submission and info.get("exit_status") == "Submitted":
            _inject_strategy(self.instance_id, task, submission)

        return info

    ProgressTrackingAgent.run = _patched_run
    logger.info("ContextGraph treatment mode enabled (dynamic memory: query + online learning)")
