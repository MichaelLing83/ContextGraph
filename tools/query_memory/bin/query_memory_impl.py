#!/usr/bin/env python3
"""SWE-agent tool: query memory for debugging strategies.

Supports two modes:
1. HTTP mode (MEMORY_SERVER_URL set) — calls external server, no dependencies
2. Direct Neo4j mode (NEO4J_URI set) — connects to Neo4j, needs neo4j driver

When AGENT_RERANK_ENABLED=1 and the server supports /query_memory_items,
an extra LLM rerank pass selects the top items using in-container context
(task, error, git diff snapshot). See lib/agent_rerank.py.

CLI contract: when invoked with the correct three positional arguments
(``<current_error> <task_description> <phase>``), this script exits 0
and prints exactly one result string to stdout. Errors (missing config,
backend unreachable, exceptions mid-call) are surfaced as
``ERROR: ...`` strings the SWE-agent tool layer forwards back to the
agent — never raised, never exit-non-zero. Bad CLI usage (missing
arguments) still exits non-zero with a usage message; that's a
programming error from the caller side, not a tool-level failure.

The earlier version of this script used ``sys.exit(1)`` on the
missing-NEO4J_PASSWORD path as well; that was removed in favour of the
consistent return-a-string contract that the HTTP path has always
used. If any downstream tooling parsed exit codes to detect that case,
switch it to grep ``^ERROR:`` on stdout.
"""

import json
import logging
import os
import sys
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)


def _ensure_tool_lib_on_path() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tool_root = os.path.dirname(script_dir)
    tool_lib = os.path.join(tool_root, "lib")
    if os.path.isdir(tool_lib) and tool_lib not in sys.path:
        sys.path.insert(0, tool_lib)


# Resolve `agent_rerank` once at import time. The dynamic-import-per-call
# pattern Sourcery flagged was wasteful on a hot tool, and the lib path
# must be set BEFORE the import resolves — once is enough.
_ensure_tool_lib_on_path()
try:
    import agent_rerank as _agent_rerank  # noqa: E402
except ImportError:
    _agent_rerank = None  # type: ignore[assignment]


def _post_json(
    url: str,
    payload: dict,
    *,
    timeout: int = 60,
    log_prefix: str = "POST",
) -> tuple[object | None, str | None]:
    """POST `payload` as JSON to `url`, decode the response as JSON.

    Returns (parsed_data, None) on success or (None, error_text) on any
    failure. Errors are logged at DEBUG and the returned error_text is
    suitable for inclusion in a user-facing ``ERROR: ...`` string.

    Common factor between `_query_items` and the legacy `/query_memory`
    call. Future timeout/header/logging tweaks land in one place.
    """
    body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            # urllib's default opener already converts 4xx/5xx into
            # HTTPError, but be defensive in case an alternate opener
            # surfaces the response object directly. Treat any non-2xx
            # status as an error so we never silently parse a server
            # error body as a valid JSON response.
            status = getattr(resp, "status", None) or getattr(resp, "code", 200)
            raw = resp.read()
            if not (200 <= status < 300):
                snippet = raw[:200].decode("utf-8", errors="replace")
                logger.debug(
                    "%s got HTTP %s at %s: %s", log_prefix, status, url, snippet,
                )
                return None, f"HTTP {status} from {url}"
            data = json.loads(raw.decode("utf-8"))
    except URLError as e:
        logger.debug("%s unreachable at %s: %s", log_prefix, url, e)
        return None, f"Could not reach {url}: {e}"
    except Exception as e:  # noqa: BLE001 — convert any transport / decode error to a string
        logger.debug("%s failed at %s (%s): %s", log_prefix, url, type(e).__name__, e)
        return None, f"{type(e).__name__} at {url}: {e}"
    return data, None


