"""Hybrid contextgraph Pro server with repo-aware retrieval.

Returns two channels:
  - REPO-SPECIFIC: k_in entries filtered to the current repo (concrete file
    paths and idioms).
  - GENERAL PATTERNS: k_out entries from other repos (abstract patterns to
    use as inspiration only).

Usage:
    NEO4J_URI=bolt://localhost:7693 uv run python \\
        scripts/baselines/contextgraph_pro_server.py --port 8010 \\
        --k-in 3 --k-out 2
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence

import typer
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from agent_memory import AgentMemory
from agent_memory.models import PlaybookEntry


# Canonical Pro repos (used to extract repo hint from problem text).
PRO_REPOS = [
    "ansible/ansible", "internetarchive/openlibrary", "flipt-io/flipt",
    "qutebrowser/qutebrowser", "gravitational/teleport",
    "protonmail/webclients", "future-architect/vuls",
    "element-hq/element-web", "navidrome/navidrome",
    "NodeBB/NodeBB", "tutao/tutanota",
]


class MemoryQueryRequest(BaseModel):
    problem_statement: Optional[str] = None
    error_message: Optional[str] = None
    current_file: Optional[str] = None
    query: Optional[str] = None
    current_error: Optional[str] = None
    task_description: Optional[str] = None
    phase: Optional[str] = None
    repo: Optional[str] = None
    instance_id: Optional[str] = None


class MemoryQueryResponse(BaseModel):
    playbook: str
    num_entries: int
    retrieval_method: str = "contextgraph_pro_hybrid"
    repo: Optional[str] = None


def infer_repo(request: MemoryQueryRequest) -> Optional[str]:
    """Resolve current repo from (in order) request.repo, instance_id, text."""
    if request.repo:
        req = request.repo.strip().lower()
        # Exact "org/name" match first.
        for r in PRO_REPOS:
            if req == r.lower():
                return r
        # Then exact bare-name match against the repo half of "org/name".
        for r in PRO_REPOS:
            if req == r.split("/", 1)[1].lower():
                return r
        # Caller supplied something we can't normalise — return as-is.
        return request.repo
    if request.instance_id:
        m = re.match(r"instance_([^_]+)__([^-]+)", request.instance_id)
        if m:
            cand = f"{m.group(1)}/{m.group(2)}"
            for r in PRO_REPOS:
                if cand.lower() == r.lower():
                    return r
    blob = " ".join(
        x for x in (request.problem_statement, request.task_description,
                    request.error_message, request.current_error, request.query)
        if x
    ).lower()
    # Score-based match to avoid substring false positives:
    #   3 = exact "org/name"; 2 = repo name as whole word; 1 = "org/" prefix
    best_match: Optional[str] = None
    best_score: int = 0
    for r in PRO_REPOS:
        org, name = r.split("/")
        org_l, name_l = org.lower(), name.lower()
        full_l = f"{org_l}/{name_l}"
        if full_l in blob:
            score = 3
        elif re.search(r"\b" + re.escape(name_l) + r"\b", blob):
            score = 2
        elif f"{org_l}/" in blob:
            score = 1
        else:
            score = 0
        if score > best_score:
            best_score = score
            best_match = r
    return best_match


def cypher_filtered_search(
    store, embedding: List[float], top_k: int, repo: Optional[str], match_repo: bool
) -> List[PlaybookEntry]:
    """Vector search constrained by source_repo equality / inequality.

    PlaybookEntry text is stored as ``"[repo/name] rule_text"`` so we filter on
    the leading bracket prefix instead of a separate column.
    """
    if repo is None:
        return []
    if match_repo:
        query = """
        CALL db.index.vector.queryNodes('playbook_embedding', $oversample, $embedding)
        YIELD node, score
        WHERE node.text STARTS WITH $prefix
        RETURN node{.*} AS p, score
        ORDER BY score DESC
        LIMIT $k
        """
        params = {"oversample": top_k * 20, "embedding": embedding,
                  "prefix": f"[{repo}]", "k": top_k}
    else:
        query = """
        CALL db.index.vector.queryNodes('playbook_embedding', $oversample, $embedding)
        YIELD node, score
        WHERE NOT node.text STARTS WITH $prefix
        RETURN node{.*} AS p, score
        ORDER BY score DESC
        LIMIT $k
        """
        params = {"oversample": top_k * 5, "embedding": embedding,
                  "prefix": f"[{repo}]", "k": top_k}
    rows = store.execute_query(query, params)
    out: List[PlaybookEntry] = []
    for row in rows:
        p = row["p"]
        out.append(PlaybookEntry(
            id=p.get("id", ""), prefix=p.get("prefix", "misc"),
            section=p.get("section", "OTHERS"), text=p.get("text", ""),
        ))
    return out


def format_hybrid(
    in_entries: Sequence[PlaybookEntry],
    out_entries: Sequence[PlaybookEntry],
    repo: Optional[str],
) -> str:
    parts = ["<memory_playbook>"]
    parts.append(
        "The following rules were retrieved from a memory of past coding fixes. "
        "Apply the REPO-SPECIFIC section literally (file paths, idioms). "
        "Use the GENERAL PATTERNS section as inspiration only — do NOT transplant "
        "code structure from other repositories."
    )
    if in_entries:
        # When `repo` is None the in_entries channel is filled from the generic
        # top-k fallback, so it is not actually repo-specific. Use a neutral
        # header in that case to avoid misleading downstream consumers.
        header = f"REPO-SPECIFIC RULES ({repo})" if repo else "GENERAL RULES"
        parts.append(f"\n## {header}")
        for e in in_entries:
            parts.append(f"[{e.id}] {e.text}")
    if out_entries:
        parts.append("\n## GENERAL PATTERNS (from other repos)")
        for e in out_entries:
            parts.append(f"[{e.id}] {e.text}")
    parts.append("</memory_playbook>")
    return "\n".join(parts)


def create_app(k_in: int, k_out: int) -> FastAPI:
    app = FastAPI(title="ContextGraph Pro Hybrid Server", version="2.0.0")

    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7693")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "contextgraph123")
    api_key = (os.environ.get("LITELLM_MASTER_KEY", "")
               or os.environ.get("OPENAI_API_KEY", ""))
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
    store = memory.playbook_retriever.store

    @app.get("/health")
    def health():
        # Avoid leaking credentials from neo4j_uri (may be bolt://user:pass@host).
        # Probe connectivity without echoing the URI.
        try:
            store.execute_query("RETURN 1 AS ok LIMIT 1")
            neo4j_connected = True
        except Exception:
            neo4j_connected = False
        return {"status": "ok", "neo4j_connected": neo4j_connected,
                "mode": "hybrid", "k_in": k_in, "k_out": k_out}

    @app.post("/query_memory", response_model=MemoryQueryResponse)
    def query_memory(request: MemoryQueryRequest):
        parts = [
            x for x in (request.error_message, request.current_error,
                        request.problem_statement, request.task_description,
                        request.query)
            if x
        ]
        query_text = " ".join(parts).strip()
        if not query_text:
            return MemoryQueryResponse(playbook="", num_entries=0, repo=None)

        repo = infer_repo(request)
        embedding = memory.playbook_retriever.embedder.embed(query_text)

        if repo:
            in_entries = cypher_filtered_search(
                store, embedding, k_in, repo, match_repo=True
            )
            out_entries = cypher_filtered_search(
                store, embedding, k_out, repo, match_repo=False
            )
        else:
            in_entries = memory.playbook_retriever.retrieve(
                query_text, query_embedding=embedding, top_k=k_in + k_out,
            )
            out_entries = []

        playbook = format_hybrid(in_entries, out_entries, repo)
        return MemoryQueryResponse(
            playbook=playbook,
            num_entries=len(in_entries) + len(out_entries),
            repo=repo,
        )

    return app


cli = typer.Typer()


@cli.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    port: int = typer.Option(8010, help="Bind port"),
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    k_in: int = typer.Option(3, help="In-repo top-k"),
    k_out: int = typer.Option(2, help="Out-of-repo top-k"),
):
    if ctx.invoked_subcommand is None:
        app = create_app(k_in=k_in, k_out=k_out)
        uvicorn.run(app, host=host, port=port)


@cli.command()
def serve(
    port: int = typer.Option(8010, help="Bind port"),
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    k_in: int = typer.Option(3, help="In-repo top-k"),
    k_out: int = typer.Option(2, help="Out-of-repo top-k"),
):
    app = create_app(k_in=k_in, k_out=k_out)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    cli()
