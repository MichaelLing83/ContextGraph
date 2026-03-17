"""Ingest a playbook file into Neo4j.

Parses a playbook text file, embeds each entry, and stores them
as PlaybookEntry nodes in Neo4j.

Usage:
    python scripts/ingest_playbook.py --file playbooks/initial.txt
    python scripts/ingest_playbook.py --file playbooks/initial.txt --dry-run
"""

import os
import sys
import argparse
import logging
from pathlib import Path
from collections import Counter

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

from agent_memory.neo4j_store import Neo4jStore
from agent_memory.embeddings import get_embedding_client
from agent_memory.playbook import parse_playbook, PlaybookRetriever

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Ingest a playbook file into Neo4j")
    parser.add_argument("--file", required=True, help="Path to playbook text file")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, don't store")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASSWORD"))
    args = parser.parse_args()

    if not args.neo4j_password:
        logger.error("NEO4J_PASSWORD environment variable is required (set it in .env)")
        sys.exit(1)

    # Parse
    playbook_path = Path(args.file)
    if not playbook_path.exists():
        logger.error("File not found: %s", playbook_path)
        sys.exit(1)

    content = playbook_path.read_text(encoding="utf-8")
    entries = parse_playbook(content)

    # Report stats
    prefix_counts = Counter(e.prefix for e in entries)
    logger.info("Parsed %d entries from %s", len(entries), playbook_path)
    for prefix, count in sorted(prefix_counts.items()):
        logger.info("  [%s] %s: %d entries", prefix, e.section if (e := next((x for x in entries if x.prefix == prefix), None)) else "?", count)

    if args.dry_run:
        logger.info("Dry run — not storing in Neo4j")
        for entry in entries[:5]:
            logger.info("  %s: %s", entry.id, entry.text[:80])
        return

    # Connect and ingest
    api_key = os.environ.get("OPENAI_API_KEY")
    embedder = get_embedding_client("openai", api_key=api_key) if api_key else get_embedding_client("mock")

    store = Neo4jStore(uri=args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
    store.init_schema(vector_dimensions=embedder.dimensions)

    retriever = PlaybookRetriever(store, embedder)
    count = retriever.ingest(entries)

    logger.info("Ingested %d playbook entries into Neo4j", count)
    store.close()


if __name__ == "__main__":
    main()
