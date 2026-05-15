"""Agent-side LLM rerank for memory query results.

The graph retrieval pipeline returns K candidate items. This module asks an
LLM to re-rank those K items down to a smaller k that's actually returned to
the calling coding agent, using more in-container context (current task,
current error, recent git diff) than the embedding/BM25/PPR channels can.

Design notes:
  * Pure stdlib — `urllib.request` only, so the module runs inside the
    SWE-agent Docker container without pulling in `openai` or `requests`.
  * The LLM is reached via the same OpenAI-compatible endpoint the rest of
    the tool already uses (`$OPENAI_API_BASE` + `$LITELLM_MASTER_KEY` /
    `$OPENAI_API_KEY`).
  * Failures fall back to "return the top k by original score, untouched"
    — this is a re-ranker, never a gatekeeper. Memory must still be useful
    if the rerank LLM is down or rate-limited.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from typing import Iterable, List, Optional, Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


# How many candidates to pull from the server before the LLM rerank.
# k_out (the count actually returned to the calling agent) is a separate
# knob — typically smaller, e.g. 3.
DEFAULT_K_IN = 10
DEFAULT_K_OUT = 3

# Cap the working-context snippet we attach to the rerank prompt. The agent
# may be sitting on a multi-MB diff; we just want a signal of *where* it's
# editing, not the full diff.
WORKING_CONTEXT_CHAR_BUDGET = 4_000

_RERANK_TIMEOUT_S = 90

# Default subprocess timeout for the `git diff --stat HEAD` snapshot.
# Overridable via $AGENT_RERANK_GIT_TIMEOUT_S so large/slow repos can
# extend it and laptop disks can shrink it. The snapshot result is
# cached per (cwd) within a process so we don't keep paying this cost
# on hot paths.
_DEFAULT_GIT_TIMEOUT_S = 5
_git_diff_cache: dict[str, str] = {}


def _llm_endpoint() -> Optional[tuple[str, str, str]]:
    """Return (base_url, api_key, model) or None if rerank is unconfigured.

    Falls back through the same env vars the existing tool already uses, so
    the operator doesn't need to set extra knobs for the common case.
    """
    base_url = os.environ.get("OPENAI_API_BASE", "").rstrip("/")
    api_key = (
        os.environ.get("RERANK_API_KEY")
        or os.environ.get("LITELLM_MASTER_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    )
    model = os.environ.get("RERANK_MODEL") or os.environ.get("REWRITER_MODEL") or ""
    if not base_url or not api_key or not model:
        return None
    return base_url, api_key, model


def _capture_git_diff_stat(working_dir: Optional[str] = None) -> str:
    """Best-effort `git diff --stat HEAD` snapshot to feed the reranker.

    Returns "" if the working dir isn't a git repo or git isn't on PATH.
    Truncates to WORKING_CONTEXT_CHAR_BUDGET so a runaway diff can't blow
    the rerank prompt. Cached per cwd within the process so back-to-back
    rerank calls (multiple ``query_memory`` invocations in one agent
    session) don't keep paying the subprocess cost. Cache is invalidated
    by process restart — fine for a single SWE-agent run, where each
    instance gets a fresh container.
    """
    cache_key = working_dir or os.getcwd()
    cached = _git_diff_cache.get(cache_key)
    if cached is not None:
        return cached
    timeout_s = _read_positive_int_env(
        "AGENT_RERANK_GIT_TIMEOUT_S", _DEFAULT_GIT_TIMEOUT_S,
    )
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=working_dir,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        _git_diff_cache[cache_key] = ""
        return ""
    if result.returncode != 0:
        _git_diff_cache[cache_key] = ""
        return ""
    out = result.stdout.strip()
    if len(out) > WORKING_CONTEXT_CHAR_BUDGET:
        out = out[:WORKING_CONTEXT_CHAR_BUDGET] + "\n…[truncated]"
    _git_diff_cache[cache_key] = out
    return out


def invalidate_git_diff_cache() -> None:
    """Drop the cached `git diff --stat` output. Mostly for tests.

    Production callers that hit this path want the cache (one snapshot
    per agent run is intentional); tests need a fresh capture per
    assertion. Public to keep the test surface explicit.
    """
    _git_diff_cache.clear()


def _build_rerank_prompt(
    items: Sequence[dict],
    task_description: str,
    current_error: str,
    k_out: int,
    working_context: str,
) -> str:
    """Render the system+user prompt as one string for chat.completions."""
    item_lines = []
    for idx, item in enumerate(items):
        # `text` already includes the "[repo/name] rule" prefix from the
        # server. Truncate at 800 chars per item — the rules in the graph
        # are usually short, but the occasional long one shouldn't dominate.
        text = (item.get("text") or "").strip()
        if len(text) > 800:
            text = text[:800] + "…"
        section = item.get("section") or "OTHERS"
        item_lines.append(f"[{idx}] ({section}) {text}")
    items_block = "\n".join(item_lines)

    return (
        "You are re-ranking memory items for a coding agent. The agent is "
        "stuck on a real bug and has been given the candidate items below. "
        f"Pick UP TO {k_out} distinct items the agent should read first — "
        "the ones most likely to point at the right fix for THIS task and "
        "THIS error. Prefer items whose advice is concrete and matches the "
        "current working state. Drop generic patterns when a repo-specific "
        "item covers the same ground.\n\n"
        f"## Task\n{task_description.strip() or '(no task description provided)'}\n\n"
        f"## Current error\n{current_error.strip() or '(no error provided)'}\n\n"
        f"## Agent's current working state (git diff --stat HEAD)\n"
        f"{working_context or '(no git context)'}\n\n"
        f"## Candidate items ({len(items)} total)\n{items_block}\n\n"
        "Respond ONLY with a JSON object of the form "
        '{"picks": [<int>, <int>, …]} — the array contains the distinct '
        f"candidate indices you chose, in priority order, no other keys, "
        f"no markdown. Return at most {k_out}; return fewer if not that "
        "many items are genuinely useful (do not pad with duplicates or "
        "low-quality picks)."
    )


def _call_chat_completion(
    prompt: str,
    base_url: str,
    api_key: str,
    model: str,
) -> Optional[str]:
    url = f"{base_url}/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        # Bounded so a misbehaving model can't run away — the response is a
        # short JSON object.
        "max_tokens": 512,
    }).encode("utf-8")
    req = Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    # Rerank stays "never a gatekeeper": any failure here returns None
    # and lets the caller fall back to top-k. Log levels are split:
    #   - Transient network/decode noise (URLError, TimeoutError,
    #     UnicodeDecodeError, OSError-derivatives, JSONDecodeError) →
    #     DEBUG. These get noisy under proxy hiccups and we already
    #     have a working fallback.
    #   - Anything else → WARNING (something programmer-side might
    #     want to look at).
    try:
        with urlopen(req, timeout=_RERANK_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, UnicodeError, OSError) as e:
        logger.debug("Rerank LLM transient error (%s): %s", type(e).__name__, e)
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("Rerank LLM unexpected error (%s): %s", type(e).__name__, e)
        return None
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.debug("Rerank LLM response missing choices: %s", data)
        return None


_JSON_BLOCK_RE = re.compile(r"\{[^{}]*\"picks\"\s*:\s*\[[^\]]*\][^{}]*\}", re.S)


def _parse_picks(content: str, max_index: int, k_out: int) -> Optional[List[int]]:
    """Parse the model's JSON answer; clamp to valid indices, dedupe."""
    if not content:
        return None
    # Try direct JSON first; fall back to substring match for models that
    # wrap their answer in prose / markdown.
    parsed = None
    for candidate in (content.strip(), *_JSON_BLOCK_RE.findall(content)):
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(parsed, dict):
        return None
    raw_picks = parsed.get("picks")
    if not isinstance(raw_picks, list):
        return None
    seen: List[int] = []
    for value in raw_picks:
        if not isinstance(value, int):
            continue
        if value < 0 or value > max_index:
            continue
        if value in seen:
            continue
        seen.append(value)
        if len(seen) >= k_out:
            break
    return seen or None


