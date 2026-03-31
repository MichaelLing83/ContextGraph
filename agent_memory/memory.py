"""AgentMemory - Unified API for agent long-term memory."""

from typing import Optional, List
from dataclasses import dataclass, field
import logging

from agent_memory.models import State, Methodology, Fragment, Strategy, ProblemSummary
from agent_memory.neo4j_store import Neo4jStore
from agent_memory.embeddings import get_embedding_client
from agent_memory.writer import MemoryWriter, RawTrajectory
from agent_memory.retriever import MemoryRetriever
from agent_memory.consolidator import MemoryConsolidator
from agent_memory.loop_detector import LoopDetector, LoopInfo
from agent_memory.entity_resolver import EntityResolver
from agent_memory.community import CommunityDetector
from agent_memory.formatter import StructuredContextFormatter
from agent_memory.playbook import PlaybookRetriever, format_playbook

logger = logging.getLogger(__name__)


@dataclass
class MemoryContext:
    """Context returned from memory query."""

    methodologies: List[Methodology] = field(default_factory=list)
    similar_fragments: List[Fragment] = field(default_factory=list)
    strategies: List[Strategy] = field(default_factory=list)
    problem_summaries: List[ProblemSummary] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def has_suggestions(self) -> bool:
        return bool(self.methodologies or self.strategies or self.problem_summaries)

    def to_structured(self) -> str:
        """Return structured XML representation of this context."""
        from agent_memory.retriever import RetrievalResult, EnrichedFragment
        # Convert similar_fragments to enriched_fragments for the formatter
        enriched = [
            EnrichedFragment(fragment=f, relevance_score=max(0.0, 0.8 - (i * 0.1)))
            for i, f in enumerate(self.similar_fragments)
        ]
        result = RetrievalResult(
            methodologies=self.methodologies,
            similar_fragments=self.similar_fragments,
            enriched_fragments=enriched,
            strategies=self.strategies,
            problem_summaries=self.problem_summaries,
            warnings=self.warnings,
        )
        formatter = StructuredContextFormatter()
        return formatter.format(result)


@dataclass
class MemoryStats:
    """Statistics from memory."""

    error_frequency: dict = field(default_factory=dict)
    failure_patterns: list = field(default_factory=list)
    total_trajectories: int = 0
    total_methodologies: int = 0
    total_communities: int = 0


