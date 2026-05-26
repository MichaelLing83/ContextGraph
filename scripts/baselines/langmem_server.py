"""LangMem memory server — FastAPI shape compatible with QueryMemoryTool.

Uses LangGraph's `InMemoryStore` with an OpenAI-compatible embedder routed
through our LiteLLM proxy. Ingests the 300 enriched summaries as one memory
each, exposes /query_memory for retrieval.

Usage:
    OPENAI_API_KEY=$LITELLM_MASTER_KEY OPENAI_API_BASE=http://localhost:4000/v1 \
        uv run python scripts/baselines/langmem_server.py --port 8031 \
        --summaries-json results/swectx_paper/lite_summaries_enriched.json
"""
from __future__ import annotations
import argparse, json, os, sys, time
from pathlib import Path
from typing import Optional, List

import typer
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from langgraph.store.memory import InMemoryStore


NAMESPACE = ("swectx_lite_exp",)


class MemoryQueryRequest(BaseModel):
    problem_statement: Optional[str] = None
    error_message: Optional[str] = None
    current_file: Optional[str] = None
    query: Optional[str] = None


class MemoryQueryResponse(BaseModel):
    playbook: str
    num_entries: int
    retrieval_method: str = "langmem_inmemory"


def build_store(api_key: str, api_base: str) -> InMemoryStore:
    """InMemoryStore backed by an OpenAI-compatible embedder via LiteLLM."""
    # LangChain OpenAIEmbeddings reads OPENAI_API_KEY/OPENAI_API_BASE from env;
    # we've set both above by caller. The "openai:" prefix uses langchain-openai.
    return InMemoryStore(
        index={
            "embed": "openai:text-embedding-3-large",
            "dims": 3072,
        }
    )


def ingest_summaries(store: InMemoryStore, summaries_json: str) -> int:
    data = json.load(open(summaries_json))
    t0 = time.time()
    ingested = 0
    for r in data:
        rid = r["instance_id"]
        strategies = r.get("strategies") or []
        if not strategies or "rule_text" not in strategies[0]:
            print(f"  [skip {rid}] missing strategies[0].rule_text", file=sys.stderr)
            continue
        text = strategies[0]["rule_text"]
        # store.put expects (namespace, key, value); value must be json-serializable dict
        store.put(NAMESPACE, key=rid, value={
            "content": text,
            "repo": r.get("repo", ""),
            "instance_id": rid,
        })
        ingested += 1
    print(f"  ingested {ingested}/{len(data)} items into {NAMESPACE} in {time.time()-t0:.1f}s", file=sys.stderr)
    return ingested


def create_app(store: InMemoryStore) -> FastAPI:
    app = FastAPI(title="LangMem Memory Server", version="1.0.0")

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "langmem_inmemory"}

    @app.post("/query_memory", response_model=MemoryQueryResponse)
    def query_memory(request: MemoryQueryRequest):
        parts = []
        for f in (request.error_message, request.problem_statement, request.query):
            if f: parts.append(f)
        q = " ".join(parts).strip()
        if not q:
            return MemoryQueryResponse(playbook="", num_entries=0)
        try:
            results = store.search(NAMESPACE, query=q, limit=5)
        except Exception as e:
            return MemoryQueryResponse(playbook=f"<memory_error>{e}</memory_error>", num_entries=0)
        if not results:
            return MemoryQueryResponse(playbook="", num_entries=0)
        body = ["<memory_playbook>",
                "The following entries were retrieved from a LangMem-indexed memory of past coding fixes.\n"]
        for i, hit in enumerate(results, 1):
            v = hit.value if hasattr(hit, "value") else hit.get("value", {})
            iid = v.get("instance_id", "?")
            txt = v.get("content", "")
            body.append(f"[{i}] (from {iid})\n{txt[:1500]}\n")
        body.append("</memory_playbook>")
        return MemoryQueryResponse(playbook="\n".join(body), num_entries=len(results))

    return app


cli = typer.Typer()


@cli.command()
def serve(
    host: str = typer.Option("0.0.0.0"),
    port: int = typer.Option(8031),
    summaries_json: str = typer.Option("results/swectx_paper/lite_summaries_enriched.json"),
    skip_ingest: bool = typer.Option(False, "--skip-ingest"),
):
    api_key = os.environ.get("LITELLM_MASTER_KEY") or os.environ.get("OPENAI_API_KEY", "")
    api_base = os.environ.get("OPENAI_API_BASE", "http://localhost:4000/v1")
    if not api_key:
        sys.exit("Missing LITELLM_MASTER_KEY / OPENAI_API_KEY")
    # Mirror env vars into shapes langchain-openai expects
    os.environ["OPENAI_API_KEY"] = api_key
    os.environ["OPENAI_API_BASE"] = api_base
    os.environ["OPENAI_BASE_URL"] = api_base  # newer langchain-openai
    print(f"Building LangMem InMemoryStore", file=sys.stderr)
    store = build_store(api_key, api_base)
    if not skip_ingest:
        print(f"Ingesting summaries from {summaries_json}", file=sys.stderr)
        ingest_summaries(store, summaries_json)
    app = create_app(store)
    print(f"Listening on {host}:{port}", file=sys.stderr)
    uvicorn.run(app, host=host, port=port)


@cli.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        serve()


if __name__ == "__main__":
    cli()