def _format_items_as_playbook(items: list, repo: str | None = None) -> str:
    """Render a list of MemoryItem dicts the way the server formats playbook
    text, so the rerank path is interchangeable with /query_memory output."""
    if not items:
        return ""
    lines = ["<memory_playbook>"]
    if repo:
        lines.append(
            "The following rules were retrieved from a memory of past coding "
            "fixes. Apply the REPO-SPECIFIC section literally (file paths, "
            "idioms). Use the GENERAL PATTERNS section as inspiration only — "
            "do NOT transplant code structure from other repositories."
        )
        lines.append("")
        in_repo = [i for i in items if i.get("section") == "REPO_SPECIFIC"]
        out_repo = [i for i in items if i.get("section") != "REPO_SPECIFIC"]
        if in_repo:
            lines.append(f"## REPO-SPECIFIC RULES ({repo})")
            for item in in_repo:
                lines.append(f"[{item.get('id', '?')}] {item.get('text', '')}")
            lines.append("")
        if out_repo:
            lines.append("## GENERAL PATTERNS (from other repos)")
            for item in out_repo:
                lines.append(f"[{item.get('id', '?')}] {item.get('text', '')}")
    else:
        lines.append("The following rules were retrieved from memory:")
        lines.append("")
        for item in items:
            lines.append(f"[{item.get('id', '?')}] {item.get('text', '')}")
    lines.append("</memory_playbook>")
    return "\n".join(lines)


def _query_items(
    server_url: str,
    payload: dict,
    k_in: int,
) -> tuple[list[dict], str | None] | None:
    """Hit /query_memory_items, request `top_k=k_in` candidates.

    Returns (items, repo) on success, or None if the endpoint isn't
    available / the server returned an error — the caller should fall
    back to the legacy /query_memory path.

    Failure reasons are surfaced at DEBUG so an operator running with
    `--log-level=DEBUG` can tell "the server has no matching rules" from
    "the items endpoint is misconfigured or down".
    """
    url = f"{server_url.rstrip('/')}/query_memory_items"
    body = dict(payload)
    body["top_k"] = k_in
    data, err = _post_json(url, body, log_prefix="items endpoint")
    if err is not None:
        return None
    if not isinstance(data, dict):
        logger.debug("Items endpoint returned non-dict %s at %s", type(data).__name__, url)
        return None
    items = data.get("items")
    if not isinstance(items, list):
        logger.debug("Items endpoint response missing 'items' list at %s; keys=%s", url, list(data.keys()))
        return None
    return items, data.get("repo")


def query_via_http(server_url: str, current_error: str, task_description: str, phase: str) -> str:
    payload = {
        "current_error": current_error,
        "task_description": task_description,
        "phase": phase,
    }

    # Rerank path: only kicks in when explicitly enabled AND the server
    # advertises a structured items endpoint. Either gate failure falls
    # back to the legacy /query_memory flow so existing experiments are
    # bit-stable when the env var is unset.
    if _agent_rerank is not None and _agent_rerank.is_enabled():
        k_in = _agent_rerank.get_default_k_in()
        k_out = _agent_rerank.get_default_k_out()
        fetched = _query_items(server_url, payload, k_in)
        if fetched is not None:
            items, repo = fetched
            if items:
                picked = _agent_rerank.rerank_items(
                    items,
                    task_description=task_description,
                    current_error=current_error,
                    k_out=k_out,
                    working_dir=os.getcwd(),
                )
                return _format_items_as_playbook(picked, repo=repo)
            # Empty items list — fall through to legacy path so the
            # caller still gets a clean "no rules" response.

    url = f"{server_url.rstrip('/')}/query_memory"
    data, err = _post_json(url, payload, log_prefix="legacy /query_memory")
    if err is not None:
        return f"ERROR: {err}"
    if isinstance(data, dict) and "playbook" in data and data["playbook"]:
        return data["playbook"]
    if isinstance(data, dict) and "result" in data:
        return data["result"]
    if isinstance(data, dict) and "strategies" in data:
        strategies = data["strategies"]
        lines = []
        for i, s in enumerate(strategies, 1):
            text = s.get("text", s.get("rule_text", str(s)))
            lines.append(f"{i}. {text}")
        return "\n".join(lines) if lines else "No relevant strategies found."
    # Unexpected response shape — don't dump the whole body into the
    # agent's prompt context. Log the full body at DEBUG for operators,
    # but only return a short snippet so a misconfigured server can't
    # leak large or sensitive payloads back to the model.
    raw = json.dumps(data) if not isinstance(data, str) else data
    snippet = raw[:200]
    logger.debug("Legacy /query_memory unexpected response shape: %s", raw)
    return f"ERROR: Memory server returned unexpected shape (snippet: {snippet})"




