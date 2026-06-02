"""Build a context graph as Obsidian markdown notes (wikilinks + tags)."""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, List, Literal, Optional

from agent_memory.vault.models import RawVaultNote

if TYPE_CHECKING:
    from agent_memory.vault.fragment_summary import FragmentSummarizer
from agent_memory.vault.segmenter import ChunkMode, segment_note
from agent_memory.vault.wikilinks import (
    note_title_to_filename,
    source_rel_to_stub_stem,
    wikilink_for_path,
)

logger = logging.getLogger(__name__)

DEFAULT_GRAPH_DIR = "ContextGraph"
LinkMode = Literal["stub", "symlink", "frontmatter"]
RelatedMode = Literal["all", "none", "adjacent", "topk"]
DEFAULT_RELATED_TOPK = 2


def related_section_indices(
    index: int,
    n_sections: int,
    *,
    mode: RelatedMode = "all",
    topk: int = DEFAULT_RELATED_TOPK,
) -> List[int]:
    """
    Indices of sibling fragments to link from section ``index``.

    ``topk``: at most ``topk`` others, nearest in document order first
    (smallest ``abs(i - j)``, then lower index on ties).
    """
    if n_sections <= 1 or mode == "none":
        return []
    if mode == "all":
        return [j for j in range(n_sections) if j != index]
    if mode == "adjacent":
        out: List[int] = []
        if index > 0:
            out.append(index - 1)
        if index < n_sections - 1:
            out.append(index + 1)
        return out
    if mode == "topk":
        k = max(1, topk)
        others = sorted(
            (j for j in range(n_sections) if j != index),
            key=lambda j: (abs(j - index), j),
        )
        return others[:k]
    raise ValueError(f"unknown related mode: {mode!r}")

_REGISTRY_NAME = ".graph_registry.json"


