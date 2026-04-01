"""MCP server exposing ContextGraph agent memory to any MCP-compatible client.

Run from the project root so that ``agent_memory`` is importable via the
editable install (``uv pip install -e .``).  No ``sys.path`` manipulation
is needed when the package is installed properly.
"""

import json
import logging
import os
import re
import threading

from mcp.server.fastmcp import FastMCP
from openai import OpenAI

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
_init_lock = threading.Lock()

_VALID_PHASES = {"exploring", "understanding", "locating", "fixing", "verifying", "testing"}

# LLM filter model — small and fast
_FILTER_MODEL = os.environ.get("FILTER_MODEL", "gpt-4o-mini")

# Maximum characters for structured memory output (truncated at rule boundary)
_MAX_MEMORY_CHARS = int(os.environ.get("MAX_MEMORY_CHARS", "1500"))


def _get_filter_client() -> OpenAI | None:
    """Return an OpenAI client for the relevance filter, or None if unavailable."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_API_BASE") or "https://api.chatanywhere.org"
    if not base_url.rstrip("/").endswith("/v1"):
        base_url = base_url.rstrip("/") + "/v1"
    if not api_key:
        return None
    return OpenAI(api_key=api_key, base_url=base_url)


def _parse_rules_from_playbook(playbook_text: str) -> list[tuple[str, str]]:
    """Extract (rule_id, rule_text) pairs from playbook text."""
    rules: list[tuple[str, str]] = []
    current_id: str | None = None
    current_lines: list[str] = []

    for line in playbook_text.splitlines():
        stripped = line.strip()
        m = re.match(r"^\[([a-z]+-\d+)\]\s*(.*)", stripped)
        if m:
            if current_id is not None:
                rules.append((current_id, " ".join(current_lines).strip()))
            current_id = m.group(1)
            current_lines = [m.group(2)] if m.group(2) else []
            continue
        if stripped.startswith("##"):
            if current_id is not None:
                rules.append((current_id, " ".join(current_lines).strip()))
                current_id = None
                current_lines = []
            continue
        if current_id and stripped:
            current_lines.append(stripped)

    if current_id is not None:
        rules.append((current_id, " ".join(current_lines).strip()))
    return rules


def _filter_items_with_llm(
    task_description: str,
    current_error: str,
    items: list[tuple[str, str]],
) -> list[str]:
    """Ask a small LLM which memory items are relevant.

    *items* is a list of ``(item_id, item_text)`` pairs where *item_id*
    uses the namespaced scheme produced by ``_build_all_items_for_filter``
    (e.g. ``playbook:shr-00001``, ``strategy:2``, ``experience:0``,
    ``problem:3``).

    Returns the list of *item_id* values the LLM considers relevant.
    """
    if not items:
        return []

    client = _get_filter_client()
    if client is None:
        logger.warning("No OpenAI client for LLM filter; keeping all items")
        return [r[0] for r in items]

    item_lines = []
    for item_id, item_text in items:
        text = item_text[:300] + "..." if len(item_text) > 300 else item_text
        item_lines.append(f"[{item_id}] {text}")
    items_block = "\n".join(item_lines)

    prompt = f"""You are a relevance filter for a coding agent's memory system. Given a bug report and a list of items (playbook rules, strategies, past experiences, similar problems) retrieved from memory, decide which ones might be useful for solving this specific bug.

## Bug Report
**Error:** {current_error[:500]}
**Task:** {task_description[:500]}

## Items
{items_block}

## Instructions
Return a JSON array of item IDs that could plausibly help an agent working on this bug. Be inclusive — keep items that offer general debugging wisdom applicable to this kind of problem, not just exact matches. Remove only items that are clearly about an unrelated domain or technology. Aim to keep 2-5 items.

