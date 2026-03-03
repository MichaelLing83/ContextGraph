"""Playbook parsing, retrieval, and formatting.

Playbooks are collections of rules organized by section (e.g. STRATEGIES AND HARD RULES,
COMMON MISTAKES AND CORRECT STRATEGIES). Each rule has a prefix-NNNNN id like [shr-00001].

This module provides:
- parse_playbook(): Parse playbook text into PlaybookEntry objects
- PlaybookRetriever: Dual-channel (cosine + BM25) retrieval with RRF merge
- format_playbook(): Format entries back into playbook text grouped by section
"""

import re
import logging
from typing import List, Tuple, Optional, Dict
from collections import defaultdict

from agent_memory.models import PlaybookEntry, PLAYBOOK_SECTIONS
from agent_memory.utils import escape_lucene

logger = logging.getLogger(__name__)

# Reverse lookup: section name -> prefix
_SECTION_TO_PREFIX = {v: k for k, v in PLAYBOOK_SECTIONS.items()}

# Pattern for entry ids like [shr-00001]
_ENTRY_ID_RE = re.compile(r"^\[([a-z]+)-(\d+)\]\s+(.+)", re.DOTALL)

# Pattern for section headers like ## STRATEGIES AND HARD RULES
_SECTION_HEADER_RE = re.compile(r"^##\s+(.+)$")


def parse_playbook(content: str) -> List[PlaybookEntry]:
    """Parse playbook text into PlaybookEntry objects.

    Handles:
    - ## section headers (matched against PLAYBOOK_SECTIONS values)
    - [prefix-NNNNN] rule text (may be multi-line until next entry or section)
    """
    entries: List[PlaybookEntry] = []
    current_section = "OTHERS"
    current_prefix = "misc"

    # Accumulator for multi-line entries
    current_id: Optional[str] = None
    current_text_lines: List[str] = []

    def _flush_entry():
        nonlocal current_id, current_text_lines
        if current_id and current_text_lines:
            text = " ".join(current_text_lines).strip()
            # Extract prefix from id
            prefix = current_id.split("-")[0]
            entries.append(PlaybookEntry(
                id=current_id,
                prefix=prefix,
                section=current_section,
                text=text,
            ))
        current_id = None
        current_text_lines = []

    for line in content.splitlines():
        stripped = line.strip()

        # Check for section header
        header_match = _SECTION_HEADER_RE.match(stripped)
        if header_match:
            _flush_entry()
            header_text = header_match.group(1).strip()
            # Find matching section
            if header_text in _SECTION_TO_PREFIX:
                current_section = header_text
                current_prefix = _SECTION_TO_PREFIX[header_text]
            else:
                current_section = header_text
                current_prefix = "misc"
            continue

        # Check for entry id
        entry_match = _ENTRY_ID_RE.match(stripped)
        if entry_match:
            _flush_entry()
            prefix = entry_match.group(1)
            num = entry_match.group(2)
            current_id = f"{prefix}-{num}"
            current_text_lines = [entry_match.group(3).strip()]
            continue

        # Continuation line for current entry
        if current_id and stripped:
            current_text_lines.append(stripped)

    # Flush last entry
    _flush_entry()

    return entries


