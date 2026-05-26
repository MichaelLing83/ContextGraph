"""Write vault notes into the Neo4j context graph (Trajectory + Fragment)."""

from __future__ import annotations

import uuid
import logging
from typing import TYPE_CHECKING, List, Optional

from agent_memory.models import Trajectory, Fragment
from agent_memory.vault.models import RawVaultNote
from agent_memory.vault.segmenter import segment_note

if TYPE_CHECKING:
    from agent_memory.neo4j_store import Neo4jStore
    from agent_memory.embeddings import EmbeddingClient

logger = logging.getLogger(__name__)


class VaultWriter:
    """
    Ingest one markdown note as a Trajectory episode with section Fragments.

    Maps Obsidian concepts to the existing graph schema:
      - Note file  -> Trajectory (task_type=note)
      - Section    -> Fragment (fragment_type=section)
    ErrorPattern extraction is skipped (not meaningful for general notes).
    """

    def __init__(
        self,
        store: Optional["Neo4jStore"],
        embedder: Optional["EmbeddingClient"],
    ):
        self.store = store
        self.embedder = embedder

    def write_note(self, note: RawVaultNote) -> str:
        from agent_memory.vault.models import VaultSection

        sections = segment_note(note)
        if not sections and note.body.strip():
            sections = [
                VaultSection(
                    index=0,
                    heading=note.title,
                    body=note.body.strip(),
                    level=0,
                )
            ]
        traj_id = f"note_{uuid.uuid4().hex[:12]}"

        summary = self._build_summary(note, sections)
        trajectory = Trajectory(
            id=traj_id,
            instance_id=note.rel_path,
            repo=note.folder or "vault_root",
            task_type="note",
            success=True,
            total_steps=max(1, len(sections)),
            summary=summary,
        )

        fragments = self._sections_to_fragments(sections)

        if self.embedder:
            trajectory.embedding = self.embedder.embed(summary)
            for frag in fragments:
                text = frag.description
                frag.embedding = self.embedder.embed(text[:8000])

        if self.store:
            self.store.create_trajectory(trajectory)
            for frag in fragments:
                self.store.create_fragment(frag, traj_id)

        logger.info(
            "Wrote note %s as %s (%d sections)",
            note.rel_path,
            traj_id,
            len(fragments),
        )
        return traj_id

    def _build_summary(self, note: RawVaultNote, sections: List) -> str:
        preview = note.body[:500].replace("\n", " ")
        if len(note.body) > 500:
            preview += "…"
        headings = ", ".join(s.heading for s in sections[:8])
        extra = f" Sections: {headings}." if headings else ""
        return f"Note: {note.title}. Path: {note.rel_path}.{extra} {preview}"

    def _sections_to_fragments(self, sections: List) -> List[Fragment]:
        fragments: List[Fragment] = []
        for sec in sections:
            description = f"{sec.heading}\n\n{sec.body}".strip()
            if len(description) > 6000:
                description = description[:6000] + "…"
            fragments.append(
                Fragment(
                    id=f"frag_{uuid.uuid4().hex[:12]}",
                    step_range=(sec.index, sec.index),
                    fragment_type="section",
                    description=description,
                    action_sequence=[sec.heading],
                    outcome="completed",
                )
            )
        return fragments
