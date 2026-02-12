"""Agent Memory - Long-term memory system for coding agents."""

from agent_memory.models import (
    Trajectory,
    Fragment,
    State,
    Methodology,
    ErrorPattern,
)
from agent_memory.neo4j_store import Neo4jStore
from agent_memory.embeddings import EmbeddingClient, get_embedding_client
from agent_memory.loop_detector import LoopDetector, LoopSignature, LoopInfo
from agent_memory.writer import MemoryWriter, RawTrajectory
from agent_memory.retriever import MemoryRetriever, RetrievalResult
from agent_memory.consolidator import MemoryConsolidator
from agent_memory.memory import AgentMemory, MemoryContext, MemoryStats
from agent_memory import evaluation

__version__ = "0.1.0"
__all__ = [
    # Models
    "Trajectory",
    "Fragment",
    "State",
    "Methodology",
    "ErrorPattern",
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
    # Consolidator
    "MemoryConsolidator",
    # Unified API
    "AgentMemory",
    "MemoryContext",
    "MemoryStats",
    # Evaluation module
    "evaluation",
]
