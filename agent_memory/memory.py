"""AgentMemory - Unified API for agent long-term memory."""

from typing import Optional, List
from dataclasses import dataclass, field
import logging

from agent_memory.models import State, Methodology, Fragment
from agent_memory.neo4j_store import Neo4jStore
from agent_memory.embeddings import get_embedding_client, EmbeddingClient
from agent_memory.writer import MemoryWriter, RawTrajectory
from agent_memory.retriever import MemoryRetriever, RetrievalResult
from agent_memory.consolidator import MemoryConsolidator
from agent_memory.loop_detector import LoopDetector, LoopInfo

logger = logging.getLogger(__name__)


@dataclass
class MemoryContext:
    """Context returned from memory query."""

    methodologies: List[Methodology] = field(default_factory=list)
    similar_fragments: List[Fragment] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def has_suggestions(self) -> bool:
        return bool(self.methodologies)


@dataclass
class MemoryStats:
    """Statistics from memory."""

    error_frequency: dict = field(default_factory=dict)
    failure_patterns: list = field(default_factory=list)
    total_trajectories: int = 0
    total_methodologies: int = 0


class AgentMemory:
    """
    Agent Long-term Memory - Unified API.

    Usage:
        memory = AgentMemory(neo4j_uri="bolt://localhost:7687", embedding_api_key="...")

        # During agent run
        context = memory.query(current_state)
        loop = memory.check_loop(state_history)

        # After trajectory
        memory.learn(trajectory)
    """

    def __init__(
        self,
        neo4j_uri: Optional[str] = None,
        neo4j_auth: tuple = ("neo4j", "password"),
        embedding_api_key: Optional[str] = None,
        consolidate_every: int = 16,
    ):
        # Initialize store
        if neo4j_uri:
            self.store = Neo4jStore(uri=neo4j_uri, auth=neo4j_auth)
            self.store.init_schema()
        else:
            self.store = None
            logger.warning("Running without Neo4j store (mock mode)")

        # Initialize embedder
        if embedding_api_key:
            self.embedder = get_embedding_client("openai", api_key=embedding_api_key)
        else:
            self.embedder = get_embedding_client("mock")
            logger.warning("Using mock embedder")

        # Initialize components
        self.writer = MemoryWriter(self.store, self.embedder)
        self.retriever = MemoryRetriever(self.store, self.embedder)
        self.consolidator = MemoryConsolidator(self.store, self.embedder)
        self.loop_detector = LoopDetector()

        # Consolidation tracking
        self._trajectory_count = 0
        self._consolidate_every = consolidate_every

    # === Agent Runtime API ===

    def query(self, current_state: State) -> MemoryContext:
        """
        Query memory for relevant context.

        Call this before each agent step to get:
        - Applicable methodologies
        - Similar historical fragments
        - Warnings about potential failure patterns
        """
        # Generate embedding for state if needed
        if self.embedder and not current_state.embedding:
            situation_str = current_state.to_situation_string()
            current_state.embedding = self.embedder.embed(situation_str)

        result = self.retriever.retrieve(current_state)

        return MemoryContext(
            methodologies=result.methodologies,
            similar_fragments=result.similar_fragments,
            warnings=result.warnings,
        )

    def check_loop(self, state_history: List[State]) -> Optional[LoopInfo]:
        """
        Check if agent is stuck in a loop.

        Detects loops based on error consistency:
        - Same action type
        - Same error category
        - Overlapping error keywords
        """
        return self.loop_detector.detect(state_history)

    # === Learning API ===

    def learn(self, trajectory: RawTrajectory) -> str:
        """
        Learn from a completed trajectory.

        1. Writes trajectory and fragments to memory
        2. Triggers consolidation every N trajectories

        Returns the trajectory ID.
        """
        traj_id = self.writer.write_trajectory(trajectory)

        self._trajectory_count += 1
        if self._trajectory_count % self._consolidate_every == 0:
            logger.info(f"Triggering consolidation (every {self._consolidate_every} trajectories)")
            self.consolidator.consolidate()

        return traj_id

    # === Statistics API ===

    def get_stats(self) -> MemoryStats:
        """Get memory statistics."""
        if not self.store:
            return MemoryStats()

        # Query for stats
        try:
            traj_count = self.store.execute_query(
                "MATCH (t:Trajectory) RETURN count(t) as count"
            )
            meth_count = self.store.execute_query(
                "MATCH (m:Methodology) RETURN count(m) as count"
            )

            return MemoryStats(
                total_trajectories=traj_count[0]["count"] if traj_count else 0,
                total_methodologies=meth_count[0]["count"] if meth_count else 0,
            )
        except Exception as e:
            logger.warning(f"Failed to get stats: {e}")
            return MemoryStats()

    # === Lifecycle ===

    def close(self) -> None:
        """Close connections."""
        if self.store:
            self.store.close()

    def __enter__(self) -> "AgentMemory":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