class PlaybookRetriever:
    """Retrieve relevant playbook entries using dual-channel search (cosine + BM25)."""

    def __init__(self, store, embedder):
        self.store = store
        self.embedder = embedder

    def ingest(self, entries: List[PlaybookEntry]) -> int:
        """Embed and store entries in Neo4j. Returns count ingested."""
        if not entries:
            return 0

        # Embed entries that don't have embeddings yet
        texts_to_embed = []
        indices = []
        for i, entry in enumerate(entries):
            if entry.embedding is None:
                texts_to_embed.append(entry.text)
                indices.append(i)

        if texts_to_embed and self.embedder:
            embeddings = self.embedder.embed_batch(texts_to_embed)
            for idx, emb in zip(indices, embeddings):
                entries[idx].embedding = emb

        if self.store:
            return self.store.batch_create_playbook_entries(entries)
        return len(entries)

    def ingest_file(self, path: str) -> int:
        """Parse a playbook file and ingest its entries. Returns count."""
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        entries = parse_playbook(content)
        return self.ingest(entries)

    def retrieve(
        self,
        query_text: str,
        query_embedding: Optional[List[float]] = None,
        top_k: int = 10,
    ) -> List[PlaybookEntry]:
        """Dual-channel search: cosine + BM25, RRF merge, return top_k entries."""
        if not self.store:
            return []

        # Generate embedding if not provided
        if query_embedding is None and self.embedder:
            query_embedding = self.embedder.embed(query_text)

        # Fetch candidates from both channels
        k_per_channel = top_k * 3  # over-fetch for better RRF merge

        cosine_results = self._search_cosine(query_embedding, k_per_channel) if query_embedding else []
        bm25_results = self._search_bm25(query_text, k_per_channel)

        # RRF merge
        merged = self._rrf_merge(cosine_results, bm25_results, k=60)

        # Fetch full entries for top_k ids
        top_ids = [entry_id for entry_id, _ in merged[:top_k]]
        if not top_ids:
            return []

        return self._fetch_entries_by_ids(top_ids)

    def _search_cosine(
        self, embedding: List[float], top_k: int
    ) -> List[Tuple[str, float]]:
        """Vector index query on playbook_embedding."""
        query = """
        CALL db.index.vector.queryNodes('playbook_embedding', $k, $embedding)
        YIELD node, score
        RETURN node.id AS id, score
        """
        try:
            results = self.store.execute_query(query, {
                "k": top_k,
                "embedding": embedding,
            })
            return [(r["id"], r["score"]) for r in results]
        except Exception as e:
            logger.debug("Cosine search failed: %s", e)
            return []

    def _search_bm25(
        self, query_text: str, top_k: int
    ) -> List[Tuple[str, float]]:
        """Fulltext index query on playbook_text."""
        escaped = escape_lucene(query_text)
        if not escaped.strip():
            return []
        query = """
        CALL db.index.fulltext.queryNodes('playbook_text', $query)
        YIELD node, score
        RETURN node.id AS id, score
        LIMIT $k
        """
        try:
            results = self.store.execute_query(query, {
                "query": escaped,
                "k": top_k,
            })
            return [(r["id"], r["score"]) for r in results]
        except Exception as e:
            logger.debug("BM25 search failed: %s", e)
            return []

    def _rrf_merge(
        self,
        cosine_results: List[Tuple[str, float]],
        bm25_results: List[Tuple[str, float]],
        k: int = 60,
    ) -> List[Tuple[str, float]]:
        """Reciprocal Rank Fusion merge of two ranked lists."""
        scores: Dict[str, float] = defaultdict(float)

        for rank, (entry_id, _) in enumerate(cosine_results):
            scores[entry_id] += 1.0 / (k + rank + 1)

        for rank, (entry_id, _) in enumerate(bm25_results):
            scores[entry_id] += 1.0 / (k + rank + 1)

        # Sort by RRF score descending
        merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return merged

    def _fetch_entries_by_ids(self, ids: List[str]) -> List[PlaybookEntry]:
        """Fetch PlaybookEntry nodes by their ids, preserving order."""
        query = """
        UNWIND $ids AS eid
        MATCH (p:PlaybookEntry {id: eid})
        RETURN p.id AS id, p.prefix AS prefix, p.section AS section, p.text AS text
        """
        try:
            results = self.store.execute_query(query, {"ids": ids})
        except Exception as e:
            logger.debug("Fetch entries failed: %s", e)
            return []

        # Build lookup and preserve original order
        lookup = {r["id"]: r for r in results}
        entries = []
        for eid in ids:
            if eid in lookup:
                r = lookup[eid]
                entries.append(PlaybookEntry(
                    id=r["id"],
                    prefix=r["prefix"],
                    section=r["section"],
                    text=r["text"],
                ))
        return entries


def format_playbook(entries: List[PlaybookEntry]) -> str:
    """Format entries as playbook text grouped by section.

    Output follows the canonical section order from PLAYBOOK_SECTIONS.
    Sections with no entries are omitted.
    """
    if not entries:
        return ""

    # Group entries by section
    by_section: Dict[str, List[PlaybookEntry]] = defaultdict(list)
    for entry in entries:
        by_section[entry.section].append(entry)

    parts = []
    # Output sections in canonical order
    for prefix, section_name in PLAYBOOK_SECTIONS.items():
        if section_name not in by_section:
            continue
        parts.append(f"## {section_name}")
        for entry in by_section[section_name]:
            parts.append(f"[{entry.id}] {entry.text}")
        parts.append("")  # blank line between sections

    # Any sections not in PLAYBOOK_SECTIONS (custom headers)
    known_sections = set(PLAYBOOK_SECTIONS.values())
    for section_name, section_entries in by_section.items():
        if section_name in known_sections:
            continue
        parts.append(f"## {section_name}")
        for entry in section_entries:
            parts.append(f"[{entry.id}] {entry.text}")
        parts.append("")

    return "\n".join(parts).rstrip("\n")
