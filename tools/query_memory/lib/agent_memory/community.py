"""Community Detection — Label Propagation + community summaries.

Uses Neo4j GDS Label Propagation if available, otherwise falls back to
Python-side iterative label propagation on the exported adjacency.

Communities enable hierarchical retrieval: query at the community level first,
then drill down to individual fragments.
"""

from typing import List, Dict, Optional, Set, TYPE_CHECKING
from collections import Counter, defaultdict
import uuid
import logging

from agent_memory.models import Community

if TYPE_CHECKING:
    from agent_memory.neo4j_store import Neo4jStore
    from agent_memory.embeddings import EmbeddingClient

logger = logging.getLogger(__name__)


class CommunityDetector:
    """Detect and manage communities of related graph nodes."""

    def __init__(
        self,
        store: Optional["Neo4jStore"],
        embedder: Optional["EmbeddingClient"],
        min_community_size: int = 3,
    ):
        self.store = store
        self.embedder = embedder
        self.min_community_size = min_community_size
        self._has_gds: Optional[bool] = None

    def detect_communities(self) -> int:
        """Run community detection on the fragment graph.

        Returns the number of communities created.
        """
        if not self.store:
            return 0

        # Try GDS first, fallback to Python
        if self._check_gds():
            assignments = self._gds_community_detection()
        else:
            assignments = self._fallback_community_detection()

        if not assignments:
            return 0

        # Group fragments by community
        communities = self._build_communities(assignments)

        # Generate summaries and embeddings
        for community in communities:
            self._generate_community_summary(community)

        # Write to Neo4j
        self._write_communities(communities)

        logger.info("Detected %d communities", len(communities))
        return len(communities)

    def assign_new_node(self, node_id: str) -> Optional[int]:
        """Incrementally assign a new node to the majority-neighbor community.

        Returns the community_id assigned, or None if no neighbors.
        """
        if not self.store:
            return None

        # Find neighbors and their community_ids
        query = """
        MATCH (f:Fragment {id: $node_id})
        OPTIONAL MATCH (f)-[:CAUSED_ERROR]->()<-[:CAUSED_ERROR]-(neighbor:Fragment)
        WHERE neighbor.community_id IS NOT NULL AND neighbor.community_id >= 0
        WITH f, collect(neighbor.community_id) AS neighbor_communities

        OPTIONAL MATCH (t:Trajectory)-[:HAS_FRAGMENT]->(f)
        WITH f, neighbor_communities, t
        OPTIONAL MATCH (t)-[:HAS_FRAGMENT]->(sibling:Fragment)
        WHERE sibling.id <> f.id AND sibling.community_id IS NOT NULL AND sibling.community_id >= 0
        WITH f, neighbor_communities + collect(sibling.community_id) AS all_communities

        RETURN all_communities
        """
        try:
            results = self.store.execute_query(query, {"node_id": node_id})
        except Exception as e:
            logger.debug("assign_new_node failed: %s", e)
            return None

        if not results:
            return None

        all_communities = results[0].get("all_communities", [])
        if not all_communities:
            return None

        # Majority vote
        counter = Counter(all_communities)
        majority_id = counter.most_common(1)[0][0]

        # Update the node
        try:
            self.store.execute_write(
                "MATCH (f:Fragment {id: $id}) SET f.community_id = $cid",
                {"id": node_id, "cid": majority_id},
            )
            # Add IN_COMMUNITY edge
            self.store.execute_write(
                """
                MATCH (f:Fragment {id: $id})
                MATCH (c:Community {community_id: $cid})
                MERGE (f)-[:IN_COMMUNITY]->(c)
                """,
                {"id": node_id, "cid": majority_id},
            )
        except Exception as e:
            logger.debug("Failed to assign node %s to community %d: %s", node_id, majority_id, e)
            return None

        return majority_id

    def refresh_communities(self) -> int:
        """Full re-detection of communities. Run periodically.

        Detects new communities first, then replaces old ones only on success.
        Returns the number of communities created.
        """
        if not self.store:
            return 0

        try:
            count = self.detect_communities()
        except Exception as e:
            logger.warning("Community refresh failed; existing communities retained: %s", e)
            return 0

        # Only clear old communities after successful detection. Detection
        # writes new community_id values onto Fragment nodes and re-creates
        # the Community + IN_COMMUNITY edges, but old IN_COMMUNITY edges from
        # a previous run are not overwritten — MERGE on the new edges is a
        # no-op for the old ones. Sweep stale edges (those whose endpoint
        # Community's id no longer matches the Fragment's current
        # community_id) and then drop orphan Community nodes.
        if count > 0:
            try:
                self.store.execute_write(
                    """
                    MATCH (f:Fragment)-[r:IN_COMMUNITY]->(c:Community)
                    WHERE f.community_id IS NULL OR f.community_id <> c.community_id
                    DELETE r
                    """
                )
                self.store.execute_write(
                    "MATCH (c:Community) WHERE NOT EXISTS { MATCH (:Fragment)-[:IN_COMMUNITY]->(c) } DETACH DELETE c"
                )
            except Exception as e:
                logger.debug("Old community cleanup note: %s", e)

        return count

    # ------------------------------------------------------------------
    # GDS-based detection
    # ------------------------------------------------------------------

    def _check_gds(self) -> bool:
        """Check if Neo4j GDS (Graph Data Science) is available."""
        if self._has_gds is not None:
            return self._has_gds

        if not self.store:
            self._has_gds = False
            return False

        try:
            self.store.execute_query("RETURN gds.version() AS version")
            self._has_gds = True
        except Exception:
            self._has_gds = False

        logger.debug("GDS available: %s", self._has_gds)
        return self._has_gds

    def _gds_community_detection(self) -> Dict[str, int]:
        """Run Label Propagation using Neo4j GDS."""
        if not self.store:
            return {}

        try:
            # Project fragment-to-fragment edges via shared ErrorPatterns and Trajectories.
            # Direct projection of CAUSED_ERROR/HAS_FRAGMENT won't work because those
            # connect Fragment to non-Fragment nodes (ErrorPattern/Trajectory).
            self.store.execute_write("""
                CALL gds.graph.project.cypher(
                    'fragment_graph',
                    'MATCH (f:Fragment) RETURN id(f) AS id',
                    'MATCH (f1:Fragment)-[:CAUSED_ERROR]->(:ErrorPattern)<-[:CAUSED_ERROR]-(f2:Fragment)
                     WHERE id(f1) < id(f2)
                     RETURN id(f1) AS source, id(f2) AS target
                     UNION
                     MATCH (t:Trajectory)-[:HAS_FRAGMENT]->(f1:Fragment),
                           (t)-[:HAS_FRAGMENT]->(f2:Fragment)
                     WHERE id(f1) < id(f2)
                     RETURN id(f1) AS source, id(f2) AS target'
                )
            """)

            # Run label propagation
            results = self.store.execute_query("""
                CALL gds.labelPropagation.stream('fragment_graph')
                YIELD nodeId, communityId
                RETURN gds.util.asNode(nodeId).id AS node_id, communityId AS community_id
            """)

            # Cleanup projection
            self.store.execute_write("CALL gds.graph.drop('fragment_graph')")

            assignments = {r["node_id"]: r["community_id"] for r in results}

            # Persist community_id back onto Fragment nodes — downstream
            # `_generate_community_summary` and `_write_communities` query by
            # `f.community_id`, so without this writeback GDS communities are
            # silently empty. The Python fallback path already writes back at
            # the end of `_fallback_community_detection`.
            if assignments:
                rows = [
                    {"id": node_id, "cid": cid}
                    for node_id, cid in assignments.items()
                ]
                try:
                    self.store.execute_write(
                        """
                        UNWIND $rows AS row
                        MATCH (f:Fragment {id: row.id})
                        SET f.community_id = row.cid
                        """,
                        {"rows": rows},
                    )
                except Exception as e:
                    logger.warning("Failed to write GDS community labels: %s", e)

            return assignments

        except Exception as e:
            logger.warning("GDS community detection failed, using fallback: %s", e)
            # Cleanup projection on failure
            try:
                self.store.execute_write("CALL gds.graph.drop('fragment_graph')")
            except Exception:
                pass
            return self._fallback_community_detection()

    # ------------------------------------------------------------------
    # Python fallback detection
    # ------------------------------------------------------------------

    def _fallback_community_detection(self) -> Dict[str, int]:
        """Python-side iterative label propagation on exported adjacency."""
        if not self.store:
            return {}

        # Export adjacency: Fragment→ErrorPattern→Fragment and Fragment→Trajectory→Fragment
        query = """
        MATCH (f1:Fragment)-[:CAUSED_ERROR]->(e:ErrorPattern)<-[:CAUSED_ERROR]-(f2:Fragment)
        WHERE f1.id < f2.id
        RETURN f1.id AS src, f2.id AS dst, 'error' AS rel_type

        UNION

        MATCH (t:Trajectory)-[:HAS_FRAGMENT]->(f1:Fragment)
        WITH t, f1
        MATCH (t)-[:HAS_FRAGMENT]->(f2:Fragment)
        WHERE f1.id < f2.id
        RETURN f1.id AS src, f2.id AS dst, 'trajectory' AS rel_type
        """
        try:
            edges = self.store.execute_query(query)
        except Exception as e:
            logger.debug("Adjacency export failed: %s", e)
            return {}

        if not edges:
            return {}

        # Build adjacency list
        neighbors: Dict[str, Set[str]] = defaultdict(set)
        all_nodes: Set[str] = set()
        for edge in edges:
            src, dst = edge["src"], edge["dst"]
            neighbors[src].add(dst)
            neighbors[dst].add(src)
            all_nodes.add(src)
            all_nodes.add(dst)

        # Initialize: each node is its own community
        labels: Dict[str, int] = {}
        for i, node in enumerate(sorted(all_nodes)):
            labels[node] = i

        # Iterate until convergence (max 10 iterations)
        for iteration in range(10):
            changed = False
            for node in all_nodes:
                nbrs = neighbors.get(node, set())
                if not nbrs:
                    continue

                # Find most common label among neighbors
                label_counts: Counter = Counter()
                for nbr in nbrs:
                    label_counts[labels[nbr]] += 1

                most_common = label_counts.most_common(1)[0][0]
                if labels[node] != most_common:
                    labels[node] = most_common
                    changed = True

            if not changed:
                logger.debug("Label propagation converged at iteration %d", iteration + 1)
                break

        # Write labels back to Neo4j in bulk
        rows = [{"id": node_id, "cid": community_id} for node_id, community_id in labels.items()]
        if rows:
            try:
                self.store.execute_write(
                    """
                    UNWIND $rows AS row
                    MATCH (f:Fragment {id: row.id})
                    SET f.community_id = row.cid
                    """,
                    {"rows": rows},
                )
            except Exception as e:
                logger.debug("Failed to write fallback community labels: %s", e)

        return labels

    # ------------------------------------------------------------------
    # Community building and writing
    # ------------------------------------------------------------------

    def _build_communities(self, assignments: Dict[str, int]) -> List[Community]:
        """Build Community objects from node→community_id assignments."""
        # Group by community_id
        groups: Dict[int, List[str]] = defaultdict(list)
        for node_id, community_id in assignments.items():
            groups[community_id].append(node_id)

        communities = []
        for community_id, member_ids in groups.items():
            if len(member_ids) < self.min_community_size:
                continue

            community = Community(
                id=f"comm_{uuid.uuid4().hex[:12]}",
                community_id=community_id,
                summary="",  # Will be filled by _generate_community_summary
                node_count=len(member_ids),
            )
            communities.append(community)

        return communities

    def _generate_community_summary(self, community: Community) -> None:
        """Generate a summary for a community from its member fragments."""
        if not self.store:
            return

        # Get fragment descriptions and error types for this community
        query = """
        MATCH (f:Fragment)
        WHERE f.community_id = $cid
        OPTIONAL MATCH (f)-[:CAUSED_ERROR]->(e:ErrorPattern)
        RETURN collect(DISTINCT f.description) AS descriptions,
               collect(DISTINCT e.error_type) AS error_types
        LIMIT 1
        """
        try:
            results = self.store.execute_query(query, {"cid": community.community_id})
        except Exception:
            return

        if not results:
            return

        descriptions = results[0].get("descriptions", [])
        error_types = [et for et in results[0].get("error_types", []) if et]
        community.error_types = error_types

        # Build summary from descriptions (take first 5)
        desc_sample = [d for d in descriptions[:5] if d]
        if desc_sample:
            community.summary = f"Community of {community.node_count} fragments. "
            community.summary += f"Topics: {'; '.join(desc_sample[:3])}. "
            if error_types:
                community.summary += f"Error types: {', '.join(error_types[:3])}."

        # Generate embedding for the summary
        if self.embedder and community.summary:
            community.embedding = self.embedder.embed(community.summary)

    def _write_communities(self, communities: List[Community]) -> None:
        """Write Community nodes and IN_COMMUNITY edges to Neo4j."""
        if not self.store:
            return

        for community in communities:
            # Create community node
            query = """
            MERGE (c:Community {community_id: $community_id})
            ON CREATE SET
                c.id = $id,
                c.summary = $summary,
                c.node_count = $node_count,
                c.error_types = $error_types,
                c.embedding = $embedding
            ON MATCH SET
                c.summary = $summary,
                c.node_count = $node_count,
                c.error_types = $error_types,
                c.embedding = $embedding
            """
            try:
                self.store.execute_write(query, community.to_dict())
            except Exception as e:
                logger.debug("Failed to write community %s: %s", community.id, e)
                continue

            # Create IN_COMMUNITY edges from member fragments
            link_query = """
            MATCH (f:Fragment)
            WHERE f.community_id = $cid
            MATCH (c:Community {community_id: $cid})
            MERGE (f)-[:IN_COMMUNITY]->(c)
            """
            try:
                self.store.execute_write(link_query, {"cid": community.community_id})
            except Exception as e:
                logger.debug("Failed to link community members: %s", e)
