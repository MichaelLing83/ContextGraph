"""Re-embed all graph nodes using text-embedding-3-large (3072 dim).

Replaces all existing embeddings (256/1536 dim) and backfills missing ones.
Uses batch API calls for efficiency.

Usage:
    python scripts/reembed_all_nodes.py [--batch-size 50] [--dry-run]
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parent.parent
load_dotenv(project_root / ".env")
sys.path.insert(0, str(project_root))

from neo4j import GraphDatabase
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Node label -> text field(s) to embed
NODE_TEXT_FIELDS = {
    "Fragment": ["description"],
    "Trajectory": ["summary"],
    "Strategy": ["rule_text"],
    "CanonicalRule": ["rule_text"],
    "PlaybookEntry": ["text"],
    "Community": ["summary"],
    "ErrorPattern": ["error_type", "error_keywords"],
    "ProblemSummary": ["summary_text"],
}

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")
if not NEO4J_PASSWORD:
    logger.error("NEO4J_PASSWORD environment variable is required (set it in .env)")
    sys.exit(1)


def get_text_for_node(props: dict, fields: list) -> str:
    """Extract text to embed from node properties."""
    parts = []
    for field in fields:
        val = props.get(field)
        if val is None:
            continue
        if isinstance(val, list):
            parts.append(" ".join(str(v) for v in val))
        else:
            parts.append(str(val))
    text = " ".join(parts).strip()
    # OpenAI embedding API has ~8191 token limit; truncate long texts
    if len(text) > 20000:
        text = text[:20000]
    return text


def embed_batch(client: OpenAI, texts: list, model: str = "text-embedding-3-large") -> list:
    """Call OpenAI embedding API for a batch of texts."""
    # Filter empty texts
    non_empty = [(i, t) for i, t in enumerate(texts) if t.strip()]
    if not non_empty:
        return [None] * len(texts)

    indices, batch_texts = zip(*non_empty)
    response = client.embeddings.create(input=list(batch_texts), model=model)
    embeddings = [item.embedding for item in response.data]

    # Map back to original indices
    result = [None] * len(texts)
    for idx, emb in zip(indices, embeddings):
        result[idx] = emb
    return result


def update_embeddings(driver, label: str, id_emb_pairs: list):
    """Batch update embeddings in Neo4j."""
    query = f"""
    UNWIND $pairs AS pair
    MATCH (n:{label} {{id: pair.id}})
    SET n.embedding = pair.embedding
    """
    with driver.session() as session:
        session.run(query, pairs=[{"id": id_, "embedding": emb} for id_, emb in id_emb_pairs])


def process_label(driver, client, label: str, fields: list, batch_size: int, dry_run: bool):
    """Process all nodes of a given label."""
    with driver.session() as session:
        total = session.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
    if total == 0:
        logger.info("  %s: 0 nodes, skipping", label)
        return

    logger.info("  %s: %d nodes to process", label, total)

    offset = 0
    processed = 0
    errors = 0

    while offset < total:
        # Fetch batch of nodes
        with driver.session() as session:
            results = session.run(
                f"MATCH (n:{label}) RETURN n.id AS id, properties(n) AS props "
                f"ORDER BY n.id SKIP $skip LIMIT $limit",
                skip=offset, limit=batch_size,
            ).data()

        if not results:
            break

        # Extract texts
        texts = [get_text_for_node(r["props"], fields) for r in results]
        ids = [r["id"] for r in results]

        if dry_run:
            non_empty = sum(1 for t in texts if t.strip())
            logger.info("    [dry-run] batch %d-%d: %d non-empty texts",
                        offset, offset + len(results), non_empty)
            offset += len(results)
            processed += len(results)
            continue

        # Embed
        try:
            embeddings = embed_batch(client, texts)
        except Exception as e:
            logger.error("    Embedding API error at offset %d: %s", offset, e)
            errors += 1
            if errors > 5:
                logger.error("    Too many errors, stopping %s", label)
                break
            time.sleep(5)
            continue

        # Update Neo4j
        pairs = [(id_, emb) for id_, emb in zip(ids, embeddings) if emb is not None]
        if pairs:
            update_embeddings(driver, label, pairs)

        processed += len(results)
        offset += len(results)
        logger.info("    %s: %d/%d done", label, processed, total)

        # Rate limit: ~3000 RPM for embedding, be conservative
        time.sleep(0.3)

    logger.info("  %s: completed (%d processed, %d errors)", label, processed, errors)


def update_vector_indexes(driver, dim: int):
    """Drop old vector indexes and recreate with new dimensions."""
    with driver.session() as session:
        # List existing vector indexes
        indexes = session.run("SHOW INDEXES YIELD name, type WHERE type = 'VECTOR' RETURN name").data()
        for idx in indexes:
            logger.info("  Dropping vector index: %s", idx["name"])
            session.run(f"DROP INDEX {idx['name']}")

        # Recreate vector indexes with 3072 dimensions
        vector_configs = [
            ("fragment_embedding", "Fragment", "embedding"),
            ("trajectory_embedding", "Trajectory", "embedding"),
            ("strategy_embedding", "Strategy", "embedding"),
            ("canonical_rule_embedding", "CanonicalRule", "embedding"),
            ("playbook_embedding", "PlaybookEntry", "embedding"),
            ("community_embedding", "Community", "embedding"),
            ("error_pattern_embedding", "ErrorPattern", "embedding"),
            ("problem_summary_embedding", "ProblemSummary", "embedding"),
        ]
        for idx_name, label, prop in vector_configs:
            try:
                session.run(
                    f"CREATE VECTOR INDEX {idx_name} IF NOT EXISTS "
                    f"FOR (n:{label}) ON (n.{prop}) "
                    f"OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, `vector.similarity_function`: 'cosine'}}}}"
                )
                logger.info("  Created vector index: %s (%s, %d dim)", idx_name, label, dim)
            except Exception as e:
                logger.warning("  Index %s: %s", idx_name, e)


def main():
    parser = argparse.ArgumentParser(description="Re-embed all Neo4j nodes with text-embedding-3-large")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--labels", nargs="*", help="Only process these labels")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    api_base = os.environ.get("OPENAI_API_BASE", "https://api.chatanywhere.org")
    if not api_key and not args.dry_run:
        logger.error("OPENAI_API_KEY not set")
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url=api_base.rstrip("/") + "/v1")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    # Step 1: Update vector indexes to 3072 dim
    logger.info("=== Updating vector indexes to 3072 dimensions ===")
    if not args.dry_run:
        update_vector_indexes(driver, 3072)
    else:
        logger.info("  [dry-run] would drop and recreate vector indexes")

    # Step 2: Re-embed all nodes
    logger.info("=== Re-embedding all nodes ===")
    labels_to_process = args.labels or list(NODE_TEXT_FIELDS.keys())
    for label in labels_to_process:
        if label not in NODE_TEXT_FIELDS:
            logger.warning("Unknown label: %s", label)
            continue
        fields = NODE_TEXT_FIELDS[label]
        process_label(driver, client, label, fields, args.batch_size, args.dry_run)

    # Step 3: Verify
    logger.info("=== Verification ===")
    with driver.session() as session:
        for label in labels_to_process:
            if label not in NODE_TEXT_FIELDS:
                continue
            total = session.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
            with_emb = session.run(f"MATCH (n:{label}) WHERE n.embedding IS NOT NULL RETURN count(n) AS c").single()["c"]
            dim_r = session.run(f"MATCH (n:{label}) WHERE n.embedding IS NOT NULL RETURN size(n.embedding) AS d LIMIT 1").single()
            dim = dim_r["d"] if dim_r else "-"
            status = "✓" if with_emb == total else f"⚠ {total - with_emb} missing"
            logger.info("  %s: %d/%d embedded, dim=%s %s", label, with_emb, total, dim, status)

    driver.close()
    logger.info("Done.")


if __name__ == "__main__":
    main()
