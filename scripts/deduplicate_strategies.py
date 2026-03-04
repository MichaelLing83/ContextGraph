"""Deduplicate Strategy nodes into CanonicalRule nodes with graph linking.

Loads all Strategy nodes + embeddings from Neo4j, clusters them by category
using greedy cosine similarity (threshold=0.88), creates CanonicalRule nodes
with MERGED_INTO edges (Strategy→CanonicalRule) and ADDRESSES_ERROR edges
(CanonicalRule→ErrorPattern) via graph traversal.

Usage:
    python scripts/deduplicate_strategies.py [--dry-run] [--threshold 0.88]
"""

import os
import sys
import uuid
import argparse
import logging
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Tuple

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

import numpy as np

from agent_memory.neo4j_store import Neo4jStore
from agent_memory.models import CanonicalRule, PLAYBOOK_SECTIONS
from agent_memory.strategy_extractor import CATEGORY_TO_PREFIX

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "contextgraph123")
NEO4J_AUTH = (NEO4J_USER, NEO4J_PASSWORD)


def load_strategies(store: Neo4jStore) -> List[Dict]:
    """Load all Strategy nodes with embeddings from Neo4j."""
    query = """
    MATCH (s:Strategy)
    RETURN s.id AS id, s.rule_text AS rule_text, s.category AS category,
           s.source_trajectory_id AS source_trajectory_id,
           s.source_repo AS source_repo, s.confidence AS confidence,
           s.embedding AS embedding
    """
    results = store.execute_query(query)
    logger.info("Loaded %d strategies from Neo4j", len(results))
    return results


