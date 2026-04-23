"""Export all nodes from the Neo4j context graph to a flat JSON file.

Usage:
    uv run python scripts/export_nodes_to_json.py \
        --out context_graph_nodes.json

By default embedding/vector fields are stripped to keep the file small.
Pass --include-embeddings to keep them.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from neo4j import GraphDatabase


def is_embedding_field(key: str, value) -> bool:
    key_lower = key.lower()
    if "embedding" in key_lower or key_lower.endswith("_vec") or key_lower.endswith("_vector"):
        return True
    if isinstance(value, list) and len(value) > 64 and value and isinstance(value[0], (int, float)):
        return True
    return False


def to_jsonable(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--user", default=os.environ.get("NEO4J_USER", "neo4j"))
    parser.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", "contextgraph123"))
    parser.add_argument("--out", default="context_graph_nodes.json")
    parser.add_argument("--include-embeddings", action="store_true")
    args = parser.parse_args()

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    by_label: dict[str, list[dict]] = {}

    with driver.session() as session:
        labels = [r["label"] for r in session.run("CALL db.labels() YIELD label RETURN label ORDER BY label")]
        print(f"Labels: {labels}")
        for label in labels:
            result = session.run(f"MATCH (n:`{label}`) RETURN properties(n) AS props, id(n) AS nid")
            rows: list[dict] = []
            for rec in result:
                props = dict(rec["props"])
                if not args.include_embeddings:
                    props = {k: v for k, v in props.items() if not is_embedding_field(k, v)}
                props = {k: to_jsonable(v) for k, v in props.items()}
                props["_id"] = rec["nid"]
                rows.append(props)
            by_label[label] = rows
            print(f"  {label}: {len(rows)}")
    driver.close()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_uri": args.uri,
        "counts": {k: len(v) for k, v in by_label.items()},
        "nodes": by_label,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nWrote {out_path} ({out_path.stat().st_size / (1024 * 1024):.1f} MB)")


if __name__ == "__main__":
    main()
