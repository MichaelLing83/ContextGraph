"""Hybrid contextgraph Pro server with repo-aware retrieval.

Returns two channels:
  - REPO-SPECIFIC: k_in entries filtered to the current repo (concrete file
    paths and idioms).
  - GENERAL PATTERNS: k_out entries from other repos (abstract patterns to
    use as inspiration only).

Endpoints:
  - POST /query_memory        — agent-facing: returns a pre-formatted
    ``playbook`` string ready to splice into a SWE-agent system prompt.
  - POST /query_memory_items  — orchestrator-facing: returns the same
    retrieval result as a list of structured ``MemoryItem`` objects (with
    ``repo`` / ``section`` / ``score`` fields). Intended for MeshMem-style
    memory control planes that merge / rerank across multiple backends.
  - GET  /health              — liveness + Neo4j connectivity probe.

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
from typing import List, Literal, Optional, Sequence, Tuple

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


MemoryItemSection = Literal["REPO_SPECIFIC", "GENERAL_PATTERN", "OTHERS"]


class MemoryItem(BaseModel):
    """Structured memory entry for the /query_memory_items endpoint.

    Designed for MeshMem-style consumers that want a list of memory objects
    rather than the prompt-ready `playbook` string returned by /query_memory.
    `text` retains the leading "[org/name]" prefix for verbatim agent display;
    `repo` is the same value parsed out as a queryable field.
    """
    id: str
    text: str
    repo: Optional[str] = None
    section: MemoryItemSection
    prefix: str
    score: Optional[float] = None


class MemoryItemsResponse(BaseModel):
    items: List[MemoryItem]
    num_entries: int
    retrieval_method: str = "contextgraph_hybrid"
    repo: Optional[str] = None


_REPO_PREFIX_RE = re.compile(r"^\s*\[([^\]]+)\]")


def _build_query_text(request: MemoryQueryRequest) -> str:
    """Concatenate the user-facing query fields into a single search string.

    Both /query_memory and /query_memory_items accept the same envelope of
    optional fields and join them in the same order. Centralising the logic
    here keeps the two endpoints in sync if the request shape evolves.

    Whitespace-only fields (e.g. accidental ``"   "``) are skipped and each
    surviving field is stripped, so the resulting text never carries
    incidental interior padding from blank fields between real content.
    """
    parts = [
        x.strip() for x in (request.error_message, request.current_error,
                            request.problem_statement,
                            request.task_description, request.query)
        if x and x.strip()
    ]
    return " ".join(parts)


def _extract_repo_from_text(text: str) -> Optional[str]:
    """Parse '[org/name] ...' prefix; return None if no slash inside brackets.

    Entries are stored as ``"[org/name] rule_text"``. Some legacy / non-repo
    entries may have a different leading bracket (e.g. category tag) — only
    return the value when it looks like a repo slug (contains '/').
    """
    if not text:
        return None
    m = _REPO_PREFIX_RE.match(text)
    if not m:
        return None
    candidate = m.group(1).strip()
    return candidate if "/" in candidate else None


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


# Oversample multipliers for the two-channel vector search. The in-repo
# (match=True) channel oversamples more aggressively because the post-filter
# `WHERE node.text STARTS WITH $prefix` cuts most rows; the out-of-repo
# channel keeps a smaller buffer since its post-filter is the complement and
# only removes ~1/N of candidates.
_OVERSAMPLE_IN_REPO = 20
_OVERSAMPLE_OUT_OF_REPO = 5


def _filtered_vector_query(
    store, embedding: List[float], top_k: int,
    repo: Optional[str], match_repo: bool,
) -> List[Tuple[PlaybookEntry, float]]:
    """Run a single vector search constrained by repo prefix equality / inequality.

    PlaybookEntry text is stored as ``"[repo/name] rule_text"`` so we filter on
    the leading bracket prefix instead of a separate column. Returns
    ``[(entry, cosine_score), ...]``. The two public wrappers
    (``cypher_filtered_search``, ``cypher_filtered_search_with_scores``)
    project this output for their respective callers.

    Returns ``[]`` immediately when ``repo`` is ``None`` or whitespace-only.
    Without this guard an empty repo would build ``prefix="[]"`` and run a
    confusing-but-silent ``STARTS WITH '[]'`` filter on the index. Centralising
    the check here means both wrappers get the same protection without each
    re-implementing it.
    """
    if repo is None or not repo.strip():
        return []
    where_clause = (
        "WHERE node.text STARTS WITH $prefix" if match_repo
        else "WHERE NOT node.text STARTS WITH $prefix"
    )
    oversample_mult = (
        _OVERSAMPLE_IN_REPO if match_repo else _OVERSAMPLE_OUT_OF_REPO
    )
    query = f"""
    CALL db.index.vector.queryNodes('playbook_embedding', $oversample, $embedding)
    YIELD node, score
    {where_clause}
    RETURN node{{.*}} AS p, score
    ORDER BY score DESC
    LIMIT $k
    """
    params = {
        "oversample": top_k * oversample_mult,
        "embedding": embedding,
        "prefix": f"[{repo}]",
        "k": top_k,
    }
    rows = store.execute_query(query, params)
    out: List[Tuple[PlaybookEntry, float]] = []
    for row in rows:
        p = row["p"]
        entry = PlaybookEntry(
            id=p.get("id", ""), prefix=p.get("prefix", "misc"),
            section=p.get("section", "OTHERS"), text=p.get("text", ""),
        )
        out.append((entry, float(row["score"])))
    return out


def cypher_filtered_search(
    store, embedding: List[float], top_k: int,
    repo: Optional[str], match_repo: bool,
) -> List[PlaybookEntry]:
    """Vector search constrained by source_repo equality / inequality.

    Thin wrapper around :func:`_filtered_vector_query` that discards scores.
    Used by /query_memory, which only renders entry text into the playbook
    string and never reads the cosine score. ``repo=None`` (or empty) is
    handled by the underlying helper.
    """
    return [entry for entry, _score in
            _filtered_vector_query(store, embedding, top_k, repo, match_repo)]


def cypher_filtered_search_with_scores(
    store, embedding: List[float], top_k: int,
    repo: Optional[str], match_repo: bool,
) -> List[Tuple[PlaybookEntry, float]]:
    """Vector search that preserves cosine scores.

    Thin wrapper around :func:`_filtered_vector_query`. Used by
    /query_memory_items where MeshMem-style consumers want the score as a
    queryable metadata field. ``repo=None`` (or empty) is handled by the
    underlying helper.
    """
    return _filtered_vector_query(store, embedding, top_k, repo, match_repo)


def _entries_to_items(
    scored: Sequence[Tuple[PlaybookEntry, Optional[float]]],
    section: MemoryItemSection,
) -> List[MemoryItem]:
    """Map ``(PlaybookEntry, score)`` pairs to ``MemoryItem`` instances.

    ``section`` is typed as :data:`MemoryItemSection` so call sites that pass
    a stale or misspelled label fail at type-check time rather than silently
    sneaking through Pydantic validation. ``score`` is ``Optional`` to share
    one mapping path between the Cypher branch (real scores) and the
    no-repo fallback branch (no scores available).
    """
    return [
        MemoryItem(
            id=entry.id,
            text=entry.text,
            repo=_extract_repo_from_text(entry.text),
            section=section,
            prefix=entry.prefix or "misc",
            score=score,
        )
        for entry, score in scored
    ]


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
        query_text = _build_query_text(request)
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

    @app.post("/query_memory_items", response_model=MemoryItemsResponse)
    def query_memory_items(request: MemoryQueryRequest):
        """Structured-list variant of /query_memory for MeshMem-style consumers.

        Same query / scope inference as /query_memory, but returns a list of
        ``MemoryItem`` (with ``repo`` / ``section`` / ``score`` fields) instead
        of a pre-formatted playbook string. Useful for orchestrators that need
        to merge / rerank / filter entries across multiple memory backends.
        """
        query_text = _build_query_text(request)
        if not query_text:
            return MemoryItemsResponse(items=[], num_entries=0, repo=None)

        repo = infer_repo(request)
        embedding = memory.playbook_retriever.embedder.embed(query_text)
        items: List[MemoryItem] = []

        if repo:
            in_scored = cypher_filtered_search_with_scores(
                store, embedding, k_in, repo, match_repo=True,
            )
            out_scored = cypher_filtered_search_with_scores(
                store, embedding, k_out, repo, match_repo=False,
            )
            items.extend(_entries_to_items(in_scored, "REPO_SPECIFIC"))
            items.extend(_entries_to_items(out_scored, "GENERAL_PATTERN"))
        else:
            # No repo could be inferred — fall back to non-filtered top-k.
            # PlaybookRetriever.retrieve does not expose per-entry scores, so
            # we pair each entry with None and route through the same
            # _entries_to_items mapping the Cypher branch uses. section
            # "OTHERS" signals "neither repo-specific nor cross-repo by
            # design".
            fallback = memory.playbook_retriever.retrieve(
                query_text, query_embedding=embedding, top_k=k_in + k_out,
            )
            items.extend(_entries_to_items(
                [(entry, None) for entry in fallback], "OTHERS",
            ))

        return MemoryItemsResponse(
            items=items, num_entries=len(items), repo=repo,
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