def greedy_cluster(
    strategies: List[Dict],
    threshold: float = 0.88,
) -> List[List[Dict]]:
    """Greedy clustering by category with cosine similarity threshold.

    Groups strategies within the same category. For each strategy,
    assigns it to the first cluster whose centroid has cosine >= threshold,
    or creates a new cluster.
    """
    # Group by category first
    by_category: Dict[str, List[Dict]] = defaultdict(list)
    for s in strategies:
        by_category[s.get("category", "debugging")].append(s)

    all_clusters: List[List[Dict]] = []

    for category, cat_strategies in by_category.items():
        # Separate strategies with/without embeddings
        with_emb = [s for s in cat_strategies if s.get("embedding")]
        without_emb = [s for s in cat_strategies if not s.get("embedding")]

        # Each strategy without embedding becomes its own cluster
        for s in without_emb:
            all_clusters.append([s])

        # Greedy clustering for strategies with embeddings
        clusters: List[Tuple[np.ndarray, List[Dict]]] = []  # (centroid, members)

        for s in with_emb:
            vec = np.array(s["embedding"], dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm == 0:
                all_clusters.append([s])
                continue
            vec_normed = vec / norm

            # Find best matching cluster
            best_sim = -1.0
            best_idx = -1
            for i, (centroid, _) in enumerate(clusters):
                sim = float(np.dot(vec_normed, centroid))
                if sim > best_sim:
                    best_sim = sim
                    best_idx = i

            if best_sim >= threshold and best_idx >= 0:
                # Add to existing cluster and update centroid
                centroid, members = clusters[best_idx]
                members.append(s)
                # Update centroid as running average
                n = len(members)
                new_centroid = centroid * ((n - 1) / n) + vec_normed * (1 / n)
                c_norm = np.linalg.norm(new_centroid)
                if c_norm > 0:
                    new_centroid = new_centroid / c_norm
                clusters[best_idx] = (new_centroid, members)
            else:
                # Start new cluster
                clusters.append((vec_normed.copy(), [s]))

        for _, members in clusters:
            all_clusters.append(members)

        logger.info(
            "Category '%s': %d strategies → %d clusters",
            category,
            len(cat_strategies),
            len([c for c in clusters]) + len(without_emb),
        )

    return all_clusters


def create_canonical_rules(
    clusters: List[List[Dict]],
) -> List[Tuple[CanonicalRule, List[str]]]:
    """Create CanonicalRule objects from clusters.

    Returns list of (CanonicalRule, [strategy_ids]) tuples.
    """
    rules = []

    for cluster in clusters:
        if not cluster:
            continue

        # Use the first strategy's text as representative (cluster centroid)
        representative = cluster[0]
        category = representative.get("category", "debugging")
        prefix = CATEGORY_TO_PREFIX.get(category, "misc")
        section = PLAYBOOK_SECTIONS.get(prefix, "OTHERS")

        # Collect metadata from all members
        strategy_ids = [s["id"] for s in cluster]
        repos = list({s.get("source_repo", "") for s in cluster if s.get("source_repo")})
        confidences = [s.get("confidence", 0.8) for s in cluster]
        avg_confidence = sum(confidences) / len(confidences)

        # Use centroid embedding if available
        embeddings = [
            np.array(s["embedding"], dtype=np.float32)
            for s in cluster if s.get("embedding")
        ]
        embedding = None
        if embeddings:
            centroid = np.mean(embeddings, axis=0)
            c_norm = np.linalg.norm(centroid)
            if c_norm > 0:
                centroid = centroid / c_norm
            embedding = centroid.tolist()

        rule = CanonicalRule(
            id=f"rule_{uuid.uuid4().hex[:12]}",
            rule_text=representative["rule_text"],
            category=category,
            prefix=prefix,
            section=section,
            member_count=len(cluster),
            avg_confidence=round(avg_confidence, 3),
            source_repos=repos[:20],  # cap for Neo4j property size
            error_types=[],  # filled later via graph traversal
            embedding=embedding,
        )
        rules.append((rule, strategy_ids))

    return rules


def find_error_links(
    store: Neo4jStore,
    rule_id: str,
    strategy_ids: List[str],
) -> List[str]:
    """Find ErrorPattern types linked to strategies via graph traversal.

    Path: Strategy → DERIVED_FROM → Trajectory → HAS_FRAGMENT → Fragment → CAUSED_ERROR → ErrorPattern
    """
    query = """
    UNWIND $strategy_ids AS sid
    MATCH (s:Strategy {id: sid})-[:DERIVED_FROM]->(t:Trajectory)
          -[:HAS_FRAGMENT]->(f:Fragment)-[:CAUSED_ERROR]->(e:ErrorPattern)
    RETURN DISTINCT e.error_type AS error_type
    """
    try:
        results = store.execute_query(query, {"strategy_ids": strategy_ids})
        return [r["error_type"] for r in results if r.get("error_type")]
    except Exception as e:
        logger.debug("Error link query failed: %s", e)
        return []


def main():
    parser = argparse.ArgumentParser(description="Deduplicate strategies into canonical rules")
    parser.add_argument("--dry-run", action="store_true", help="Show clustering stats without writing to Neo4j")
    parser.add_argument("--threshold", type=float, default=0.88, help="Cosine similarity threshold for clustering")
    args = parser.parse_args()

    store = Neo4jStore(uri=NEO4J_URI, auth=NEO4J_AUTH)
    if not store.verify_connectivity():
        logger.error("Cannot connect to Neo4j at %s", NEO4J_URI)
        sys.exit(1)

    # Load strategies
    strategies = load_strategies(store)
    if not strategies:
        logger.warning("No strategies found in Neo4j")
        store.close()
        return

    # Cluster
    clusters = greedy_cluster(strategies, threshold=args.threshold)
    logger.info(
        "Clustering: %d strategies → %d clusters (threshold=%.2f)",
        len(strategies), len(clusters), args.threshold,
    )

    # Stats
    sizes = [len(c) for c in clusters]
    singletons = sum(1 for s in sizes if s == 1)
    logger.info(
        "Cluster sizes: min=%d, max=%d, avg=%.1f, singletons=%d (%.1f%%)",
        min(sizes), max(sizes), sum(sizes) / len(sizes),
        singletons, 100 * singletons / len(sizes),
    )

    if args.dry_run:
        logger.info("Dry run — not writing to Neo4j")
        # Show top 10 largest clusters
        top_clusters = sorted(clusters, key=len, reverse=True)[:10]
        for i, cluster in enumerate(top_clusters):
            logger.info(
                "  Cluster %d (%d members): %s",
                i + 1, len(cluster), cluster[0]["rule_text"][:80],
            )
        store.close()
        return

    # Create CanonicalRule objects
    rules_with_ids = create_canonical_rules(clusters)
    logger.info("Created %d CanonicalRule objects", len(rules_with_ids))

    # Write to Neo4j
    created = 0
    merged_into = 0
    addresses_error = 0

    for rule, strategy_ids in rules_with_ids:
        # Find linked error patterns via graph traversal
        error_types = find_error_links(store, rule.id, strategy_ids)
        rule.error_types = error_types[:20]  # cap

        # Create the CanonicalRule node
        store.create_canonical_rule(rule)
        created += 1

        # Create MERGED_INTO edges
        for sid in strategy_ids:
            store.link_strategy_to_canonical_rule(sid, rule.id)
            merged_into += 1

        # Create ADDRESSES_ERROR edges
        for et in error_types:
            store.link_canonical_rule_to_error(rule.id, et)
            addresses_error += 1

        if created % 100 == 0:
            logger.info("  Progress: %d/%d rules created", created, len(rules_with_ids))

    logger.info(
        "Done: %d CanonicalRules, %d MERGED_INTO edges, %d ADDRESSES_ERROR edges",
        created, merged_into, addresses_error,
    )

    store.close()


if __name__ == "__main__":
    main()
