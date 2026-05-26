"""Turn Obsidian vault search hits into a readable knowledge summary."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from agent_memory.vault.obsidian_index import ObsidianVaultIndex, SearchHit

_SECTION_CUT_RE = re.compile(r"^##\s+(Graph|Related)\s*$", re.MULTILINE)
_QUERY_WINDOW = 280


def _source_key(hit: "SearchHit", index: "ObsidianVaultIndex") -> str:
    note = index.notes.get(hit.rel_path)
    if note:
        rel = note.meta.get("source_rel_path") or note.meta.get("source")
        if rel:
            return str(rel)
        for tag in sorted(note.tags):
            if tag.startswith("cg/source/"):
                return tag.replace("cg/source/", "", 1)
    for tag in sorted(hit.tags):
        if tag.startswith("cg/source/"):
            return tag.replace("cg/source/", "", 1)
    return "other"


def _chapter_label(source_key: str) -> str:
    if source_key == "other":
        return "其他笔记"
    name = source_key.replace("\\", "/").split("/")[-1]
    if name.lower().endswith(".md"):
        name = name[:-3]
    return name


def _clean_body(body: str) -> str:
    body = _SECTION_CUT_RE.split(body)[0]
    lines = []
    for line in body.splitlines():
        if line.strip().startswith("#"):
            continue
        lines.append(line)
    text = "\n".join(lines).strip()
    text = re.sub(r"\[\[[^\]]+\]\]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _excerpt(text: str, query: str, max_len: int) -> str:
    plain = re.sub(r"\s+", " ", text).strip()
    if not plain:
        return ""
    if len(plain) <= max_len:
        return plain
    q = query.strip().lower()
    if q:
        idx = plain.lower().find(q)
        if idx >= 0:
            start = max(0, idx - max_len // 3)
            chunk = plain[start : start + max_len]
            prefix = "…" if start > 0 else ""
            suffix = "…" if start + max_len < len(plain) else ""
            return f"{prefix}{chunk}{suffix}"
    return plain[: max_len - 1] + "…"


def build_knowledge_summary(
    query: str,
    hits: List["SearchHit"],
    index: "ObsidianVaultIndex",
    *,
    max_chars: int = 6000,
    per_excerpt: int = 500,
    include_meta: bool = False,
) -> str:
    """
    Merge search hits into prose-oriented markdown (content only by default).

    Groups fragments by source chapter, deduplicates overlapping excerpts.
    """
    if not hits:
        return f"未找到与「{query}」相关的笔记。" if query else "未找到匹配的笔记。"

    grouped: dict[str, list] = defaultdict(list)
    for hit in hits:
        grouped[_source_key(hit, index)].append(hit)

    lines: list[str] = []
    if include_meta and query:
        lines.extend([f"# 知识摘要：{query}", ""])

    used = sum(len(l) + 1 for l in lines)
    section_count = 0

    for source_key in sorted(grouped.keys(), key=lambda k: (k == "other", k)):
        chapter_hits = sorted(grouped[source_key], key=lambda h: -h.score)
        chapter_title = _chapter_label(source_key)
        block = [f"## {chapter_title}", ""]

        seen_excerpts: set[str] = set()
        for hit in chapter_hits:
            note = index.notes.get(hit.rel_path)
            raw = note.body if note else hit.snippet
            cleaned = _clean_body(raw)
            excerpt = _excerpt(cleaned, query, per_excerpt)
            if not excerpt or excerpt in seen_excerpts:
                continue
            seen_excerpts.add(excerpt)
            block.append(excerpt)
            block.append("")

        if len(block) <= 2:
            continue

        block_text = "\n".join(block).strip() + "\n"
        if used + len(block_text) > max_chars:
            remaining = max_chars - used
            if remaining < 200:
                break
            block_text = block_text[:remaining].rstrip() + "…\n"
        lines.append(block_text)
        used += len(block_text)
        section_count += 1

    if section_count == 0:
        return "未找到可提取的正文。"

    return "\n".join(lines).strip() + "\n"


def llm_polish_summary(
    draft: str,
    query: str,
    *,
    api_base: str,
    api_key: str,
    model: str = "claude-sonnet-4-20250514",
) -> str:
    """Optional: rewrite grouped draft into one cohesive paragraph via LLM."""
    from openai import OpenAI

    client = OpenAI(base_url=api_base, api_key=api_key)
    prompt = f"""You are summarizing notes from an Obsidian knowledge graph for the topic/query: "{query}".

Below is structured excerpt material (may be Swedish or other languages). Write ONE cohesive summary in the same language as the majority of the source text.

Rules:
- Synthesize, do not list files
- Keep facts from the excerpts only; do not invent
- 2-4 short paragraphs, readable prose
- You may use a short bullet list only if it improves clarity

Source material:
{draft[:12000]}
"""
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1200,
    )
    return (resp.choices[0].message.content or draft).strip()
