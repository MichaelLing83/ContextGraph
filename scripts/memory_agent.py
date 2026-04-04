"""Custom mini-swe-agent agent class that injects ContextGraph memory into task text.

Usage with mini-extra swebench:
  mini-extra swebench \
    --agent-class scripts.memory_agent.MemoryAgent \
    ...

The agent queries Neo4j for relevant strategies before starting,
and prepends them to the problem statement.
"""

import logging
import os
import sys

# Ensure repo root is importable
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from minisweagent.agents.default import DefaultAgent

logger = logging.getLogger("memory_agent")

NEO4J_PORT = os.environ.get("NEO4J_PORT", "7690")


def _get_memory_context(task_text: str) -> str:
    """Query ContextGraph for memory relevant to this task."""
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
                current_error=task_text[:500],
                task_description="Fix the bug described in the task",
                phase="fixing",
            ))
            result = output.to_structured()
            logger.info("Memory context: %d chars", len(result))
            return result
        finally:
            memory.close()
    except Exception as e:
        logger.warning("Memory query failed: %s", e)
        return ""


class MemoryAgent(DefaultAgent):
    """DefaultAgent with ContextGraph memory injection.

    Before the first step, prepends memory context to the task text
    in the user message.
    """

    def run(self, task: str) -> None:
        # Inject memory context
        memory = _get_memory_context(task)
        if memory:
            augmented_task = (
                f"<memory_context>\n"
                f"The following strategies from past debugging experiences may help:\n"
                f"{memory}\n"
                f"</memory_context>\n\n"
                f"{task}"
            )
            logger.info("Injected %d chars of memory context", len(memory))
        else:
            augmented_task = task

        # Run the standard agent with augmented task
        super().run(augmented_task)