def rerank_items(
    items: Sequence[dict],
    *,
    task_description: str,
    current_error: str,
    k_out: int = DEFAULT_K_OUT,
    working_dir: Optional[str] = None,
    working_context: Optional[str] = None,
) -> List[dict]:
    """Return up to ``k_out`` items chosen by the rerank LLM.

    Always returns a list (never raises). On any failure — missing config,
    network error, unparseable response — falls back to the leading
    ``k_out`` items in the input order, which preserves the server-side
    ranking the caller already got.
    """
    if not items:
        return []
    if k_out <= 0:
        return []
    # Single-item input has nothing to reorder; skip the LLM call.
    # Otherwise *always* invoke the rerank when enabled — even if
    # len(items) <= k_out, the model can still reorder by relevance,
    # which is most of the value here. Truncation is the secondary
    # benefit; reordering is the primary one.
    if len(items) <= 1:
        return list(items)

    endpoint = _llm_endpoint()
    if endpoint is None:
        logger.debug("Agent rerank disabled: endpoint config missing")
        return list(items[:k_out])

    if working_context is None:
        working_context = _capture_git_diff_stat(working_dir)

    prompt = _build_rerank_prompt(
        items=items,
        task_description=task_description,
        current_error=current_error,
        k_out=k_out,
        working_context=working_context,
    )
    base_url, api_key, model = endpoint
    content = _call_chat_completion(prompt, base_url, api_key, model)
    picks = _parse_picks(content or "", max_index=len(items) - 1, k_out=k_out)
    if not picks:
        # The underlying reason (network failure, malformed response,
        # picks list parsed empty) is already logged at DEBUG inside the
        # helper. This message just confirms the fallback path was
        # taken — repeating it at WARNING was redundant noise when a
        # rerank endpoint is briefly flaky.
        logger.debug(
            "Agent rerank produced no usable picks; falling back to top-k "
            "by server score (reason logged separately).",
        )
        return list(items[:k_out])
    return [items[i] for i in picks]


def is_enabled() -> bool:
    """Cheap check the impl layer uses to gate the rerank path."""
    val = os.environ.get("AGENT_RERANK_ENABLED", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def get_default_k_in() -> int:
    return _read_positive_int_env("AGENT_RERANK_K_IN", DEFAULT_K_IN)


def get_default_k_out() -> int:
    return _read_positive_int_env("AGENT_RERANK_K_OUT", DEFAULT_K_OUT)


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default
