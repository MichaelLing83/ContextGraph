"""Segment a note into fragments (heading blocks or paragraph chunks)."""

from __future__ import annotations

import re
from typing import List

from agent_memory.vault.models import RawVaultNote, VaultSection
from agent_memory.vault.parser import markdown_to_plain_text

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def segment_note(
    note: RawVaultNote,
    *,
    max_section_chars: int = 4000,
    min_section_chars: int = 80,
) -> List[VaultSection]:
    """
    Split note body into sections.

    Prefer markdown headings (any level). If none, fall back to paragraph chunks.
    Uses structured_body (headings intact); falls back to plain body.
    """
    body = (note.structured_body or note.body).strip()
    if not body:
        return []

    matches = list(_HEADING_RE.finditer(body))
    if matches:
        return _sections_from_headings(matches, body)

    return _sections_from_paragraphs(body, max_section_chars, min_section_chars)


def _sections_from_headings(matches: list, body: str) -> List[VaultSection]:
    sections: List[VaultSection] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        block = body[start:end].strip()
        level = len(match.group(1))
        heading = match.group(2).strip()
        # Body after the heading line
        first_nl = block.find("\n")
        section_body = block[first_nl + 1 :].strip() if first_nl >= 0 else ""
        if not section_body:
            section_body = heading
        plain_body = markdown_to_plain_text(section_body, strip_fm=False)
        sections.append(
            VaultSection(
                index=i,
                heading=heading,
                body=plain_body or section_body,
                level=level,
            )
        )
    return sections


def _sections_from_paragraphs(
    body: str,
    max_chars: int,
    min_chars: int,
) -> List[VaultSection]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if not paragraphs:
        return []

    sections: List[VaultSection] = []
    buf: List[str] = []
    buf_len = 0
    idx = 0

    def flush() -> None:
        nonlocal idx, buf, buf_len
        if not buf:
            return
        text = "\n\n".join(buf).strip()
        if len(text) >= min_chars or not sections:
            title = text[:80].replace("\n", " ")
            if len(text) > 80:
                title += "…"
            plain = markdown_to_plain_text(text, strip_fm=False)
            sections.append(
                VaultSection(index=idx, heading=title, body=plain or text, level=0)
            )
            idx += 1
        buf = []
        buf_len = 0

    for para in paragraphs:
        if buf_len + len(para) + 2 > max_chars and buf:
            flush()
        buf.append(para)
        buf_len += len(para) + 2
    flush()
    return sections
