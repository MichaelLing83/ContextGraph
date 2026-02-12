"""Memory Consolidator - periodic abstraction and cleanup."""

from typing import List, Dict, Any, Optional, TYPE_CHECKING
import uuid
import math
import logging

from agent_memory.models import Methodology, Fragment

if TYPE_CHECKING:
    from agent_memory.neo4j_store import Neo4jStore
    from agent_memory.embeddings import EmbeddingClient

logger = logging.getLogger(__name__)


class MemoryConsolidator:
    """
    Consolidates memory every N trajectories.

    Tasks:
    1. Abstract methodologies from successful fragments
    2. Merge similar nodes
    3. Update statistics
    4. Cleanup low-quality data
    """

    def __init__(
        self,
        store: Optional["Neo4jStore"],
        embedder: Optional["EmbeddingClient"],
        llm_client: Optional[Any] = None,
        consolidation_interval: int = 16,
    ):
        self.store = store
        self.embedder = embedder
        self.llm_client = llm_client
        self.consolidation_interval = consolidation_interval
        self._trajectory_count = 0

    def should_consolidate(self, trajectory_count: int) -> bool:
        """Check if consolidation should run based on trajectory count."""
        return trajectory_count > 0 and trajectory_count % self.consolidation_interval == 0

    def consolidate(self, batch_size: int = 16) -> Dict[str, int]:
        """
        Run consolidation tasks.

        Returns stats about what was consolidated.
        """
        stats = {
            "methodologies_created": 0,
            "nodes_merged": 0,
            "nodes_cleaned": 0,
        }

        if not self.store:
            return stats

        # 1. Abstract methodologies
        new_methods = self._abstract_methodologies()
        stats["methodologies_created"] = len(new_methods)

        # 2. Merge similar nodes
        merged = self._merge_similar_nodes(similarity_threshold=0.9)
        stats["nodes_merged"] = merged

        # 3. Update statistics
        self._update_statistics()

        # 4. Cleanup
        cleaned = self._cleanup()
        stats["nodes_cleaned"] = cleaned

        logger.info(f"Consolidation complete: {stats}")
        return stats

    def _abstract_methodologies(self) -> List[Methodology]:
        """
        Abstract methodologies from successful error recovery fragments.

        Groups similar fragments and creates methodology from common patterns.
        """
        if not self.store:
            return []

        # Get successful error recovery fragments
        query = """
        MATCH (t:Trajectory {success: true})-[:HAS_FRAGMENT]->(f:Fragment)
        WHERE f.fragment_type = 'error_recovery'
        RETURN f
        ORDER BY f.outcome DESC
        LIMIT 100
        """

        results = self.store.execute_query(query)
        fragments = [Fragment.from_dict(r["f"]) for r in results if "f" in r]

        if not fragments:
            return []

        # Group by similar descriptions (simple heuristic)
        groups = self._group_similar_fragments(fragments)

        # Create methodology for each group with enough examples
        new_methodologies = []
        for group in groups:
            if len(group) >= 3:  # Minimum 3 examples
                methodology = self._create_methodology_from_group(group)
                if methodology:
                    self.store.create_methodology(methodology)
                    # Create RESOLVED_BY edges from ErrorPatterns to this Methodology
                    self._link_methodology_to_errors(methodology, group)
                    new_methodologies.append(methodology)

        return new_methodologies

    def _group_similar_fragments(
        self,
        fragments: List[Fragment],
        similarity_threshold: float = 0.8,
    ) -> List[List[Fragment]]:
        """Group fragments by similarity."""
        if not fragments:
            return []

        # Use embeddings if available
        if all(f.embedding for f in fragments):
            return self._group_by_embedding(fragments, similarity_threshold)

        # Fallback: group by description keywords
        return self._group_by_keywords(fragments)

    def _group_by_embedding(
        self,
        fragments: List[Fragment],
        threshold: float,
    ) -> List[List[Fragment]]:
        """Group fragments by embedding similarity."""
        groups: List[List[Fragment]] = []
        used = set()

        for i, frag in enumerate(fragments):
            if i in used:
                continue

            group = [frag]
            used.add(i)

            for j, other in enumerate(fragments):
                if j in used:
                    continue
                if frag.embedding and other.embedding:
                    sim = self._calculate_similarity(frag.embedding, other.embedding)
                    if sim >= threshold:
                        group.append(other)
                        used.add(j)

            groups.append(group)

        return groups

    def _group_by_keywords(self, fragments: List[Fragment]) -> List[List[Fragment]]:
        """Group fragments by keyword overlap."""
        groups: List[List[Fragment]] = []
        used = set()

        for i, frag in enumerate(fragments):
            if i in used:
                continue

            keywords = set(frag.description.lower().split())
            group = [frag]
            used.add(i)

            for j, other in enumerate(fragments):
                if j in used:
                    continue
                other_keywords = set(other.description.lower().split())
                overlap = len(keywords & other_keywords) / max(len(keywords | other_keywords), 1)
                if overlap > 0.5:
                    group.append(other)
                    used.add(j)

            groups.append(group)

        return groups

    def _group_by_error_type(self, fragments_data: List[Dict]) -> Dict[str, List[Dict]]:
        """Group fragment data by error type."""
        groups: Dict[str, List[Dict]] = {}
        for data in fragments_data:
            error_type = data.get("error_type", "Unknown")
            if error_type not in groups:
                groups[error_type] = []
            groups[error_type].append(data)
        return groups

    def _create_methodology_from_group(self, group: List[Fragment]) -> Optional[Methodology]:
        """Create a methodology from a group of similar fragments."""
        if not group:
            return None

        # Extract common patterns
        actions = []
        for frag in group:
            actions.extend(frag.action_sequence)

        # Find most common action sequence
        action_str = ", ".join(list(dict.fromkeys(actions))[:5])

        # Create situation description
        situation = f"When encountering errors similar to: {group[0].description[:100]}"

        # Create strategy
        strategy = f"Apply action sequence: {action_str}"

        return Methodology(
            id=f"meth_{uuid.uuid4().hex[:12]}",
            situation=situation,
            strategy=strategy,
            confidence=0.8,  # Initial confidence
            success_count=len(group),
            failure_count=0,
            source_fragment_ids=[f.id for f in group],
        )

    def _link_methodology_to_errors(
        self,
        methodology: Methodology,
        fragments: List[Fragment],
    ) -> None:
        """Create RESOLVED_BY edges from ErrorPatterns to Methodology."""
        if not self.store:
            return

        # Link methodology to error patterns associated with its source fragments
        query = """
        MATCH (f:Fragment)-[:CAUSED_ERROR]->(e:ErrorPattern)
        WHERE f.id IN $fragment_ids
        MATCH (m:Methodology {id: $methodology_id})
        MERGE (e)-[:RESOLVED_BY]->(m)
        """
        self.store.execute_write(query, {
            "fragment_ids": [f.id for f in fragments],
            "methodology_id": methodology.id,
        })

    def _merge_similar_nodes(self, similarity_threshold: float = 0.9) -> int:
        """Merge nodes that are very similar to reduce redundancy."""
        if not self.store:
            return 0

        merged_count = 0

        # Merge similar methodologies
        query = """
        MATCH (m1:Methodology), (m2:Methodology)
        WHERE id(m1) < id(m2)
          AND m1.situation = m2.situation
        RETURN m1.id as id1, m2.id as id2
        LIMIT 10
        """

        results = self.store.execute_query(query)
        for r in results:
            # Merge m2 into m1
            merge_query = """
            MATCH (m1:Methodology {id: $id1}), (m2:Methodology {id: $id2})
            SET m1.success_count = m1.success_count + m2.success_count,
                m1.failure_count = m1.failure_count + m2.failure_count
            DETACH DELETE m2
            """
            self.store.execute_write(merge_query, {"id1": r["id1"], "id2": r["id2"]})
            merged_count += 1

        return merged_count

    def _update_statistics(self) -> None:
        """Update statistics on nodes."""
        if not self.store:
            return

        # Update error pattern frequencies
        query = """
        MATCH (f:Fragment)-[:CAUSED_ERROR]->(e:ErrorPattern)
        WITH e, count(f) as cnt
        SET e.frequency = cnt
        """
        try:
            self.store.execute_write(query)
        except Exception as e:
            logger.warning(f"Failed to update statistics: {e}")

    def _cleanup(self) -> int:
        """Remove low-quality or stale data."""
        if not self.store:
            return 0

        cleaned = 0

        # Remove methodologies with low confidence and no recent usage
        query = """
        MATCH (m:Methodology)
        WHERE m.confidence < 0.2
          AND m.success_count + m.failure_count > 5
        WITH m, count(m) as cnt
        DETACH DELETE m
        RETURN cnt
        """

        try:
            results = self.store.execute_query(query)
            if results:
                cleaned = results[0].get("cnt", 0)
        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")

        return cleaned

    def _calculate_similarity(self, emb1: List[float], emb2: List[float]) -> float:
        """Calculate cosine similarity between two embeddings."""
        if not emb1 or not emb2 or len(emb1) != len(emb2):
            return 0.0

        dot_product = sum(a * b for a, b in zip(emb1, emb2))
        norm1 = math.sqrt(sum(a * a for a in emb1))
        norm2 = math.sqrt(sum(b * b for b in emb2))

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (norm1 * norm2)
