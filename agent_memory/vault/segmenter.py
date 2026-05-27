"""Segment a note into fragments (heading blocks or paragraph chunks)."""

from __future__ import annotations

import re
from typing import List, Literal

from agent_memory.vault.models import RawVaultNote, VaultSection
from agent_memory.vault.parser import markdown_to_plain_text

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
ChunkMode = Literal["heading", "adaptive", "chapter"]


def segment_note(
    note: RawVaultNote,
    *,
    mode: ChunkMode = "heading",
    target_chars: int = 500,
    max_fragment_chars: int = 3000,
    max_section_chars: int = 4000,
    min_section_chars: int = 80,
    preserve_markup: bool = True,
) -> List[VaultSection]:
    """Split a note into fragments. See ``mode`` for strategies."""
    if mode == "chapter":
        sections = _segment_whole_note(note, preserve_markup=preserve_markup)
    elif mode == "adaptive":
        sections = segment_note_adaptive(
            note, target_chars=target_chars, preserve_markup=preserve_markup
        )
    else:
        sections = _segment_by_headings_or_paragraphs(
            note,
            max_section_chars=max_section_chars,
            min_section_chars=min_section_chars,
            preserve_markup=preserve_markup,
        )
    if max_fragment_chars > 0:
        sections = cap_fragment_sizes(sections, max_fragment_chars)
    return sections


def cap_fragment_sizes(
    sections: List[VaultSection], max_chars: int
) -> List[VaultSection]:
    """
    Split any fragment larger than ``max_chars`` by markdown paragraphs (``\\n\\n``).

    Preserves all text; single paragraphs above the cap stay intact.
    """
    if max_chars <= 0:
        return sections
    out: List[VaultSection] = []
    for sec in sections:
        if len(sec.body) <= max_chars:
            out.append(sec)
        else:
            out.extend(_split_section_by_paragraphs(sec, max_chars))
    for i, sec in enumerate(out):
        sec.index = i
    return out


def _split_section_by_paragraphs(
    sec: VaultSection, max_chars: int
) -> List[VaultSection]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", sec.body) if p.strip()]
    if not paragraphs:
        return [sec]

    chunks: List[str] = []
    buf: List[str] = []
    buf_len = 0

    def flush_buf() -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        chunks.append("\n\n".join(buf))
        buf = []
        buf_len = 0

    for para in paragraphs:
        if len(para) > max_chars:
            flush_buf()
            chunks.append(para)
            continue
        extra = 2 if buf else 0
        if buf and buf_len + extra + len(para) > max_chars:
            flush_buf()
        buf.append(para)
        buf_len += extra + len(para)
    flush_buf()

    if len(chunks) == 1:
        return [sec]

    total = len(chunks)
    parts: List[VaultSection] = []
    for i, body in enumerate(chunks):
        if total == 1:
            heading = sec.heading
        else:
            heading = f"{sec.heading} ({i + 1}/{total})"
            if len(heading) > 120:
                heading = heading[:117] + "…"
        parts.append(
            VaultSection(
                index=sec.index,
                heading=heading,
                body=body,
                level=sec.level,
            )
        )
    return parts


def segment_note_adaptive(
    note: RawVaultNote,
    *,
    target_chars: int = 500,
    preserve_markup: bool = True,
) -> List[VaultSection]:
    """
    Adaptive greedy chunking without dropping content.

    - If the whole note (plain text) fits in ``target_chars``, one fragment.
    - Otherwise split into atomic units (heading blocks, or paragraphs if no headings),
      then greedily merge adjacent units while total size <= ``target_chars``.
    - A single unit larger than ``target_chars`` is kept whole (no truncation).
    """
    full = (note.structured_body or note.body).strip()
    if not full:
        return []

    if len(full) <= target_chars:
        body = full if preserve_markup else note.body.strip()
        return [
            VaultSection(
                index=0,
                heading=note.title,
                body=body,
                level=0,
            )
        ]

    units = _atomic_units(note, preserve_markup=preserve_markup)
    if not units:
        return _segment_whole_note(note)

    packed: List[VaultSection] = []
    buf: List[VaultSection] = []
    buf_len = 0

    def flush_buffer() -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        packed.append(_merge_units(buf, index=len(packed)))
        buf = []
        buf_len = 0

    for unit in units:
        unit_len = len(unit.body)
        if unit_len > target_chars:
            flush_buffer()
            packed.append(
                VaultSection(
                    index=len(packed),
                    heading=unit.heading,
                    body=unit.body,
                    level=unit.level,
                )
            )
            continue

        extra = 2 if buf else 0
        if buf and buf_len + extra + unit_len > target_chars:
            flush_buffer()
        buf.append(unit)
        buf_len += extra + unit_len

    flush_buffer()
    return packed


def _segment_whole_note(
    note: RawVaultNote, *, preserve_markup: bool = True
) -> List[VaultSection]:
    body = (note.structured_body if preserve_markup else note.body).strip()
    if not body:
        return []
    return [VaultSection(index=0, heading=note.title, body=body, level=0)]


def _atomic_units(note: RawVaultNote, *, preserve_markup: bool = True) -> List[VaultSection]:
    """Smallest splits: per heading block, or per paragraph when there are no headings."""
    return _segment_by_headings_or_paragraphs(
        note,
        max_section_chars=10_000,
        min_section_chars=1,
        preserve_markup=preserve_markup,
    )


def _section_body_text(section_body: str, *, preserve_markup: bool) -> str:
    if preserve_markup:
        return section_body
    return markdown_to_plain_text(section_body, strip_fm=False) or section_body


def _merge_units(units: List[VaultSection], *, index: int) -> VaultSection:
    if len(units) == 1:
        return VaultSection(
            index=index,
            heading=units[0].heading,
            body=units[0].body,
            level=units[0].level,
        )
    heading = units[0].heading
    if units[-1].heading != heading:
        heading = f"{units[0].heading} – {units[-1].heading}"
        if len(heading) > 120:
            heading = heading[:117] + "…"
    body = "\n\n".join(u.body for u in units if u.body)
    return VaultSection(
        index=index,
        heading=heading,
        body=body,
        level=units[0].level,
    )


def _segment_by_headings_or_paragraphs(
    note: RawVaultNote,
    *,
    max_section_chars: int,
    min_section_chars: int,
    preserve_markup: bool = True,
) -> List[VaultSection]:
    body = (note.structured_body or note.body).strip()
    if not body:
        return []

    matches = list(_HEADING_RE.finditer(body))
    if matches:
        return _sections_from_headings(matches, body, preserve_markup=preserve_markup)

    return _sections_from_paragraphs(
        body, max_section_chars, min_section_chars, preserve_markup=preserve_markup
    )


def _sections_from_headings(
    matches: list, body: str, *, preserve_markup: bool = True
) -> List[VaultSection]:
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
        sections.append(
            VaultSection(
                index=i,
                heading=heading,
                body=_section_body_text(section_body, preserve_markup=preserve_markup),
                level=level,
            )
        )
    return sections


def _sections_from_paragraphs(
    body: str,
    max_chars: int,
    min_chars: int,
    *,
    preserve_markup: bool = True,
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
            sections.append(
                VaultSection(
                    index=idx,
                    heading=title,
                    body=_section_body_text(text, preserve_markup=preserve_markup),
                    level=0,
                )
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