class AgentMemory:
    """
    Agent Long-term Memory - Unified API.

    Zep-optimized architecture:
      - Triple-search retrieval (cosine + BM25 + BFS + community)
      - Reranking pipeline (RRF + MMR + node distance boost)
      - Entity resolution (semantic deduplication)
      - Community detection (label propagation)
      - Temporal validity (dual timeline edges)
      - Structured context output (XML-tagged)

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
        embedding_base_url: Optional[str] = None,
        embedding_model: str = "text-embedding-3-small",
        consolidate_every: int = 16,
        rewriter_api_base: Optional[str] = None,
        rewriter_api_key: Optional[str] = None,
        rewriter_model: str = "claude-sonnet-4-20250514",
        rewriter_enabled: bool = False,
    ):
        # Initialize embedder first (needed for schema dimensions)
        if embedding_api_key:
            kwargs = {"api_key": embedding_api_key, "model": embedding_model}
            if embedding_base_url:
                kwargs["base_url"] = embedding_base_url
            self.embedder = get_embedding_client("openai", **kwargs)
        else:
            self.embedder = get_embedding_client("mock")
            logger.warning("Using mock embedder")

        # Initialize store
        if neo4j_uri:
            self.store = Neo4jStore(uri=neo4j_uri, auth=neo4j_auth)
            self.store.init_schema(vector_dimensions=self.embedder.dimensions)
        else:
            self.store = None
            logger.warning("Running without Neo4j store (mock mode)")

        # Initialize entity resolver
        self.entity_resolver = EntityResolver(self.store, self.embedder)

        # Initialize community detector
        self.community_detector = CommunityDetector(self.store, self.embedder)

        # Initialize formatter
        self.formatter = StructuredContextFormatter()

        # Initialize query rewriter (optional)
        self.query_rewriter = None
        if rewriter_enabled and rewriter_api_key and rewriter_api_base:
            from agent_memory.query_rewriter import QueryRewriter
            self.query_rewriter = QueryRewriter(
                api_base=rewriter_api_base,
                api_key=rewriter_api_key,
                model=rewriter_model,
                enabled=True,
            )

        # Initialize components (with entity resolver)
        self.writer = MemoryWriter(
            self.store, self.embedder, entity_resolver=self.entity_resolver
        )
        self.retriever = MemoryRetriever(
            self.store, self.embedder, query_rewriter=self.query_rewriter
        )
        self.consolidator = MemoryConsolidator(
            self.store, self.embedder, entity_resolver=self.entity_resolver
        )
        self.loop_detector = LoopDetector()

        # Playbook retriever
        self.playbook_retriever = PlaybookRetriever(
            self.store, self.embedder, query_rewriter=self.query_rewriter
        )

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
        - Similar problem summaries
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
            strategies=result.strategies,
            problem_summaries=result.problem_summaries,
            warnings=result.warnings,
        )

    def query_structured(self, current_state: State) -> str:
        """Query memory and return structured XML context.

        Convenience method that returns token-efficient XML output
        suitable for direct injection into agent prompts.
        Includes playbook entries before the XML section.
        """
        if self.embedder and not current_state.embedding:
            situation_str = current_state.to_situation_string()
            current_state.embedding = self.embedder.embed(situation_str)

        # Get playbook context
        playbook_text = self.query_playbook(current_state)

        # Get retriever context
        result = self.retriever.retrieve(current_state)
        xml_text = self.formatter.format(result)

        if playbook_text:
            return playbook_text + "\n\n" + xml_text
        return xml_text

    def query_playbook(self, current_state: State, top_k: int = 5) -> str:
        """Query playbook entries relevant to the current state.

        Returns playbook-format text wrapped in <memory_playbook> tags,
        or empty string if no entries found.

        Extracts error_type from current_error to enable PPR graph traversal
        (HippoRAG-style multi-hop retrieval from ErrorPattern → CanonicalRule).

        If the formatted output exceeds 2000 characters, re-formats with
        only the top 3 entries to reduce noise in the agent's context window.
        """
        # Build query text from state
        parts = []
        if current_state.current_error:
            parts.append(current_state.current_error)
        if current_state.task_description:
            parts.append(current_state.task_description)
        if not parts:
            return ""

        query_text = " ".join(parts)

        # Extract error type for PPR seed nodes
        error_type = None
        if current_state.current_error:
            error_type = current_state._extract_error_type(
                current_state.current_error
            )
            if error_type == "Unknown":
                error_type = None

        # When rewriter is active, pass None for embedding so
        # PlaybookRetriever re-embeds from the rewritten text
        query_embedding = None if self.query_rewriter else current_state.embedding

        entries = self.playbook_retriever.retrieve(
            query_text,
            query_embedding=query_embedding,
            top_k=top_k,
            error_type=error_type,
        )

        if not entries:
            return ""

        result = format_playbook(entries, wrap=True)

        # If output is too long, trim to top 3 entries to reduce noise
        if len(result) > 2000 and len(entries) > 3:
            result = format_playbook(entries[:3], wrap=True)

        return result

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

        1. Writes trajectory and fragments to memory (with entity resolution)
        2. Assigns new fragments to communities
        3. Triggers consolidation every N trajectories

        Returns the trajectory ID.
        """
        traj_id = self.writer.write_trajectory(trajectory)

        # Assign new fragments to communities incrementally
        if self.store and self.community_detector:
            try:
                query = """
                MATCH (t:Trajectory {id: $traj_id})-[:HAS_FRAGMENT]->(f:Fragment)
                RETURN f.id AS fid
                """
                results = self.store.execute_query(query, {"traj_id": traj_id})
                for r in results:
                    fid = r.get("fid")
                    if fid:
                        self.community_detector.assign_new_node(fid)
            except Exception as e:
                logger.debug("Community assignment note: %s", e)

        self._trajectory_count += 1
        if self._trajectory_count % self._consolidate_every == 0:
            logger.info(f"Triggering consolidation (every {self._consolidate_every} trajectories)")
            self.consolidator.consolidate()
            # Refresh communities periodically
            if self._trajectory_count % (self._consolidate_every * 4) == 0:
                self.community_detector.refresh_communities()

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
            comm_count = self.store.execute_query(
                "MATCH (c:Community) RETURN count(c) as count"
            )

            return MemoryStats(
                total_trajectories=traj_count[0]["count"] if traj_count else 0,
                total_methodologies=meth_count[0]["count"] if meth_count else 0,
                total_communities=comm_count[0]["count"] if comm_count else 0,
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
