"""FastAPI server mimicking Agent-KB's API for A/B testing.

Provides a REST interface that SWE-agent can use instead of the Neo4j-based
QueryMemoryTool. This enables direct comparison between Agent-KB's hybrid
retrieval and ContextGraph's graph-based retrieval.

Usage:
    # Start server (requires built index)
    uv run python scripts/baselines/agentkb_server.py \
        --index-dir data/baselines/agentkb_index \
        --port 8001

    # Test
    curl -X POST http://localhost:8001/search \
        -H "Content-Type: application/json" \
        -d '{"query": "ImportError module not found", "top_k": 5}'

Endpoints:
    POST /search  - Search the knowledge base
    POST /add     - Add new experience (online learning)
    GET  /health  - Health check
    GET  /stats   - Knowledge base statistics
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

import typer
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Ensure scripts.baselines is importable when running directly
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts.baselines.agentkb_baseline import AgentKBRetriever

# ---------------------------------------------------------------------------
# Pydantic models for request/response
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    """Search request body."""
    query: str = Field(..., description="Query text to search for")
    top_k: int = Field(default=10, ge=1, le=100, description="Number of results")
    text_weight: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="TF-IDF weight override"
    )
    semantic_weight: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Semantic weight override"
    )
    error_type: Optional[str] = Field(
        default=None, description="Error type filter (for compatibility with ContextGraph API)"
    )


class SearchResult(BaseModel):
    """Single search result."""
    id: str
    text: str
    category: str
    source_type: str
    error_types: List[str]
    score: float
    tfidf_score: float
    semantic_score: float


class SearchResponse(BaseModel):
    """Search response body."""
    query: str
    results: List[SearchResult]
    elapsed_ms: float
    method: str = "agent_kb_hybrid"


class AddRequest(BaseModel):
    """Request to add a new experience record."""
    text: str = Field(..., description="Experience text to add")
    category: str = Field(default="OTHERS", description="Category/section")
    error_types: List[str] = Field(default_factory=list, description="Related error types")
    source_type: str = Field(default="OnlineLearning", description="Source type label")


class AddResponse(BaseModel):
    """Response after adding a record."""
    id: str
    success: bool
    total_records: int


class StatsResponse(BaseModel):
    """Knowledge base statistics."""
    total_records: int
    playbook_entries: int
    canonical_rules: int
    online_learned: int
    has_tfidf: bool
    has_embeddings: bool
    embedding_model: str
    text_weight: float
    semantic_weight: float


class MemoryQueryRequest(BaseModel):
    """SWE-agent compatible query format (mirrors QueryMemoryTool)."""
    problem_statement: Optional[str] = None
    error_message: Optional[str] = None
    current_file: Optional[str] = None
    query: Optional[str] = None


class MemoryQueryResponse(BaseModel):
    """SWE-agent compatible response format."""
    playbook: str
    num_entries: int
    retrieval_method: str = "agent_kb_hybrid"


# ---------------------------------------------------------------------------
# FastAPI app factory
# ---------------------------------------------------------------------------


def create_app(index_dir: str) -> FastAPI:
    """Create FastAPI app with loaded index."""
    app = FastAPI(
        title="Agent-KB Baseline Server",
        description="Agent-KB style hybrid retrieval for ContextGraph A/B comparison",
        version="1.0.0",
    )

    retriever = AgentKBRetriever()
    retriever.load_index(index_dir)
    _online_count = [0]  # mutable counter for online-learned records

    @app.get("/health")
    def health():
        return {"status": "ok", "records": len(retriever.records)}

    @app.get("/stats", response_model=StatsResponse)
    def stats():
        pb_count = sum(1 for r in retriever.records if r.get("source_type") == "PlaybookEntry")
        cr_count = sum(1 for r in retriever.records if r.get("source_type") == "CanonicalRule")
        return StatsResponse(
            total_records=len(retriever.records),
            playbook_entries=pb_count,
            canonical_rules=cr_count,
            online_learned=_online_count[0],
            has_tfidf=retriever.tfidf_matrix is not None,
            has_embeddings=retriever.embeddings is not None,
            embedding_model=retriever.embedding_model,
            text_weight=retriever.text_weight,
            semantic_weight=retriever.semantic_weight,
        )

    @app.post("/search", response_model=SearchResponse)
    def search(request: SearchRequest):
        t0 = time.time()

        results = retriever.query(
            query_text=request.query,
            top_k=request.top_k,
            text_weight=request.text_weight,
            semantic_weight=request.semantic_weight,
        )

        # If error_type filter is specified, boost results that match
        if request.error_type:
            for r in results:
                if request.error_type in r.get("error_types", []):
                    r["score"] *= 1.2  # 20% boost for error type match

            results.sort(key=lambda x: x["score"], reverse=True)

        elapsed_ms = (time.time() - t0) * 1000

        return SearchResponse(
            query=request.query,
            results=[SearchResult(**r) for r in results],
            elapsed_ms=elapsed_ms,
        )

    @app.post("/add", response_model=AddResponse)
    def add_record(request: AddRequest):
        """Add a new experience (online learning)."""
        record_id = f"online-{len(retriever.records):05d}"
        record = {
            "id": record_id,
            "text": request.text,
            "category": request.category,
            "source_type": request.source_type,
            "error_types": request.error_types,
            "prefix": "ol",
            "source_trajectories": [],
            "member_count": 0,
        }

        try:
            retriever.add_record(record)
            _online_count[0] += 1
            return AddResponse(
                id=record_id,
                success=True,
                total_records=len(retriever.records),
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/query_memory", response_model=MemoryQueryResponse)
    def query_memory(request: MemoryQueryRequest):
        """SWE-agent compatible endpoint (mirrors QueryMemoryTool interface).

        Accepts the same fields as our Neo4j-based QueryMemoryTool and returns
        formatted playbook text that can be injected into agent context.
        """
        # Build query from available fields
        parts = []
        if request.query:
            parts.append(request.query)
        if request.problem_statement:
            parts.append(request.problem_statement)
        if request.error_message:
            parts.append(f"Error: {request.error_message}")
        if request.current_file:
            parts.append(f"File: {request.current_file}")

        query_text = " ".join(parts) if parts else "general coding problem"

        results = retriever.query(query_text, top_k=10)

        # Format as playbook text (compatible with agent consumption)
        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"[{r['id']}] {r['text']}")

        playbook_text = "\n".join(lines) if lines else "No relevant experience found."

        return MemoryQueryResponse(
            playbook=playbook_text,
            num_entries=len(results),
        )

    return app


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

cli_app = typer.Typer(help="Agent-KB baseline FastAPI server.")


@cli_app.command()
def serve(
    index_dir: str = typer.Option(
        "data/baselines/agentkb_index",
        help="Directory containing the built index",
    ),
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(8001, help="Bind port"),
    reload: bool = typer.Option(False, help="Enable auto-reload"),
):
    """Start the Agent-KB baseline server."""
    typer.echo(f"Starting Agent-KB baseline server on {host}:{port}")
    typer.echo(f"Index directory: {index_dir}")

    app = create_app(index_dir)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    cli_app()