class ObsidianGraphBuilder:
    """
    Write graph nodes into an Obsidian vault.

    Modes:
      1. **Separate vaults** (recommended): read ``source_vault``, write ``graph_vault``.
         The graph vault is its own Obsidian library (MOC, Fragments/, Sources/).
      2. **Embedded**: single vault with nodes under ``graph_dir/`` (default ``ContextGraph/``).

    Cross-vault links to originals:
      - ``frontmatter`` (default): source path in YAML only; no ``Sources/`` notes or source wikilinks
        (Graph view shows fragment nodes only).
      - ``stub``: one ``Sources/*.md`` card per source note; fragments wikilink to it.
      - ``symlink``: ``Sources/`` → source vault directory (wikilinks like ``[[Sources/Projects/Note]]``).
    """

    def __init__(
        self,
        graph_vault: Path,
        *,
        source_vault: Optional[Path] = None,
        graph_dir: str = "",
        link_mode: LinkMode = "frontmatter",
        chunk_mode: ChunkMode = "heading",
        fragment_chars: int = 500,
        fragment_max_chars: int = 3000,
        preserve_markup: bool = True,
        related_mode: RelatedMode = "all",
        related_topk: int = DEFAULT_RELATED_TOPK,
        summarizer: Optional["FragmentSummarizer"] = None,
    ):
        self.graph_vault = graph_vault.resolve()
        self.source_vault = (source_vault or graph_vault).resolve()
        self.separate_vaults = self.source_vault != self.graph_vault
        self.link_mode = link_mode
        self.chunk_mode = chunk_mode
        self.fragment_chars = fragment_chars
        self.fragment_max_chars = fragment_max_chars
        self.preserve_markup = preserve_markup
        self.related_mode = related_mode
        self.related_topk = related_topk
        self.summarizer = summarizer

        if graph_dir:
            self.graph_root = self.graph_vault / graph_dir
        elif self.separate_vaults:
            self.graph_root = self.graph_vault
        else:
            self.graph_root = self.graph_vault / DEFAULT_GRAPH_DIR

        self.fragments_dir = self.graph_root / "Fragments"
        self.sources_dir = self.graph_root / "Sources"
        self.registry_path = self.graph_root / _REGISTRY_NAME
        self._registry: dict = {
            "source_vault": str(self.source_vault),
            "graph_vault": str(self.graph_vault),
            "separate_vaults": self.separate_vaults,
            "link_mode": link_mode,
            "chunk_mode": chunk_mode,
            "fragment_chars": fragment_chars,
            "fragment_max_chars": fragment_max_chars,
            "related_mode": related_mode,
            "related_topk": related_topk,
            "sources": {},
            "fragments": {},
            "source_stubs": {},
        }
        self._stub_cache: dict[str, str] = {}

    def load_registry(self) -> None:
        if self.registry_path.is_file():
            loaded = json.loads(self.registry_path.read_text(encoding="utf-8"))
            self._registry.update(loaded)
            self._stub_cache = dict(self._registry.get("source_stubs", {}))

    def save_registry(self) -> None:
        self.graph_root.mkdir(parents=True, exist_ok=True)
        self._registry["source_stubs"] = self._stub_cache
        self.registry_path.write_text(
            json.dumps(self._registry, indent=2),
            encoding="utf-8",
        )

    def setup_cross_vault_links(self) -> None:
        """Create Sources/ symlink when link_mode=symlink."""
        if not self.separate_vaults or self.link_mode != "symlink":
            return
        self.sources_dir.parent.mkdir(parents=True, exist_ok=True)
        if self.sources_dir.exists() or self.sources_dir.is_symlink():
            return
        os.symlink(self.source_vault, self.sources_dir, target_is_directory=True)
        logger.info("Symlinked %s -> %s", self.sources_dir, self.source_vault)

    def ingest_note(self, note: RawVaultNote) -> List[str]:
        """Create fragment notes linked to the source. Returns fragment rel paths (graph vault)."""
        from agent_memory.vault.models import VaultSection

        sections = segment_note(
            note,
            mode=self.chunk_mode,
            target_chars=self.fragment_chars,
            max_fragment_chars=self.fragment_max_chars,
            preserve_markup=self.preserve_markup,
        )
        if not sections and note.body.strip():
            sections = [
                VaultSection(
                    index=0,
                    heading=note.title,
                    body=note.body.strip(),
                    level=0,
                )
            ]

        source_ref = self._source_reference(note)
        fragment_paths: List[str] = []

        for sec_idx, sec in enumerate(sections):
            frag_id = f"frag_{uuid.uuid4().hex[:8]}"
            slug = note_title_to_filename(f"{note.path.stem}--{sec.heading}")
            rel = self._graph_rel(f"Fragments/{slug}.md")
            out_path = self.graph_vault / rel

            folder_tag = _folder_tag(note.folder)
            tags_yaml = "\n".join(
                f"  - {t}"
                for t in [
                    "cg/fragment",
                    "cg/node",
                    folder_tag,
                    f"cg/source/{_slug_tag(note.path.stem)}",
                ]
            )
            related = self._related_fragment_links(
                note.path.stem,
                sections,
                sec_idx,
                from_rel=rel,
            )
            graph_section = self._graph_section(note, sec.heading, source_ref)
            llm_summary = ""
            if self.summarizer is not None:
                summary = self.summarizer.summarize(sec.heading, sec.body)
                if summary:
                    llm_summary = f'cg_llm_summary: "{_escape_yaml(summary)}"\n'

            content = f"""---
cg_type: fragment
cg_id: {frag_id}
source_vault: "{self.source_vault}"
source_rel_path: "{note.rel_path}"
source_heading: "{_escape_yaml(sec.heading)}"
{llm_summary}tags:
{tags_yaml}
---

# {sec.heading}

{sec.body}

## Graph

{graph_section}

## Related

{related}
"""
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content, encoding="utf-8")
            fragment_paths.append(rel)
            self._registry.setdefault("fragments", {})[rel] = {
                "id": frag_id,
                "source_rel_path": note.rel_path,
                "heading": sec.heading,
            }

        self._registry.setdefault("sources", {})[note.rel_path] = {
            "title": note.title,
            "fragments": fragment_paths,
            "folder": note.folder,
            "stub": self._stub_cache.get(note.rel_path),
        }
        return fragment_paths

    def write_moc(self) -> Path:
        """Rewrite MOC.md listing sources and fragments (all wikilinks within graph vault)."""
        self.graph_root.mkdir(parents=True, exist_ok=True)
        moc_path = self.graph_root / "MOC.md"

        vault_note = ""
        if self.separate_vaults:
            vault_note = (
                f"\nSource vault: `{self.source_vault}`  \n"
                f"Graph vault: `{self.graph_vault}`\n"
            )

        lines = [
            "---",
            "tags:",
            "  - cg/moc",
            "  - cg/node",
            "---",
            "",
            "# Context Graph",
            "",
            "Generated knowledge graph (Obsidian-native: wikilinks + tags).",
            vault_note,
            "",
            "## Sources",
            "",
        ]
        for rel, info in sorted(self._registry.get("sources", {}).items()):
            stub = info.get("stub")
            if stub:
                lines.append(
                    f"- {wikilink_for_path(stub)} — {info.get('title', rel)} "
                    f"`(source: {rel})`"
                )
            else:
                lines.append(f"- `{rel}` — {info.get('title', rel)}")
            for frag_rel in info.get("fragments", []):
                lines.append(f"  - {wikilink_for_path(frag_rel)}")

        lines.extend(
            [
                "",
                "## Browse by tag",
                "",
                "- `#cg/fragment` — section-level chunks",
                "- `#cg/source/...` — group fragments by source note (tag on fragments)",
                "- `#cg/moc` — this index",
                "",
                "## Search",
                "",
                "```bash",
                f'uv run python scripts/query_obsidian_vault.py --vault "{self.graph_vault}" '
                '-q "your query" --tag cg/fragment --hops 1',
                "```",
                "",
            ]
        )
        moc_path.write_text("\n".join(lines), encoding="utf-8")
        return moc_path

    def _graph_rel(self, subpath: str) -> str:
        """Path relative to graph_vault root."""
        try:
            return str((self.graph_root / subpath).relative_to(self.graph_vault))
        except ValueError:
            return subpath

    def _source_reference(self, note: RawVaultNote) -> dict:
        """Resolve how fragments point back to the original note."""
        if not self.separate_vaults:
            return {
                "wikilink": wikilink_for_path(note.rel_path),
                "heading_wikilink": None,
                "path_line": f"`{note.rel_path}`",
            }

        if self.link_mode == "symlink":
            link_path = f"Sources/{note.rel_path}"
            if link_path.lower().endswith(".md"):
                link_path = link_path[:-3]
            return {
                "wikilink": f"[[{link_path}]]",
                "heading_wikilink": lambda h: f"[[{link_path}#{h}]]",
                "path_line": f"`{self.source_vault / note.rel_path}`",
            }

        if self.link_mode == "stub":
            stub_rel = self._ensure_source_stub(note)
            return {
                "wikilink": wikilink_for_path(stub_rel),
                "heading_wikilink": lambda h: (
                    f"{wikilink_for_path(stub_rel)} (§ {_escape_yaml(h)})"
                ),
                "path_line": (
                    f"Source vault `{self.source_vault}` → `{note.rel_path}`"
                ),
            }

        # frontmatter only
        return {
            "wikilink": None,
            "heading_wikilink": None,
            "path_line": f"`{self.source_vault / note.rel_path}`",
        }

    def _ensure_source_stub(self, note: RawVaultNote) -> str:
        cached_rel = self._stub_cache.get(note.rel_path)
        if cached_rel:
            cached_out = self.graph_vault / cached_rel
            if cached_out.exists():
                return cached_rel

        slug = source_rel_to_stub_stem(note.rel_path)
        rel = self._graph_rel(f"Sources/{slug}.md")
        out = self.graph_vault / rel
        preview = note.body[:400].replace("\n", " ")
        if len(note.body) > 400:
            preview += "…"

        content = f"""---
cg_type: source
source_vault: "{self.source_vault}"
source_rel_path: "{note.rel_path}"
tags:
  - cg/source
  - cg/node
  - {_folder_tag(note.folder)}
---

# {note.title}

Index card for a note in the **source vault** (not duplicated here).

- **Path**: `{note.rel_path}`
- **Vault**: `{self.source_vault}`

## Preview

{preview}
"""
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        self._stub_cache[note.rel_path] = rel
        return rel

    def _graph_section(self, note: RawVaultNote, heading: str, source_ref: dict) -> str:
        lines = []
        if source_ref.get("wikilink"):
            hl = source_ref["heading_wikilink"]
            if callable(hl):
                lines.append(f"- **Source**: {hl(heading)}")
            else:
                lines.append(f"- **Source**: {source_ref['wikilink']}")
        lines.append(f"- **Original**: {source_ref['path_line']}")
        if self.separate_vaults:
            lines.append(f"- **Source vault**: `{self.source_vault}`")
        return "\n".join(lines)

    def _related_fragment_links(
        self,
        note_stem: str,
        sections: list,
        section_index: int,
        *,
        from_rel: str,
    ) -> str:
        indices = related_section_indices(
            section_index,
            len(sections),
            mode=self.related_mode,
            topk=self.related_topk,
        )
        links = []
        for j in indices:
            sec = sections[j]
            slug = note_title_to_filename(f"{note_stem}--{sec.heading}")
            rel = self._graph_rel(f"Fragments/{slug}.md")
            links.append(f"- {wikilink_for_path(rel, from_rel=from_rel)}")
        return "\n".join(links) if links else "_None._"


def _folder_tag(folder: str) -> str:
    if not folder:
        return "cg/folder/root"
    return "cg/folder/" + _slug_tag(folder.replace("/", "-"))


def _slug_tag(text: str) -> str:
    import re

    s = re.sub(r"[^\w\-]+", "-", text.lower()).strip("-")
    return s[:60] or "unknown"


def _escape_yaml(value: str) -> str:
    return value.replace('"', '\\"')
