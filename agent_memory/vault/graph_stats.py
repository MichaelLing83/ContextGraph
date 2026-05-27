"""Statistics for a built Obsidian graph vault."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from agent_memory.vault.wikilinks import extract_wikilinks, split_frontmatter


@dataclass
class CharStats:
    count: int = 0
    total: int = 0
    min: int = 0
    max: int = 0
    mean: float = 0.0
    median: float = 0.0

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "total": self.total,
            "min": self.min,
            "max": self.max,
            "mean": round(self.mean, 1),
            "median": round(self.median, 1),
        }


@dataclass
class GraphVaultStats:
    """Summary metrics after ``build_obsidian_graph.py``."""

    nodes: Dict[str, int] = field(default_factory=dict)
    fragment_body_chars: CharStats = field(default_factory=CharStats)
    edges: Dict[str, int] = field(default_factory=dict)
    notes_scanned: int = 0

    def to_dict(self) -> dict:
        return {
            "nodes": self.nodes,
            "fragment_body_chars": self.fragment_body_chars.to_dict(),
            "edges": self.edges,
            "notes_scanned": self.notes_scanned,
        }


def _body_char_count(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")
    _, body = split_frontmatter(text)
    return len(body.strip())


def compute_graph_vault_stats(
    graph_vault: Path,
    *,
    graph_root: Optional[Path] = None,
) -> GraphVaultStats:
    """
    Scan the graph vault on disk and compute node / edge / size statistics.

    Nodes: fragment, source stub, and other markdown notes under graph_root.
    Edges: each ``[[wikilink]]`` in graph notes (outgoing link).
    """
    graph_vault = graph_vault.resolve()
    root = (graph_root or graph_vault).resolve()

    stats = GraphVaultStats()
    fragment_chars: List[int] = []
    total_links = 0
    resolved_links = 0
    unique_edges: set[tuple[str, str]] = set()

    # Index all markdown paths for resolution (stem paths without .md)
    md_files: Dict[str, Path] = {}
    for path in graph_vault.rglob("*.md"):
        if ".obsidian" in path.parts:
            continue
        rel = str(path.relative_to(graph_vault)).replace("\\", "/")
        md_files[rel] = path
        stem = rel[:-3] if rel.lower().endswith(".md") else rel
        md_files.setdefault(stem, path)
        md_files.setdefault(Path(rel).stem, path)

    def resolve_target(target: str) -> bool:
        t = target.replace("\\", "/")
        if t.lower().endswith(".md"):
            t = t[:-3]
        candidates = {t, f"{t}.md", t.split("/")[-1], f"{t.split('/')[-1]}.md"}
        return any(c in md_files for c in candidates)

    for path in sorted(graph_vault.rglob("*.md")):
        if ".obsidian" in path.parts:
            continue
        rel = str(path.relative_to(graph_vault)).replace("\\", "/")
        stats.notes_scanned += 1

        if rel.startswith("Fragments/") or "/Fragments/" in rel:
            stats.nodes["fragments"] = stats.nodes.get("fragments", 0) + 1
            fragment_chars.append(_body_char_count(path))
        elif rel.startswith("Sources/") or "/Sources/" in rel:
            stats.nodes["source_stubs"] = stats.nodes.get("source_stubs", 0) + 1
        elif rel.endswith("MOC.md"):
            stats.nodes["moc"] = stats.nodes.get("moc", 0) + 1
        else:
            stats.nodes["other"] = stats.nodes.get("other", 0) + 1

        text = path.read_text(encoding="utf-8", errors="replace")
        for target, _ in extract_wikilinks(text):
            total_links += 1
            unique_edges.add((rel, target))
            if resolve_target(target):
                resolved_links += 1

    stats.nodes["total"] = sum(
        v for k, v in stats.nodes.items() if k != "total"
    )

    if fragment_chars:
        stats.fragment_body_chars = CharStats(
            count=len(fragment_chars),
            total=sum(fragment_chars),
            min=min(fragment_chars),
            max=max(fragment_chars),
            mean=statistics.mean(fragment_chars),
            median=statistics.median(fragment_chars),
        )

    stats.edges = {
        "wikilinks_total": total_links,
        "wikilinks_unique": len(unique_edges),
        "wikilinks_resolved": resolved_links,
        "wikilinks_unresolved": total_links - resolved_links,
    }
    return stats


def format_graph_stats(stats: GraphVaultStats) -> str:
    """Human-readable summary for logs / terminal."""
    n = stats.nodes
    c = stats.fragment_body_chars
    e = stats.edges
    lines = [
        "Graph vault statistics",
        f"  Notes scanned:     {stats.notes_scanned}",
        f"  Nodes (total):     {n.get('total', 0)}",
        f"    Fragments:       {n.get('fragments', 0)}",
        f"    Source stubs:    {n.get('source_stubs', 0)}",
        f"    MOC:             {n.get('moc', 0)}",
        f"    Other:           {n.get('other', 0)}",
    ]
    if c.count:
        lines.extend(
            [
                f"  Fragment body chars ({c.count} fragments):",
                f"    total:  {c.total:,}",
                f"    mean:   {c.mean:,.1f}",
                f"    median: {c.median:,.1f}",
                f"    min:    {c.min:,}",
                f"    max:    {c.max:,}",
            ]
        )
    lines.extend(
        [
            f"  Edges (wikilinks):",
            f"    total:      {e.get('wikilinks_total', 0):,}",
            f"    unique:     {e.get('wikilinks_unique', 0):,}",
            f"    resolved:   {e.get('wikilinks_resolved', 0):,}",
            f"    unresolved: {e.get('wikilinks_unresolved', 0):,}",
        ]
    )
    return "\n".join(lines)