Respond with ONLY a JSON array, e.g.: ["playbook:shr-00001", "strategy:2", "experience:0"] or []"""

    try:
        response = client.chat.completions.create(
            model=_FILTER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=256,
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        kept_ids = json.loads(content)
        if not isinstance(kept_ids, list):
            logger.warning("LLM filter returned non-list: %s; keeping all", content)
            return [r[0] for r in items]
        return [str(rid) for rid in kept_ids]
    except Exception as e:
        logger.warning("LLM filter failed (%s); keeping all items", e)
        return [r[0] for r in items]


def _rebuild_playbook_text(playbook_text: str, kept_ids: set[str]) -> str:
    """Rebuild playbook text keeping only rules whose IDs are in kept_ids."""
    if not kept_ids:
        return ""

    output_lines: list[str] = []
    current_section_header: str | None = None
    in_kept_rule = False

    for line in playbook_text.splitlines():
        stripped = line.strip()

        if stripped.startswith("##"):
            current_section_header = line
            in_kept_rule = False
            continue

        m = re.match(r"^\[([a-z]+-\d+)\]", stripped)
        if m:
            rule_id = m.group(1)
            if rule_id in kept_ids:
                if current_section_header:
                    output_lines.append(current_section_header)
                    current_section_header = None
                output_lines.append(line)
                in_kept_rule = True
            else:
                in_kept_rule = False
            continue

        if in_kept_rule and stripped:
            output_lines.append(line)

    result = "\n".join(output_lines).strip()
    if not result:
        return ""

    if "<memory_playbook>" in playbook_text:
        return (
            "<memory_playbook>\n"
            "The following rules were learned from solving similar coding "
            "problems in the past.\nApply relevant rules to your current task.\n\n"
            f"{result}\n"
            "</memory_playbook>"
        )
    return result


def _build_all_items_for_filter(output) -> list[tuple[str, str]]:
    """Build a unified list of (item_id, item_text) from all output sections."""
    items: list[tuple[str, str]] = []

    # Playbook rules
    if output.playbook_text:
        for rule_id, rule_text in _parse_rules_from_playbook(output.playbook_text):
            items.append((f"playbook:{rule_id}", rule_text))

    # Strategies
    for i, s in enumerate(output.strategies):
        items.append((f"strategy:{i}", s.rule_text))

    # Similar experiences
    for i, e in enumerate(output.similar_experiences):
        items.append((f"experience:{i}", e.resolution))

    # Similar problems
    for i, p in enumerate(output.similar_problems):
        items.append((f"problem:{i}", p.summary))

    return items


def _apply_llm_relevance_filter(
    output, task_description: str, current_error: str
) -> None:
    """Filter all output sections using LLM relevance judgement (in-place)."""
    items = _build_all_items_for_filter(output)
    if not items:
        return

    logger.info("LLM filter: evaluating %d total items", len(items))
    kept_ids = _filter_items_with_llm(task_description, current_error, items)
    kept_set = set(kept_ids)
    logger.info(
        "LLM filter: kept %d/%d items (removed %d)",
        len(kept_set), len(items), len(items) - len(kept_set),
    )

    # Filter playbook text
    if output.playbook_text:
        playbook_kept = {
            item_id.split(":", 1)[1]
            for item_id in kept_set
            if item_id.startswith("playbook:")
        }
        rules = _parse_rules_from_playbook(output.playbook_text)
        if rules and not playbook_kept:
            output.playbook_text = ""
        elif rules and len(playbook_kept) < len(rules):
            output.playbook_text = _rebuild_playbook_text(
                output.playbook_text, playbook_kept
            )

    # Filter strategies
    kept_strategy_indices = {
        int(item_id.split(":")[1])
        for item_id in kept_set
        if item_id.startswith("strategy:")
    }
    if output.strategies:
        output.strategies = [
            s for i, s in enumerate(output.strategies)
            if i in kept_strategy_indices
        ]

    # Filter experiences
    kept_exp_indices = {
        int(item_id.split(":")[1])
        for item_id in kept_set
        if item_id.startswith("experience:")
    }
    if output.similar_experiences:
        output.similar_experiences = [
            e for i, e in enumerate(output.similar_experiences)
            if i in kept_exp_indices
        ]

    # Filter similar problems
    kept_prob_indices = {
        int(item_id.split(":")[1])
        for item_id in kept_set
        if item_id.startswith("problem:")
    }
    if output.similar_problems:
        output.similar_problems = [
            p for i, p in enumerate(output.similar_problems)
            if i in kept_prob_indices
        ]


def _get_tool() -> QueryMemoryTool:
    global _memory, _tool
    if _tool is not None:
        return _tool

    with _init_lock:
        if _tool is not None:
            return _tool

        neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
        neo4j_password = os.environ.get("NEO4J_PASSWORD", "")
        embedding_api_key = os.environ.get("OPENAI_API_KEY", "")
        embedding_base_url = os.environ.get("OPENAI_API_BASE") or None
        embedding_model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-large")

        _check_required_env()

        _memory = AgentMemory(
            neo4j_uri=neo4j_uri,
            neo4j_auth=(neo4j_user, neo4j_password),
            embedding_api_key=embedding_api_key,
            embedding_base_url=embedding_base_url,
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

    The return value is an XML-formatted string (token-efficient for LLMs).

    Args:
        current_error: The error message or traceback currently being debugged.
        task_description: Brief description of the current task or issue.
        phase: Current phase of problem solving.
            One of: exploring, understanding, locating, fixing, verifying, testing.
    """
    phase = phase.lower()
    if phase not in _VALID_PHASES:
        logger.warning("Invalid phase %r, defaulting to 'fixing'", phase)
        phase = "fixing"
    tool = _get_tool()
    input_data = QueryMemoryInput(
        current_error=current_error,
        task_description=task_description,
        phase=phase,
    )
    output = tool.invoke(input_data)

    # LLM relevance filter: ask a small model to judge which playbook
    # rules are actually relevant to this specific bug, removing noise.
    _apply_llm_relevance_filter(output, task_description, current_error)

    # If memory has nothing relevant, return a brief message instead of
    # verbose empty XML that wastes the agent's context window.
    has_experiences = bool(output.similar_experiences)
    has_strategies = bool(output.strategies)
    has_playbook = bool(output.playbook_text and len(output.playbook_text) > 50)
    if not has_experiences and not has_strategies and not has_playbook:
        return "No relevant past experiences found for this query."

    # to_structured() returns an XML string; to_json() is the fallback
    try:
        structured = output.to_structured()
    except Exception:
        logger.warning("to_structured() failed, falling back to JSON", exc_info=True)
        return output.to_json()

    # Truncate at a rule boundary to avoid overwhelming the agent
    if len(structured) > _MAX_MEMORY_CHARS:
        structured = _truncate_at_rule_boundary(structured, _MAX_MEMORY_CHARS)

    return structured


