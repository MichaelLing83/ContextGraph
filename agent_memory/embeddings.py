"""Embedding client for semantic matching."""

from abc import ABC, abstractmethod
from typing import List, Optional
import hashlib
import logging

logger = logging.getLogger(__name__)


class EmbeddingClient(ABC):
    """Abstract base class for embedding clients."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Return the dimensionality of embeddings produced by this client."""
        pass

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        pass

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts. Override for efficiency."""
        return [self.embed(text) for text in texts]


class MockEmbeddingClient(EmbeddingClient):
    """Mock embedding client for testing."""

    def __init__(self, dimension: int = 256):
        self.dimension = dimension

    @property
    def dimensions(self) -> int:
        return self.dimension

    def embed(self, text: str) -> List[float]:
        """Generate deterministic mock embedding based on text hash."""
        # Use hash to generate deterministic but varied embeddings
        hash_bytes = hashlib.sha256(text.encode()).digest()
        # Convert bytes to floats in range [-1, 1]
        embedding = []
        for i in range(self.dimension):
            # Use bytes cyclically to create float
            val = (hash_bytes[i % len(hash_bytes)] - 128) / 128.0
            embedding.append(val)

        return embedding


class OpenAIEmbeddingClient(EmbeddingClient):
    """OpenAI embedding client."""

    # Known model dimensions
    _MODEL_DIMENSIONS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(self, api_key: str, model: str = "text-embedding-3-small", base_url: Optional[str] = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self._client = None

    @property
    def dimensions(self) -> int:
        return self._MODEL_DIMENSIONS.get(self.model, 1536)

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def embed(self, text: str) -> List[float]:
        response = self.client.embeddings.create(
            input=text,
            model=self.model,
        )
        return response.data[0].embedding

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        response = self.client.embeddings.create(
            input=texts,
            model=self.model,
        )
        return [item.embedding for item in response.data]


def get_embedding_client(
    client_type: str = "openai",
    api_key: Optional[str] = None,
    **kwargs,
) -> EmbeddingClient:
    """Factory function to get embedding client."""
    if client_type == "mock":
        return MockEmbeddingClient(**kwargs)
    elif client_type == "openai":
        if api_key is None:
            import os
            api_key = os.environ.get("OPENAI_API_KEY")
        if api_key is None:
            raise ValueError("OpenAI API key required")
        return OpenAIEmbeddingClient(api_key=api_key, **kwargs)
    else:
        raise ValueError(f"Unknown client type: {client_type}")
