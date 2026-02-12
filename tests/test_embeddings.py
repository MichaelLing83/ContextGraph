"""Tests for embedding client."""

from agent_memory.embeddings import get_embedding_client


class TestEmbeddingClient:
    def test_client_interface(self):
        """Test EmbeddingClient has required methods."""
        client = get_embedding_client("mock")
        assert hasattr(client, "embed")
        assert hasattr(client, "embed_batch")

    def test_mock_client_embed(self):
        """Test mock client returns embedding."""
        client = get_embedding_client("mock")
        result = client.embed("test text")
        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(x, float) for x in result)

    def test_mock_client_embed_batch(self):
        """Test mock client batch embedding."""
        client = get_embedding_client("mock")
        texts = ["text 1", "text 2", "text 3"]
        results = client.embed_batch(texts)
        assert len(results) == 3
        assert all(isinstance(r, list) for r in results)

    def test_mock_client_deterministic(self):
        """Test mock client produces deterministic embeddings."""
        client = get_embedding_client("mock")
        text = "same text produces same embedding"
        result1 = client.embed(text)
        result2 = client.embed(text)
        assert result1 == result2

    def test_mock_client_different_texts(self):
        """Test mock client produces different embeddings for different texts."""
        client = get_embedding_client("mock")
        result1 = client.embed("text one")
        result2 = client.embed("text two")
        assert result1 != result2
