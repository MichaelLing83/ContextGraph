"""ContextGraph ablation server — single channel or combination, per --mode.

Modes:
  cosine_only       Only cosine vector search (3072-dim text-embedding-3-large)
  bm25_only         Only Neo4j fulltext BM25
  rrf_no_mmr        Cosine + BM25 with RRF merge, diversity=0 (no MMR)
  full              Cosine + BM25 with RRF + MMR diversity=0.3 (= contextgraph_pro_server)
  full_with_ppr     full + PPR seeded from extracted error_type keyword

Mirrors `contextgraph_pro_server.py` HTTP shape; QueryMemoryTool unchanged.
"""
from __future__ import annotations
import os, re, sys
from pathlib import Path
from typing import Optional, List

import typer
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from agent_memory import AgentMemory
from agent_memory.playbook import format_playbook


# Common Python error keywords we'll look for in problem_statement / error_message
ERROR_KEYWORDS = [
    "TypeError", "ValueError", "AttributeError", "KeyError", "IndexError",
    "ImportError", "ModuleNotFoundError", "NameError", "RuntimeError",
    "AssertionError", "RecursionError", "ZeroDivisionError", "FileNotFoundError",
    "PermissionError", "NotImplementedError", "StopIteration", "OSError",
    "UnboundLocalError", "OverflowError", "UnicodeDecodeError",
]


class MemoryQueryRequest(BaseModel):
    problem_statement: Optional[str] = None
    error_message: Optional[str] = None
    current_file: Optional[str] = None
    query: Optional[str] = None


class MemoryQueryResponse(BaseModel):
    playbook: str
    num_entries: int
    retrieval_method: str = "contextgraph_ablation"


def extract_error_type(text: str) -> Optional[str]:
    """Find first known Python error keyword in text, if any."""
    for kw in ERROR_KEYWORDS:
        if re.search(rf"\b{kw}\b", text):
            return kw
    return None


def create_app(mode: str) -> FastAPI:
    app = FastAPI(title=f"ContextGraph Ablation Server ({mode})", version="1.0.0")

    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7695")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "contextgraph123")

    api_key = os.environ.get("LITELLM_MASTER_KEY") or os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_API_BASE", "http://localhost:4000/v1")
    model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-large")

    memory = AgentMemory(
        neo4j_uri=neo4j_uri,
        neo4j_auth=(neo4j_user, neo4j_password),
        embedding_api_key=api_key or None,
        embedding_base_url=base_url or None,
        embedding_model=model,
        rewriter_enabled=False,
    )
    retriever = memory.playbook_retriever

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": mode, "neo4j_uri": neo4j_uri}

    # Each mode is a fixed combination of the public retrieve() flags.
    # PPR is gated on `error_type` server-side; for modes that should NOT
    # use PPR we always pass error_type=None, for `full_with_ppr` we extract
    # an error keyword from the request text first.
    _MODES = {
        "cosine_only":    dict(use_cosine=True,  use_bm25=False, use_ppr=False, use_mmr=False),
        "bm25_only":      dict(use_cosine=False, use_bm25=True,  use_ppr=False, use_mmr=False),
        "rrf_no_mmr":     dict(use_cosine=True,  use_bm25=True,  use_ppr=False, use_mmr=False),
        "full":           dict(use_cosine=True,  use_bm25=True,  use_ppr=False, use_mmr=True),
        "full_with_ppr":  dict(use_cosine=True,  use_bm25=True,  use_ppr=True,  use_mmr=True),
    }

    @app.post("/query_memory", response_model=MemoryQueryResponse)
    def query_memory(request: MemoryQueryRequest):
        parts = []
        for f in (request.error_message, request.problem_statement, request.query):
            if f: parts.append(f)
        q = " ".join(parts).strip()
        if not q:
            return MemoryQueryResponse(playbook="", num_entries=0)
        if mode not in _MODES:
            return MemoryQueryResponse(playbook=f"<bad_mode>{mode}</bad_mode>", num_entries=0)

        # Only the `full_with_ppr` mode needs an error_type for PPR seeding;
        # other modes pass None so retrieve() short-circuits PPR cleanly.
        if mode == "full_with_ppr":
            err = extract_error_type(
                (request.error_message or "") + " " + (request.problem_statement or "")
            )
        else:
            err = None

        entries = retriever.retrieve(
            q, top_k=5, error_type=err, **_MODES[mode],
        )
        if not entries:
            return MemoryQueryResponse(playbook="", num_entries=0)
        playbook = format_playbook(entries, wrap=True)
        if len(playbook) > 2500 and len(entries) > 3:
            playbook = format_playbook(entries[:3], wrap=True)
        return MemoryQueryResponse(
            playbook=playbook, num_entries=len(entries),
            retrieval_method=f"contextgraph_ablation_{mode}",
        )

    return app


cli = typer.Typer()


@cli.command()
def serve(
    host: str = typer.Option("0.0.0.0"),
    port: int = typer.Option(8040),
    mode: str = typer.Option("full"),
):
    app = create_app(mode)
    print(f"[ablation:{mode}] listening on {host}:{port}", file=sys.stderr)
    uvicorn.run(app, host=host, port=port)


@cli.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context, mode: str = typer.Option("full")):
    if ctx.invoked_subcommand is None:
        app = create_app(mode)
        uvicorn.run(app, host="0.0.0.0", port=8040)


if __name__ == "__main__":
    cli()