def _truncate_at_rule_boundary(text: str, max_chars: int) -> str:
    """Truncate structured output at a rule/XML-element boundary.

    Tries to cut after the last complete closing XML tag (e.g. </strategy>)
    or before the last playbook rule marker ([prefix-NNNNN]) within max_chars,
    so the agent never sees a half-finished entry.
    """
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]

    # Prefer cutting after the last complete closing XML element tag
    close_tag_pattern = re.compile(r'</\w+>\s*\n')
    tag_matches = list(close_tag_pattern.finditer(truncated))
    if tag_matches:
        cut_point = tag_matches[-1].end()
        truncated = text[:cut_point].rstrip()
    else:
        # Fallback: cut before the last playbook rule marker
        rule_pattern = re.compile(r'\n\[([a-z]+)-(\d+)\]\s')
        rule_matches = list(rule_pattern.finditer(truncated))
        if rule_matches:
            cut_point = rule_matches[-1].start()
            truncated = text[:cut_point].rstrip()
        else:
            # Last resort: cut at last newline
            last_newline = truncated.rfind('\n')
            if last_newline > 0:
                truncated = truncated[:last_newline].rstrip()

    # Close any unclosed wrapper tags (detected dynamically rather than
    # hardcoding specific tag names so that new sections are handled
    # automatically).
    open_tags = re.findall(r'<([A-Za-z_][\w-]*)(?:\s[^>]*)?>',  truncated)
    close_tags = re.findall(r'</([A-Za-z_][\w-]*)>', truncated)
    close_counts: dict[str, int] = {}
    for t in close_tags:
        close_counts[t] = close_counts.get(t, 0) + 1
    for t in reversed(open_tags):
        if close_counts.get(t, 0) > 0:
            close_counts[t] -= 1
        else:
            truncated += f'\n</{t}>'

    return truncated


def _check_required_env() -> None:
    """Validate required environment variables. Called at startup and in _get_tool."""
    missing = []
    if not os.environ.get("NEO4J_PASSWORD"):
        missing.append("NEO4J_PASSWORD")
    if not os.environ.get("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    if missing:
        msg = f"Missing required environment variables: {', '.join(missing)}"
        logger.error(msg)
        raise RuntimeError(msg)


if __name__ == "__main__":
    _check_required_env()
    mcp.run()
