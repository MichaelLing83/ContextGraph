"""Export Neo4j PlaybookEntry and CanonicalRule nodes to flat JSON for Agent-KB baseline.

Connects to the baseline Neo4j instance (port 7687, read-only) and exports
all PlaybookEntry and CanonicalRule nodes as flat records compatible with
Agent-KB's knowledge base structure.

Usage:
    uv run python scripts/baselines/agentkb_export.py export \
        --out data/baselines/agentkb_knowledge_base.json

Each record:
    {
        "id": "shr-00001",
        "text": "...",
        "category": "STRATEGIES AND HARD RULES",
        "source_type": "PlaybookEntry" | "CanonicalRule",
        "error_types": ["ImportError", "ModuleNotFoundError"],
        "member_count": 5,  # only for CanonicalRule
    }
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import typer
from neo4j import GraphDatabase

app = typer.Typer(help="Export Neo4j knowledge to flat JSON for Agent-KB baseline.")


def _connect(uri: str, user: str, password: str):
    """Create Neo4j driver."""
    return GraphDatabase.driver(uri, auth=(user, password))


def _export_playbook_entries(session) -> list[dict]:
    """Export all PlaybookEntry nodes."""
    query = """
    MATCH (p:PlaybookEntry)
    OPTIONAL MATCH (p)<-[:MERGED_INTO]-(s:Strategy)-[:DERIVED_FROM]->(t:Trajectory)
    WITH p, collect(DISTINCT t.instance_id) AS source_trajectories
    RETURN p.id AS id, p.text AS text, p.section AS section,
           p.prefix AS prefix, source_trajectories
    """
    results = session.run(query)
    records = []
    for rec in results:
        records.append({
            "id": rec["id"],
            "text": rec["text"] or "",
            "category": rec["section"] or "OTHERS",
            "prefix": rec["prefix"] or "misc",
            "source_type": "PlaybookEntry",
            "source_trajectories": rec["source_trajectories"] or [],
            "error_types": [],
            "member_count": 0,
        })
    return records


def _export_canonical_rules(session) -> list[dict]:
    """Export all CanonicalRule nodes with their error type connections."""
    query = """
    MATCH (cr:CanonicalRule)
    OPTIONAL MATCH (cr)-[:ADDRESSES_ERROR]->(ep:ErrorPattern)
    WITH cr, collect(DISTINCT ep.error_type) AS error_types
    OPTIONAL MATCH (s:Strategy)-[:MERGED_INTO]->(cr)
    OPTIONAL MATCH (s)-[:DERIVED_FROM]->(t:Trajectory)
    WITH cr, error_types, collect(DISTINCT t.instance_id) AS source_trajectories
    RETURN cr.id AS id, cr.rule_text AS text, cr.category AS category,
           cr.member_count AS member_count, cr.avg_confidence AS avg_confidence,
           error_types, source_trajectories
    """
    results = session.run(query)
    records = []
    for rec in results:
        records.append({
            "id": rec["id"],
            "text": rec["text"] or "",
            "category": rec["category"] or "OTHERS",
            "prefix": "",
            "source_type": "CanonicalRule",
            "source_trajectories": rec["source_trajectories"] or [],
            "error_types": rec["error_types"] or [],
            "member_count": rec["member_count"] or 0,
            "avg_confidence": rec["avg_confidence"] or 0.0,
        })
    return records


@app.command()
def export(
    uri: str = typer.Option(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        help="Neo4j bolt URI",
    ),
    user: str = typer.Option(
        os.environ.get("NEO4J_USER", "neo4j"),
        help="Neo4j username",
    ),
    password: str = typer.Option(
        os.environ.get("NEO4J_PASSWORD", "contextgraph123"),
        help="Neo4j password",
    ),
    out: str = typer.Option(
        "data/baselines/agentkb_knowledge_base.json",
        help="Output JSON file path",
    ),
    include_playbook: bool = typer.Option(True, help="Include PlaybookEntry nodes"),
    include_canonical: bool = typer.Option(True, help="Include CanonicalRule nodes"),
):
    """Export PlaybookEntry and CanonicalRule nodes to flat JSON."""
    driver = _connect(uri, user, password)

    all_records = []
    with driver.session() as session:
        if include_playbook:
            playbook_records = _export_playbook_entries(session)
            typer.echo(f"Exported {len(playbook_records)} PlaybookEntry nodes")
            all_records.extend(playbook_records)

        if include_canonical:
            canonical_records = _export_canonical_rules(session)
            typer.echo(f"Exported {len(canonical_records)} CanonicalRule nodes")
            all_records.extend(canonical_records)

    driver.close()

    # Write output
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "source_uri": uri,
        "export_type": "agentkb_baseline",
        "counts": {
            "total": len(all_records),
            "PlaybookEntry": sum(1 for r in all_records if r["source_type"] == "PlaybookEntry"),
            "CanonicalRule": sum(1 for r in all_records if r["source_type"] == "CanonicalRule"),
        },
        "records": all_records,
    }

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    size_mb = out_path.stat().st_size / (1024 * 1024)
    typer.echo(f"\nWrote {out_path} ({size_mb:.1f} MB, {len(all_records)} records)")


if __name__ == "__main__":
    app()
