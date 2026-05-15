"""Agent Memory - Long-term memory system for coding agents."""

from agent_memory.models import (
    Trajectory,
    Fragment,
    State,
    Methodology,
    ErrorPattern,
    TemporalEdge,
    Community,
    PlaybookEntry,
    PLAYBOOK_SECTIONS,
)
from agent_memory.neo4j_store import Neo4jStore
from agent_memory.embeddings import EmbeddingClient, get_embedding_client
from agent_memory.loop_detector import LoopDetector, LoopSignature, LoopInfo
from agent_memory.writer import MemoryWriter, RawTrajectory
from agent_memory.retriever import MemoryRetriever, RetrievalResult, ScoredResult, EnrichedFragment
from agent_memory.consolidator import MemoryConsolidator
from agent_memory.reranker import RerankerPipeline
from agent_memory.formatter import StructuredContextFormatter
from agent_memory.entity_resolver import EntityResolver
from agent_memory.community import CommunityDetector
from agent_memory.playbook import PlaybookRetriever, parse_playbook, format_playbook
from agent_memory.query_rewriter import QueryRewriter
from agent_memory.memory import AgentMemory, MemoryContext, MemoryStats
from agent_memory import evaluation

__version__ = "0.2.0"
__all__ = [
    # Models
    "Trajectory",
    "Fragment",
    "State",
    "Methodology",
    "ErrorPattern",
    "TemporalEdge",
    "Community",
    "PlaybookEntry",
    "PLAYBOOK_SECTIONS",
    # Store
    "Neo4jStore",
    # Embeddings
    "EmbeddingClient",
    "get_embedding_client",
    # Loop detection
    "LoopDetector",
    "LoopSignature",
    "LoopInfo",
    # Writer
    "MemoryWriter",
    "RawTrajectory",
    # Retriever
    "MemoryRetriever",
    "RetrievalResult",
    "ScoredResult",
    "EnrichedFragment",
    # Reranker
    "RerankerPipeline",
    # Formatter
    "StructuredContextFormatter",
    # Entity Resolution
    "EntityResolver",
    # Community Detection
    "CommunityDetector",
    # Consolidator
    "MemoryConsolidator",
    # Playbook
    "PlaybookRetriever",
    "parse_playbook",
    "format_playbook",
    # Query Rewriter
    "QueryRewriter",
    # Unified API
    "AgentMemory",
    "MemoryContext",
    "MemoryStats",
    # Evaluation module
    "evaluation",
]