def query_via_neo4j(current_error: str, task_description: str, phase: str) -> str:
    # `_ensure_tool_lib_on_path()` already ran at module load, so the
    # lib/ dir is on sys.path; no need to re-invoke per call.

    # Importing AgentMemory transitively imports the neo4j driver, so a
    # missing-driver case will surface here as ImportError. No need for
    # a separate `import neo4j` presence check.
    try:
        from agent_memory import AgentMemory
        from agent_memory.evaluation.swe_agent_tool import QueryMemoryTool, QueryMemoryInput
    except (ImportError, SyntaxError) as exc:
        return f"ERROR: agent_memory unavailable (driver or lib missing): {exc}"

    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://host.docker.internal:7687")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "")
    if not neo4j_password:
        return "ERROR: NEO4J_PASSWORD not set"

    embedding_api_key = os.environ.get("LITELLM_MASTER_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    embedding_base_url = os.environ.get("OPENAI_API_BASE", "http://localhost:4000/v1")
    embedding_model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-large")
    rewriter_enabled = os.environ.get("REWRITER_ENABLED", "").lower() in ("1", "true", "yes")
    rewriter_api_base = os.environ.get("REWRITER_API_BASE", "http://localhost:4000/v1")
    rewriter_api_key = os.environ.get("REWRITER_API_KEY", "") or os.environ.get("LITELLM_MASTER_KEY", "")
    rewriter_model = os.environ.get("REWRITER_MODEL", "claude-sonnet-4-20250514")

    try:
        memory = AgentMemory(
            neo4j_uri=neo4j_uri,
            neo4j_auth=(neo4j_user, neo4j_password),
            embedding_api_key=embedding_api_key or None,
            embedding_base_url=embedding_base_url or None,
            embedding_model=embedding_model,
            rewriter_api_base=rewriter_api_base or None,
            rewriter_api_key=rewriter_api_key or None,
            rewriter_model=rewriter_model,
            rewriter_enabled=rewriter_enabled,
        )
    except Exception as exc:
        # Match the HTTP path's contract: always return a string, never
        # crash the CLI. The agent reads stdout as the tool result.
        return f"ERROR: Failed to open memory backend: {exc}"

    try:
        tool = QueryMemoryTool(memory)
        inp = QueryMemoryInput(current_error=current_error, task_description=task_description, phase=phase)
        output = tool.invoke(inp)
        try:
            return output.to_structured()
        except Exception:
            return output.to_json()
    except Exception as exc:
        # Same contract as above — return error text instead of letting
        # the exception propagate and bring down the agent's tool call.
        return f"ERROR: Memory query failed: {exc}"
    finally:
        memory.close()


def main():
    if len(sys.argv) < 4:
        print("Usage: query_memory <current_error> <task_description> <phase>")
        sys.exit(1)

    current_error = sys.argv[1]
    task_description = sys.argv[2]
    phase = sys.argv[3]

    server_url = os.environ.get("MEMORY_SERVER_URL", "")
    if server_url:
        result = query_via_http(server_url, current_error, task_description, phase)
    else:
        result = query_via_neo4j(current_error, task_description, phase)

    print(result)


if __name__ == "__main__":
    main()
