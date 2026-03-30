"""MCP server exposing ContextGraph agent memory to any MCP-compatible client."""

import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

# Ensure agent_memory is importable
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from agent_memory import AgentMemory
from agent_memory.evaluation.swe_agent_tool import QueryMemoryInput, QueryMemoryTool

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "contextgraph-memory",
    instructions="Query ContextGraph agent memory for debugging strategies and past experiences",
)

# Lazy singleton — initialized on first tool call
_memory: AgentMemory | None = None
_tool: QueryMemoryTool | None = None


def _get_tool() -> QueryMemoryTool:
    global _memory, _tool
    if _tool is not None:
        return _tool

    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "")
    if not neo4j_password:
        raise RuntimeError("NEO4J_PASSWORD environment variable is required")

    embedding_api_key = os.environ.get("OPENAI_API_KEY", "")
    embedding_base_url = os.environ.get("OPENAI_API_BASE", "https://api.chatanywhere.org")
    embedding_model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-large")

    _memory = AgentMemory(
        neo4j_uri=neo4j_uri,
        neo4j_auth=(neo4j_user, neo4j_password),
        embedding_api_key=embedding_api_key or None,
        embedding_base_url=embedding_base_url or None,
        embedding_model=embedding_model,
    )
    _tool = QueryMemoryTool(_memory)
    logger.info("AgentMemory initialized (Neo4j: %s)", neo4j_uri)
    return _tool


@mcp.tool()
def query_memory(
    current_error: str,
    task_description: str,
    phase: str = "fixing",
) -> str:
    """Query agent memory for similar past experiences and debugging strategies.

    Use this tool when you encounter an error or need guidance based on
    previously solved coding problems. Returns relevant strategies,
    similar error fragments, and playbook entries from the context graph.

    Args:
        current_error: The error message or traceback currently being debugged.
        task_description: Brief description of the current task or issue.
        phase: Current phase of problem solving.
            One of: exploring, understanding, locating, fixing, verifying, testing.
    """
    tool = _get_tool()
    input_data = QueryMemoryInput(
        current_error=current_error,
        task_description=task_description,
        phase=phase,
    )
    output = tool.invoke(input_data)
    try:
        return output.to_structured()
    except Exception:
        return output.to_json()


if __name__ == "__main__":
    mcp.run()
