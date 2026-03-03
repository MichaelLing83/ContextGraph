#!/usr/bin/env python3
"""Idempotent migration script for Zep-optimized schema (v2).

Creates vector indexes, BM25 full-text indexes, and backfills
error_keywords_text on existing ErrorPattern nodes.

Safe to run multiple times — all operations use IF NOT EXISTS.
"""

import os
import sys
import logging

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from agent_memory.neo4j_store import Neo4jStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def migrate(store: Neo4jStore, vector_dimensions: int = 1536) -> None:
    """Run all v2 schema migrations."""

    # 1. Re-run init_schema (idempotent) with vector indexes
    logger.info("Running init_schema with vector_dimensions=%d ...", vector_dimensions)
    store.init_schema(vector_dimensions=vector_dimensions)

    # 2. Backfill error_keywords_text on existing ErrorPattern nodes
    logger.info("Backfilling error_keywords_text on ErrorPattern nodes ...")
    backfill_query = """
    MATCH (e:ErrorPattern)
    WHERE e.error_keywords_text IS NULL AND e.error_keywords IS NOT NULL
    SET e.error_keywords_text = reduce(s = '', kw IN e.error_keywords | s + ' ' + kw)
    RETURN count(e) AS updated
    """
    try:
        results = store.execute_query(backfill_query)
        updated = results[0]["updated"] if results else 0
        logger.info("Backfilled error_keywords_text on %d ErrorPattern nodes", updated)
    except Exception as e:
        logger.warning("Backfill failed (may be empty graph): %s", e)

    # 3. Add community_id property to existing Fragment nodes (default null)
    logger.info("Ensuring community_id property exists on fragments ...")
    community_query = """
    MATCH (f:Fragment) WHERE f.community_id IS NULL
    SET f.community_id = -1
    RETURN count(f) AS updated
    """
    try:
        results = store.execute_query(community_query)
        updated = results[0]["updated"] if results else 0
        logger.info("Set default community_id on %d Fragment nodes", updated)
    except Exception as e:
        logger.warning("Community ID backfill note: %s", e)

    logger.info("Migration complete.")


def main():
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    dims = int(os.environ.get("VECTOR_DIMENSIONS", "1536"))

    logger.info("Connecting to Neo4j at %s ...", uri)
    store = Neo4jStore(uri=uri, auth=(user, password))

    if not store.verify_connectivity():
        logger.error("Cannot connect to Neo4j. Is it running?")
        sys.exit(1)

    try:
        migrate(store, vector_dimensions=dims)
    finally:
        store.close()


if __name__ == "__main__":
    main()
