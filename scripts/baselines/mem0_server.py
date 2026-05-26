"""Mem0 memory server, FastAPI shape compatible with QueryMemoryTool.

Mirrors `contextgraph_pro_server.py` so SWE-agent's tool bundle can target it
unchanged. Ingests the 300 SWE-ContextBench Lite Experience enriched summaries
into a Mem0 collection, then exposes /query_memory for retrieval.

Differences from ContextGraph:
- Mem0 internally runs an LLM-based fact extractor on add(). For summary
  ingestion (one paragraph per memory) we disable infer to skip that — we just
  want raw text indexed with embeddings.
- Embeddings + LLM both routed through our LiteLLM proxy via OPENAI_* env.
- Vector store: chroma local (no separate service).

Usage:
    OPENAI_API_KEY=$LITELLM_MASTER_KEY OPENAI_API_BASE=http://localhost:4000/v1 \
        uv run python scripts/baselines/mem0_server.py --port 8030 \
        --summaries-json results/swectx_paper/lite_summaries_enriched.json
"""
from __future__ import annotations
import argparse, json, os, sys, time
from pathlib import Path
from typing import Optional

import typer
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from mem0 import Memory


COLLECTION = "swectx_lite_exp"


class MemoryQueryRequest(BaseModel):
    problem_statement: Optional[str] = None
    error_message: Optional[str] = None
    current_file: Optional[str] = None
    query: Optional[str] = None


class MemoryQueryResponse(BaseModel):
    playbook: str
    num_entries: int
    retrieval_method: str = "mem0_chroma"


def build_memory(api_key: str, api_base: str, chroma_path: str,
                 collection: str = COLLECTION) -> Memory:
    """Build a Mem0 instance routed through LiteLLM, backed by local chroma."""
    cfg = {
        "llm": {
            "provider": "openai",
            "config": {
                "model": "claude-sonnet-4-20250514",
                "openai_base_url": api_base,
                "api_key": api_key,
            },
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": "text-embedding-3-large",
                "openai_base_url": api_base,
                "api_key": api_key,
                "embedding_dims": 3072,
            },
        },
        "vector_store": {
            "provider": "chroma",
            "config": {
                "collection_name": collection,
                "path": chroma_path,
            },
        },
        "version": "v1.1",
    }
    return Memory.from_config(cfg)


def ingest_summaries(memory: Memory, summaries_json: str, user_id: str) -> int:
    """Add each enriched summary as a single Mem0 memory.

    infer=False so Mem0 does NOT call its LLM to extract facts — we want the
    raw summary text indexed verbatim.
    """
    data = json.load(open(summaries_json))
    n = 0
    t0 = time.time()
    for r in data:
        rid = r["instance_id"]
        strategies = r.get("strategies") or []
        if not strategies or "rule_text" not in strategies[0]:
            print(f"  [skip {rid}] missing strategies[0].rule_text", file=sys.stderr)
            continue
        text = strategies[0]["rule_text"]
        try:
            memory.add(text, user_id=user_id, infer=False,
                       metadata={"instance_id": rid, "repo": r.get("repo", "")})
            n += 1
        except Exception as e:
            print(f"  [add err {rid}] {e}", file=sys.stderr)
        if n % 20 == 0:
            print(f"  ingested {n}/{len(data)} in {time.time()-t0:.1f}s", file=sys.stderr)
    print(f"  ingested total {n}/{len(data)} in {time.time()-t0:.1f}s", file=sys.stderr)
    return n


def create_app(memory: Memory, user_id: str) -> FastAPI:
    app = FastAPI(title="Mem0 Memory Server", version="1.0.0")

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "mem0_chroma", "user_id": user_id}

    @app.post("/query_memory", response_model=MemoryQueryResponse)
    def query_memory(request: MemoryQueryRequest):
        parts = []
        for f in (request.error_message, request.problem_statement, request.query):
            if f: parts.append(f)
        q = " ".join(parts).strip()
        if not q:
            return MemoryQueryResponse(playbook="", num_entries=0)
        try:
            try:
                res = memory.search(query=q, filters={"user_id": user_id}, limit=5)
            except TypeError:
                res = memory.search(query=q, user_id=user_id, limit=5)
        except Exception as e:
            return MemoryQueryResponse(playbook=f"<memory_error>{e}</memory_error>", num_entries=0)
        items = res.get("results", []) if isinstance(res, dict) else (res or [])
        if not items:
            return MemoryQueryResponse(playbook="", num_entries=0)
        body = []
        body.append("<memory_playbook>")
        body.append("The following entries were retrieved from a Mem0-indexed memory of past coding fixes.\n")
        for i, it in enumerate(items, 1):
            txt = it.get("memory") or it.get("text") or ""
            meta = it.get("metadata") or {}
            iid = meta.get("instance_id", "?")
            body.append(f"[{i}] (from {iid})\n{txt[:1500]}\n")
        body.append("</memory_playbook>")
        playbook = "\n".join(body)
        return MemoryQueryResponse(playbook=playbook, num_entries=len(items))

    return app


cli = typer.Typer()


@cli.command()
def serve(
    host: str = typer.Option("0.0.0.0"),
    port: int = typer.Option(8030),
    chroma_path: str = typer.Option("/home/jie/mem0_chroma_swectx"),
    summaries_json: str = typer.Option("results/swectx_paper/lite_summaries_enriched.json"),
    user_id: str = typer.Option("swectx"),
    skip_ingest: bool = typer.Option(False, "--skip-ingest"),
):
    api_key = os.environ.get("LITELLM_MASTER_KEY") or os.environ.get("OPENAI_API_KEY", "")
    api_base = os.environ.get("OPENAI_API_BASE", "http://localhost:4000/v1")
    if not api_key:
        sys.exit("Missing LITELLM_MASTER_KEY / OPENAI_API_KEY")
    Path(chroma_path).mkdir(parents=True, exist_ok=True)
    print(f"Building Mem0 (chroma at {chroma_path}, collection {COLLECTION})", file=sys.stderr)
    memory = build_memory(api_key, api_base, chroma_path)
    if not skip_ingest:
        print(f"Ingesting summaries from {summaries_json}", file=sys.stderr)
        ingest_summaries(memory, summaries_json, user_id)
    app = create_app(memory, user_id)
    print(f"Listening on {host}:{port}", file=sys.stderr)
    uvicorn.run(app, host=host, port=port)


@cli.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        serve()


if __name__ == "__main__":
    cli()
