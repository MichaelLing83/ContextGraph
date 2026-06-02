"""Query graph vault by treating a passage as a virtual fragment."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Set

from agent_memory.vault.obsidian_index import ObsidianVaultIndex, SearchHit, _snippet
from agent_memory.vault.similarity import jaccard_similarity, overlap_coefficient, tokenize

logger = logging.getLogger(__name__)

DEFAULT_FRAGMENT_TAG = "cg/fragment"


@dataclass
class PassageQueryConfig:
    seed_topk: int = 8
    semantic_min: float = 0.15
    semantic_weight: float = 0.6
    lexical_weight: float = 0.4
    hops: int = 1
    limit: int = 15
    tags: Optional[List[str]] = None


def search_passage(
    index: ObsidianVaultIndex,
    passage_text: str,
    *,
    query_summary: str = "",
    config: Optional[PassageQueryConfig] = None,
) -> List[SearchHit]:
    """
    Rank fragments related to a query passage (virtual fragment, not written to vault).

    1. Score each ``cg/fragment`` by semantic (cg_llm_summary) and lexical overlap.
    2. Take top ``seed_topk`` as seeds.
    3. Expand along wikilinks for ``hops`` steps.
    4. Return top ``limit`` hits.
    """
    cfg = config or PassageQueryConfig()
    passage = passage_text.strip()
    if not passage:
        return []

    required_tags = set(cfg.tags or [DEFAULT_FRAGMENT_TAG])
    query_lex_tokens = tokenize(passage)
    query_sem_tokens = tokenize(query_summary) if query_summary.strip() else set()

    scored: dict[str, tuple[float, list[str]]] = {}
    for rel, note in _fragment_notes(index, required_tags):
        reasons: list[str] = []
        sem_score = 0.0
        frag_summary = str(note.meta.get("cg_llm_summary") or "").strip()
        if query_sem_tokens and frag_summary:
            sem_score = jaccard_similarity(query_sem_tokens, tokenize(frag_summary))
            if sem_score >= cfg.semantic_min:
                reasons.append(f"semantic:{sem_score:.2f}")

        lex_score = overlap_coefficient(query_lex_tokens, note.tokens)
        if lex_score > 0:
            reasons.append(f"lexical:{lex_score:.2f}")

        if query_sem_tokens and frag_summary:
            combined = cfg.semantic_weight * sem_score + cfg.lexical_weight * lex_score
        else:
            combined = lex_score

        if combined > 0:
            scored[rel] = (combined, reasons)

    seeds = sorted(scored.items(), key=lambda x: (-x[1][0], x[0]))[: cfg.seed_topk]
    hits: dict[str, SearchHit] = {}

    def add_hit(rel: str, score: float, reasons: list[str], linked_from: Optional[str] = None) -> None:
        note = index.notes.get(rel)
        if not note:
            return
        if required_tags and not required_tags.issubset(note.tags):
            return
        snippet = _snippet(note.body, query_lex_tokens, width=200)
        prev = hits.get(rel)
        if prev is None or score > prev.score:
            hits[rel] = SearchHit(
                rel_path=rel,
                title=note.title,
                score=score,
                reasons=list(reasons),
                snippet=snippet,
                tags=set(note.tags),
                linked_from=[linked_from] if linked_from else [],
            )
        elif prev and linked_from:
            if linked_from not in prev.linked_from:
                prev.linked_from.append(linked_from)
            for r in reasons:
                if r not in prev.reasons:
                    prev.reasons.append(r)

    for rel, (score, reasons) in seeds:
        add_hit(rel, score + 3.0, reasons + ["passage_seed"])

    if cfg.hops > 0:
        expanded: dict[str, float] = {}
        for rel, hit in list(hits.items()):
            for linked in index.outlinks.get(rel, set()):
                if linked in index.notes and _is_fragment(index.notes[linked], required_tags):
                    expanded[linked] = max(expanded.get(linked, 0), hit.score * 0.6)
            for linked in index.inlinks.get(rel, set()):
                if linked in index.notes and _is_fragment(index.notes[linked], required_tags):
                    expanded[linked] = max(expanded.get(linked, 0), hit.score * 0.5)
        for rel, boost in expanded.items():
            hit = hits.get(rel)
            if hit:
                hit.score += boost
                hit.reasons.append("link_expand")
            else:
                add_hit(rel, boost, ["link_neighbor"], linked_from="graph")

    ranked = sorted(hits.values(), key=lambda h: -h.score)
    return ranked[: cfg.limit]


def _fragment_notes(index: ObsidianVaultIndex, required_tags: Set[str]):
    for rel, note in index.notes.items():
        if not _is_fragment(note, required_tags):
            continue
        yield rel, note


def _is_fragment(note, required_tags: Set[str]) -> bool:
    return required_tags.issubset(note.tags)
