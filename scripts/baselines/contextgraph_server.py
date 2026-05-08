"""FastAPI server wrapping ContextGraph's Neo4j-based retrieval.

Exposes the same /query_memory endpoint as faiss_server and agentkb_server,
so that query_memory_impl.py inside the SWE-agent Docker container can use
the HTTP backend path without needing numpy, neo4j driver, or embeddings.

Usage:
    uv run python scripts/baselines/contextgraph_server.py --port 8003
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import typer
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from agent_memory import AgentMemory
from agent_memory.evaluation.swe_agent_tool import QueryMemoryTool, QueryMemoryInput


class MemoryQueryRequest(BaseModel):
    problem_statement: Optional[str] = None
    error_message: Optional[str] = None
    current_file: Optional[str] = None
    query: Optional[str] = None


class MemoryQueryResponse(BaseModel):
    playbook: str
    num_entries: int
    retrieval_method: str = "contextgraph_ppr"


def create_app() -> FastAPI:
    app = FastAPI(title="ContextGraph Server", version="1.0.0")

    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "contextgraph123")

    api_key = (
        os.environ.get("LITELLM_MASTER_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "")
    )
    base_url = os.environ.get("OPENAI_API_BASE", "http://localhost:4000/v1")
    model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-large")

    rewriter_enabled = os.environ.get("REWRITER_ENABLED", "").lower() in (
        "1", "true", "yes",
    )
    rewriter_api_base = os.environ.get(
        "REWRITER_API_BASE", "http://localhost:4000/v1"
    )
    rewriter_api_key = (
        os.environ.get("REWRITER_API_KEY", "")
        or os.environ.get("LITELLM_MASTER_KEY", "")
    )
    rewriter_model = os.environ.get("REWRITER_MODEL", "claude-sonnet-4-20250514")

    memory = AgentMemory(
        neo4j_uri=neo4j_uri,
        neo4j_auth=(neo4j_user, neo4j_password),
        embedding_api_key=api_key or None,
        embedding_base_url=base_url or None,
        embedding_model=model,
        rewriter_api_base=rewriter_api_base or None,
        rewriter_api_key=rewriter_api_key or None,
        rewriter_model=rewriter_model,
        rewriter_enabled=rewriter_enabled,
    )
    tool = QueryMemoryTool(memory)

    @app.get("/health")
    def health():
        return {"status": "ok", "neo4j_uri": neo4j_uri}

    @app.post("/query_memory", response_model=MemoryQueryResponse)
    def query_memory(request: MemoryQueryRequest):
        error_msg = request.error_message or request.query or ""
        task_desc = request.problem_statement or ""
        inp = QueryMemoryInput(
            current_error=error_msg,
            task_description=task_desc,
            phase="fixing",
        )
        output = tool.invoke(inp)
        try:
            playbook = output.to_structured()
        except Exception:
            playbook = output.to_json()
        return MemoryQueryResponse(
            playbook=playbook,
            num_entries=1,
        )

    return app


cli = typer.Typer()


@cli.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(8003, help="Bind port"),
):
    app = create_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    cli()
