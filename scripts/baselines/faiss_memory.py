#!/usr/bin/env python3
"""Standalone FAISS-based semantic memory module.

Replicates the SemanticMemory approach from SWE-Bench-CL (agents-never-forget):
- Store problem + solution text with success metadata
- Retrieve top-k by cosine similarity
- Supports both sentence-transformers (their approach) and text-embedding-3-large (ours)

Usage:
    from scripts.baselines.faiss_memory import FAISSMemory

    memory = FAISSMemory(embedding_provider="openai", api_key="...", base_url="...")
    memory.add_experience("instance_id_1", "problem text", "solution text", success=True)
    results = memory.retrieve_relevant("new problem query", top_k=3)
"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class MemoryEntry:
    """A single experience stored in memory."""

    instance_id: str
    sequence_id: str
    problem_text: str
    solution_text: str
    success: bool
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def content(self) -> str:
        """Format entry content for embedding and retrieval."""
        status = "[SUCCESSFUL]" if self.success else "[FAILED]"
        return (
            f"{status} Task {self.instance_id}:\n"
            f"Problem: {self.problem_text[:500]}\n"
            f"Solution: {self.solution_text[:500]}"
        )


class FAISSMemory:
    """FAISS-based semantic memory for SWE-Bench-CL experiments.

    Implements the same interface as the agents-never-forget SemanticMemory
    but as a standalone module decoupled from LangChain.

    Supports two embedding backends:
    - "sentence_transformers": Uses all-MiniLM-L6-v2 (384 dim, their approach)
    - "openai": Uses text-embedding-3-large via our LiteLLM proxy (3072 dim)
    """

    def __init__(
        self,
        embedding_provider: str = "sentence_transformers",
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        top_k: int = 3,
    ):
        """Initialize FAISS memory.

        Args:
            embedding_provider: "sentence_transformers" or "openai"
            model_name: Model name for the embedding provider.
                For sentence_transformers: "sentence-transformers/all-MiniLM-L6-v2"
                For openai: "text-embedding-3-large"
            api_key: API key (required for openai provider)
            base_url: Base URL for the API (e.g. "http://localhost:4000/v1")
            top_k: Default number of results to retrieve
        """
        self.embedding_provider = embedding_provider
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.top_k = top_k

        self._entries: List[MemoryEntry] = []
        self._embeddings: Optional[np.ndarray] = None  # shape: (n, dim)
        self._embedding_dim: Optional[int] = None
        self._encoder = None  # lazy init

    @property
    def size(self) -> int:
        """Number of entries in memory."""
        return len(self._entries)

    def _get_encoder(self):
        """Lazy-initialize the embedding encoder."""
        if self._encoder is not None:
            return self._encoder

        if self.embedding_provider == "sentence_transformers":
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise ImportError(
                    "sentence-transformers required. Install with: "
                    "uv add sentence-transformers"
                )
            self._encoder = SentenceTransformer(
                self.model_name.replace("sentence-transformers/", "")
            )
            self._embedding_dim = self._encoder.get_sentence_embedding_dimension()
        elif self.embedding_provider == "openai":
            from openai import OpenAI

            kwargs: Dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._encoder = OpenAI(**kwargs)
            # Determine dimension from model name
            dim_map = {
                "text-embedding-3-large": 3072,
                "text-embedding-3-small": 1536,
                "text-embedding-ada-002": 1536,
            }
            self._embedding_dim = dim_map.get(self.model_name, 3072)
        else:
            raise ValueError(
                f"Unknown embedding_provider: {self.embedding_provider}. "
                "Use 'sentence_transformers' or 'openai'."
            )
        return self._encoder

    def _embed_text(self, text: str) -> np.ndarray:
        """Embed a single text string."""
        encoder = self._get_encoder()

        if self.embedding_provider == "sentence_transformers":
            vec = encoder.encode(text, normalize_embeddings=True)
            return np.array(vec, dtype=np.float32)
        else:
            # OpenAI-compatible API
            response = encoder.embeddings.create(
                input=text,
                model=self.model_name,
            )
            vec = response.data[0].embedding
            return np.array(vec, dtype=np.float32)

    def _embed_batch(self, texts: List[str]) -> np.ndarray:
        """Embed multiple texts."""
        encoder = self._get_encoder()

        if self.embedding_provider == "sentence_transformers":
            vecs = encoder.encode(texts, normalize_embeddings=True)
            return np.array(vecs, dtype=np.float32)
        else:
            response = encoder.embeddings.create(
                input=texts,
                model=self.model_name,
            )
            vecs = [item.embedding for item in response.data]
            return np.array(vecs, dtype=np.float32)

    def add_experience(
        self,
        instance_id: str,
        problem_text: str,
        solution_text: str,
        success: bool,
        sequence_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a completed task experience to memory.

        Args:
            instance_id: Unique task identifier (e.g. "django__django-9296")
            problem_text: The problem statement
            solution_text: The solution (diff, explanation, or summary)
            success: Whether the task was solved successfully
            sequence_id: Sequence identifier for filtering
            metadata: Additional metadata (e.g. tokens_used, steps)
        """
        entry = MemoryEntry(
            instance_id=instance_id,
            sequence_id=sequence_id,
            problem_text=problem_text,
            solution_text=solution_text,
            success=success,
            metadata=metadata or {},
        )
        self._entries.append(entry)

        # Compute embedding and update matrix
        embedding = self._embed_text(entry.content)
        if self._embeddings is None:
            self._embeddings = embedding.reshape(1, -1)
        else:
            self._embeddings = np.vstack([self._embeddings, embedding.reshape(1, -1)])

        logger.debug(
            "Added experience: %s (success=%s, memory_size=%d)",
            instance_id,
            success,
            self.size,
        )

    def retrieve_relevant(
        self,
        query: str,
        top_k: Optional[int] = None,
        sequence_filter: Optional[str] = None,
        success_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Retrieve relevant past experiences by cosine similarity.

        Args:
            query: Query text (typically the new problem statement)
            top_k: Number of results to return (default: self.top_k)
            sequence_filter: Only return results from this sequence
            success_only: Only return successful experiences

        Returns:
            List of dicts with keys: instance_id, content, success, score, metadata
        """
        if not self._entries or self._embeddings is None:
            return []

        k = top_k or self.top_k
        query_vec = self._embed_text(query)

        # Cosine similarity (embeddings are normalized for sentence_transformers)
        if self.embedding_provider == "sentence_transformers":
            scores = self._embeddings @ query_vec
        else:
            # Normalize for cosine similarity
            norms = np.linalg.norm(self._embeddings, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            normalized = self._embeddings / norms
            q_norm = np.linalg.norm(query_vec)
            if q_norm > 0:
                query_vec = query_vec / q_norm
            scores = normalized @ query_vec

        # Sort by descending similarity
        ranked_indices = np.argsort(-scores)

        results = []
        for idx in ranked_indices:
            entry = self._entries[idx]

            # Apply filters
            if sequence_filter and entry.sequence_id != sequence_filter:
                continue
            if success_only and not entry.success:
                continue

            results.append({
                "instance_id": entry.instance_id,
                "content": entry.content,
                "problem_text": entry.problem_text,
                "solution_text": entry.solution_text,
                "success": entry.success,
                "score": float(scores[idx]),
                "sequence_id": entry.sequence_id,
                "metadata": entry.metadata,
            })

            if len(results) >= k:
                break

        return results

    def clear(self) -> None:
        """Clear all entries from memory."""
        self._entries.clear()
        self._embeddings = None
        logger.info("FAISS memory cleared (had %d entries)", self.size)

    def save(self, path: Path) -> None:
        """Save memory state to disk.

        Args:
            path: Directory to save memory files
        """
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)

        # Save entries as JSON
        entries_data = [asdict(e) for e in self._entries]
        with open(path / "entries.json", "w") as f:
            json.dump(entries_data, f, indent=2)

        # Save embeddings as numpy array
        if self._embeddings is not None:
            np.save(path / "embeddings.npy", self._embeddings)

        logger.info("Saved FAISS memory (%d entries) to %s", self.size, path)

    def load(self, path: Path) -> None:
        """Load memory state from disk.

        Args:
            path: Directory containing saved memory files
        """
        path = Path(path)
        entries_file = path / "entries.json"
        embeddings_file = path / "embeddings.npy"

        if not entries_file.exists():
            logger.warning("No saved memory found at %s", path)
            return

        with open(entries_file) as f:
            entries_data = json.load(f)

        self._entries = [
            MemoryEntry(
                instance_id=e["instance_id"],
                sequence_id=e["sequence_id"],
                problem_text=e["problem_text"],
                solution_text=e["solution_text"],
                success=e["success"],
                timestamp=e.get("timestamp", 0),
                metadata=e.get("metadata", {}),
            )
            for e in entries_data
        ]

        if embeddings_file.exists():
            self._embeddings = np.load(embeddings_file)
        else:
            # Re-embed if embeddings file missing
            logger.warning("Embeddings file missing, re-embedding %d entries", len(self._entries))
            texts = [e.content for e in self._entries]
            if texts:
                self._embeddings = self._embed_batch(texts)

        logger.info("Loaded FAISS memory (%d entries) from %s", self.size, path)

    def get_stats(self) -> Dict[str, Any]:
        """Get memory statistics."""
        if not self._entries:
            return {"size": 0, "success_count": 0, "failure_count": 0}

        success_count = sum(1 for e in self._entries if e.success)
        sequences = set(e.sequence_id for e in self._entries if e.sequence_id)

        return {
            "size": self.size,
            "success_count": success_count,
            "failure_count": self.size - success_count,
            "sequences": list(sequences),
            "embedding_dim": self._embedding_dim,
            "embedding_provider": self.embedding_provider,
        }
