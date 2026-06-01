"""In-memory index of an Obsidian vault for tag/link/text retrieval."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from agent_memory.vault.parser import _SKIP_DIR_NAMES
from agent_memory.vault.wikilinks import (
    extract_inline_tags,
    extract_wikilinks,
    parse_frontmatter_tags,
    resolve_wikilink_target,
    split_frontmatter,
)

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


@dataclass
class IndexedNote:
    path: Path
    rel_path: str
    title: str
    meta: dict
    tags: Set[str]
    outlinks: List[str]  # resolved rel_paths
    body: str
    tokens: Set[str] = field(default_factory=set)


@dataclass
class SearchHit:
    rel_path: str
    title: str
    score: float
    reasons: List[str]
    snippet: str
    tags: Set[str]
    linked_from: List[str] = field(default_factory=list)


class ObsidianVaultIndex:
    """Index markdown notes by tags, wikilinks, and plain text tokens."""

    def __init__(self, vault_root: Path):
        self.vault_root = vault_root.resolve()
        self.notes: Dict[str, IndexedNote] = {}
        self.tag_to_notes: Dict[str, Set[str]] = {}
        self.outlinks: Dict[str, Set[str]] = {}
        self.inlinks: Dict[str, Set[str]] = {}

    def build(self, *, glob: str = "**/*.md") -> int:
        self.notes.clear()
        self.tag_to_notes.clear()
        self.outlinks.clear()
        self.inlinks.clear()

        count = 0
        for path in sorted(self.vault_root.glob(glob)):
            if not path.is_file():
                continue
            if any(p in _SKIP_DIR_NAMES for p in path.parts):
                continue
            self._index_file(path)
            count += 1

        self._resolve_links()
        return count

    def _index_file(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        meta, body = split_frontmatter(text)
        rel = str(path.relative_to(self.vault_root))
        title = _title_from_text(text, path)
        tags = set(meta.get("tags") or [])
        tags |= parse_frontmatter_tags(_frontmatter_raw(text) or "")
        tags |= extract_inline_tags(body)
        tokens = set(_TOKEN_RE.findall(f"{title} {body}".lower()))

        note = IndexedNote(
            path=path,
            rel_path=rel,
            title=title,
            meta=meta,
            tags=tags,
            outlinks=[],
            body=body,
            tokens=tokens,
        )
        self.notes[rel] = note
        for tag in tags:
            self.tag_to_notes.setdefault(tag, set()).add(rel)

    def _resolve_links(self) -> None:
        for rel, note in self.notes.items():
            raw = note.path.read_text(encoding="utf-8", errors="replace")
            targets: Set[str] = set()
            for target, _ in extract_wikilinks(raw):
                try:
                    resolved = resolve_wikilink_target(
                        target, self.vault_root, note.path
                    )
                except OSError:
                    continue
                if resolved:
                    try:
                        targets.add(str(resolved.relative_to(self.vault_root)))
                    except ValueError:
                        pass
            note.outlinks = sorted(targets)
            self.outlinks[rel] = targets
            for t in targets:
                self.inlinks.setdefault(t, set()).add(rel)

    def search(
        self,
        query: str,
        *,
        tags: Optional[List[str]] = None,
        tag_prefix: str = "",
        exact_phrase: str = "",
        hops: int = 0,
        limit: int = 20,
    ) -> List[SearchHit]:
        """
        Rank notes by query text, optional tag filter, and link expansion.

        Scoring (higher is better):
          - tag exact/prefix match
          - title token overlap
          - body token overlap
          - notes linked from high-scoring hits (if hops > 0)
        """
        q_tokens = set(_TOKEN_RE.findall(query.lower()))
        phrase = exact_phrase.strip().lower()
        required_tags = set(tags or [])
        hits: Dict[str, SearchHit] = {}

        def score_note(rel: str, base: float, reasons: List[str]) -> None:
            n = self.notes.get(rel)
            if not n:
                return
            if required_tags and not required_tags.issubset(n.tags):
                return
            if tag_prefix and not any(
                t.startswith(tag_prefix) or t == tag_prefix.rstrip("/")
                for t in n.tags
            ):
                return
            text_has_phrase = phrase and (
                phrase in n.title.lower() or phrase in n.body.lower()
            )
            if phrase and not text_has_phrase:
                return
            snippet = _snippet(n.body, q_tokens, phrase=phrase)
            prev = hits.get(rel)
            if prev is None or base > prev.score:
                hits[rel] = SearchHit(
                    rel_path=rel,
                    title=n.title,
                    score=base,
                    reasons=reasons,
                    snippet=snippet,
                    tags=set(n.tags),
                )

        for rel, n in self.notes.items():
            s = 0.0
            reasons: List[str] = []
            if required_tags and required_tags.issubset(n.tags):
                s += 3.0
                reasons.append("tag_filter")
            for tag in n.tags:
                if tag_prefix and tag.startswith(tag_prefix.rstrip("/")):
                    s += 1.5
                    reasons.append(f"tag:{tag}")
            title_overlap = q_tokens & set(_TOKEN_RE.findall(n.title.lower()))
            body_overlap = q_tokens & n.tokens
            if phrase:
                phrase_in_title = phrase in n.title.lower()
                phrase_in_body = phrase in n.body.lower()
                if phrase_in_title:
                    s += 4.0
                    reasons.append("exact_phrase:title")
                elif phrase_in_body:
                    s += 2.0
                    reasons.append("exact_phrase:body")
            if title_overlap:
                s += 2.0 * len(title_overlap)
                reasons.append("title")
            if body_overlap:
                s += 1.0 * len(body_overlap)
                reasons.append("body")
            if s > 0 or (required_tags and required_tags.issubset(n.tags)):
                if s == 0 and required_tags:
                    s = 2.5
                    reasons.append("tag_only")
                score_note(rel, s, reasons)

        if hops > 0:
            expanded: Dict[str, float] = {}
            for rel, hit in list(hits.items()):
                for linked in self.outlinks.get(rel, set()):
                    expanded[linked] = max(expanded.get(linked, 0), hit.score * 0.6)
                for linked in self.inlinks.get(rel, set()):
                    expanded[linked] = max(expanded.get(linked, 0), hit.score * 0.5)
            for rel, boost in expanded.items():
                hit = hits.get(rel)
                if hit:
                    hit.score += boost
                    hit.reasons.append("link_expand")
                    hit.linked_from.append("graph")
                else:
                    score_note(rel, boost, ["link_neighbor"])

        ranked = sorted(hits.values(), key=lambda h: -h.score)
        return ranked[:limit]


def _title_from_text(text: str, path: Path) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()
    return path.stem.replace("-", " ")


def _frontmatter_raw(text: str) -> Optional[str]:
    import re

    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    return m.group(1) if m else None


def _snippet(body: str, tokens: Set[str], *, phrase: str = "", width: int = 160) -> str:
    plain = re.sub(r"\s+", " ", body).strip()
    if phrase and plain:
        idx = plain.lower().find(phrase)
        if idx >= 0:
            start = max(0, idx - 40)
            return plain[start : start + width] + (
                "…" if start + width < len(plain) else ""
            )
    if not tokens or not plain:
        return plain[:width]
    lower = plain.lower()
    for tok in sorted(tokens, key=len, reverse=True):
        idx = lower.find(tok)
        if idx >= 0:
            start = max(0, idx - 40)
            return plain[start : start + width] + ("…" if start + width < len(plain) else "")
    return plain[:width] + ("…" if len(plain) > width else "")
