"""Agent-KB style hybrid retrieval baseline for ContextGraph comparison.

Implements Agent-KB's approach: TF-IDF + semantic similarity with linear combination.
Score = 0.5 * tfidf_score + 0.5 * semantic_score

This baseline uses the SAME knowledge content as ContextGraph (exported from Neo4j)
but with SIMPLER retrieval — no graph structure, no PPR, no RRF fusion, no MMR.

Usage:
    # Build the index from exported knowledge base
    uv run python scripts/baselines/agentkb_baseline.py build-index \
        --kb-path data/baselines/agentkb_knowledge_base.json \
        --index-dir data/baselines/agentkb_index

    # Query the index
    uv run python scripts/baselines/agentkb_baseline.py query \
        --index-dir data/baselines/agentkb_index \
        --query "How to fix ImportError when module not found" \
        --top-k 10

    # Export index directly from Neo4j (combines export + build)
    uv run python scripts/baselines/agentkb_baseline.py build-from-neo4j \
        --index-dir data/baselines/agentkb_index
"""

from __future__ import annotations

import json
import os
import pickle
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import typer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Ensure scripts.baselines is importable when running directly
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

app = typer.Typer(help="Agent-KB style hybrid retrieval baseline.")


# ---------------------------------------------------------------------------
# Core retrieval class
# ---------------------------------------------------------------------------


class AgentKBRetriever:
    """Agent-KB style hybrid retrieval: TF-IDF + semantic similarity.

    Mirrors Agent-KB's approach:
        score = text_weight * tfidf_cosine + semantic_weight * embedding_cosine

    Default weights: 0.5 / 0.5 (per Agent-KB paper).
    """

    def __init__(
        self,
        text_weight: float = 0.5,
        semantic_weight: float = 0.5,
        embedding_api_base: Optional[str] = None,
        embedding_api_key: Optional[str] = None,
        embedding_model: str = "text-embedding-3-large",
        use_local_embeddings: bool = False,
    ):
        self.text_weight = text_weight
        self.semantic_weight = semantic_weight
        self.embedding_api_base = embedding_api_base or os.environ.get(
            "OPENAI_API_BASE", "http://localhost:4000/v1"
        )
        self.embedding_api_key = embedding_api_key or os.environ.get(
            "OPENAI_API_KEY", os.environ.get("LITELLM_MASTER_KEY", "")
        )
        self.embedding_model = embedding_model
        self.use_local_embeddings = use_local_embeddings

        # Index state
        self.records: List[dict] = []
        self.texts: List[str] = []
        self.tfidf_vectorizer: Optional[TfidfVectorizer] = None
        self.tfidf_matrix = None
        self.embeddings: Optional[np.ndarray] = None

        # Local model (loaded lazily if use_local_embeddings=True)
        self._local_model = None

    @property
    def local_model(self):
        """Lazy-load sentence-transformers model."""
        if self._local_model is None:
            from sentence_transformers import SentenceTransformer
            self._local_model = SentenceTransformer(
                "sentence-transformers/all-MiniLM-L6-v2"
            )
        return self._local_model

    def load_knowledge_base(self, kb_path: str) -> int:
        """Load knowledge base from exported JSON file.

        Args:
            kb_path: Path to the JSON file exported by agentkb_export.py.

        Returns:
            Number of records loaded.
        """
        with open(kb_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.records = data.get("records", [])
        self.texts = [r["text"] for r in self.records]
        return len(self.records)

    def build_tfidf_index(self) -> None:
        """Build TF-IDF index over all record texts."""
        if not self.texts:
            raise ValueError("No texts loaded. Call load_knowledge_base first.")

        self.tfidf_vectorizer = TfidfVectorizer(
            stop_words="english",
            max_features=50000,
            ngram_range=(1, 2),
            sublinear_tf=True,
        )
        self.tfidf_matrix = self.tfidf_vectorizer.fit_transform(self.texts)

    def build_embeddings(self, batch_size: int = 64) -> None:
        """Build semantic embeddings for all records.

        Uses either the LiteLLM proxy (OpenAI-compatible) or local
        sentence-transformers model depending on configuration.
        """
        if not self.texts:
            raise ValueError("No texts loaded. Call load_knowledge_base first.")

        if self.use_local_embeddings:
            self._build_local_embeddings(batch_size)
        else:
            self._build_api_embeddings(batch_size)

    def _build_local_embeddings(self, batch_size: int = 64) -> None:
        """Build embeddings using local sentence-transformers model."""
        all_embeddings = []
        for i in range(0, len(self.texts), batch_size):
            batch = self.texts[i : i + batch_size]
            embs = self.local_model.encode(
                batch, batch_size=batch_size, convert_to_numpy=True
            )
            all_embeddings.append(embs)
            if (i // batch_size) % 10 == 0:
                typer.echo(f"  Embedded {i + len(batch)}/{len(self.texts)} records")

        self.embeddings = np.vstack(all_embeddings)

    def _build_api_embeddings(self, batch_size: int = 64) -> None:
        """Build embeddings using OpenAI-compatible API (LiteLLM proxy)."""
        from openai import OpenAI

        client = OpenAI(
            api_key=self.embedding_api_key, base_url=self.embedding_api_base
        )

        all_embeddings = []
        for i in range(0, len(self.texts), batch_size):
            batch = self.texts[i : i + batch_size]
            # Filter empty strings (API rejects them)
            batch_clean = [t if t.strip() else "empty" for t in batch]

            response = client.embeddings.create(
                input=batch_clean, model=self.embedding_model
            )
            embs = [item.embedding for item in response.data]
            all_embeddings.extend(embs)

            if (i // batch_size) % 10 == 0:
                typer.echo(f"  Embedded {i + len(batch)}/{len(self.texts)} records")

        self.embeddings = np.array(all_embeddings, dtype=np.float32)

    def _embed_query(self, query: str) -> np.ndarray:
        """Embed a single query string."""
        if self.use_local_embeddings:
            return self.local_model.encode(query, convert_to_numpy=True)
        else:
            from openai import OpenAI

            client = OpenAI(
                api_key=self.embedding_api_key, base_url=self.embedding_api_base
            )
            response = client.embeddings.create(
                input=query, model=self.embedding_model
            )
            return np.array(response.data[0].embedding, dtype=np.float32)

    def query(
        self,
        query_text: str,
        top_k: int = 10,
        text_weight: Optional[float] = None,
        semantic_weight: Optional[float] = None,
    ) -> List[dict]:
        """Hybrid retrieval: TF-IDF + semantic similarity.

        Score = text_weight * tfidf_cosine + semantic_weight * embedding_cosine

        Args:
            query_text: The query string.
            top_k: Number of results to return.
            text_weight: Override default text weight.
            semantic_weight: Override default semantic weight.

        Returns:
            List of dicts with keys: id, text, category, score,
            tfidf_score, semantic_score, source_type, error_types.
        """
        tw = text_weight if text_weight is not None else self.text_weight
        sw = semantic_weight if semantic_weight is not None else self.semantic_weight

        # TF-IDF scoring
        tfidf_scores = np.zeros(len(self.texts))
        if self.tfidf_matrix is not None and self.tfidf_vectorizer is not None:
            query_vec = self.tfidf_vectorizer.transform([query_text])
            tfidf_scores = cosine_similarity(query_vec, self.tfidf_matrix).flatten()

        # Semantic scoring
        semantic_scores = np.zeros(len(self.texts))
        if self.embeddings is not None:
            query_emb = self._embed_query(query_text)
            semantic_scores = cosine_similarity(
                query_emb.reshape(1, -1), self.embeddings
            ).flatten()

        # Combined score (Agent-KB formula)
        combined_scores = tw * tfidf_scores + sw * semantic_scores

        # Get top-k indices
        top_indices = combined_scores.argsort()[-top_k:][::-1]

        results = []
        for idx in top_indices:
            if combined_scores[idx] <= 0:
                continue
            record = self.records[idx]
            results.append({
                "id": record["id"],
                "text": record["text"],
                "category": record.get("category", ""),
                "source_type": record.get("source_type", ""),
                "error_types": record.get("error_types", []),
                "score": float(combined_scores[idx]),
                "tfidf_score": float(tfidf_scores[idx]),
                "semantic_score": float(semantic_scores[idx]),
            })

        return results

    def add_record(self, record: dict) -> None:
        """Add a new record to the knowledge base (online learning).

        Rebuilds TF-IDF index and appends embedding for the new record.
        """
        self.records.append(record)
        self.texts.append(record["text"])

        # Rebuild TF-IDF (necessary since vocabulary may change)
        self.build_tfidf_index()

        # Append embedding for the new record
        if self.embeddings is not None:
            new_emb = self._embed_query(record["text"])
            self.embeddings = np.vstack([self.embeddings, new_emb.reshape(1, -1)])

    def save_index(self, index_dir: str) -> None:
        """Save the built index to disk."""
        path = Path(index_dir)
        path.mkdir(parents=True, exist_ok=True)

        # Save records (JSON)
        with open(path / "records.json", "w", encoding="utf-8") as f:
            json.dump(self.records, f, ensure_ascii=False)

        # Save TF-IDF vectorizer and matrix
        if self.tfidf_vectorizer is not None:
            with open(path / "tfidf_vectorizer.pkl", "wb") as f:
                pickle.dump(self.tfidf_vectorizer, f)
            # Save sparse matrix
            from scipy import sparse
            sparse.save_npz(path / "tfidf_matrix.npz", self.tfidf_matrix)

        # Save embeddings
        if self.embeddings is not None:
            np.save(path / "embeddings.npy", self.embeddings)

        # Save metadata
        meta = {
            "num_records": len(self.records),
            "text_weight": self.text_weight,
            "semantic_weight": self.semantic_weight,
            "embedding_model": self.embedding_model,
            "use_local_embeddings": self.use_local_embeddings,
            "embedding_dim": int(self.embeddings.shape[1]) if self.embeddings is not None else 0,
        }
        with open(path / "metadata.json", "w") as f:
            json.dump(meta, f, indent=2)

        typer.echo(f"Index saved to {path}")

    def load_index(self, index_dir: str) -> None:
        """Load a previously saved index from disk."""
        path = Path(index_dir)

        # Load records
        with open(path / "records.json", "r", encoding="utf-8") as f:
            self.records = json.load(f)
        self.texts = [r["text"] for r in self.records]

        # Load TF-IDF
        tfidf_path = path / "tfidf_vectorizer.pkl"
        if tfidf_path.exists():
            with open(tfidf_path, "rb") as f:
                self.tfidf_vectorizer = pickle.load(f)
            from scipy import sparse
            self.tfidf_matrix = sparse.load_npz(path / "tfidf_matrix.npz")

        # Load embeddings
        emb_path = path / "embeddings.npy"
        if emb_path.exists():
            self.embeddings = np.load(emb_path)

        # Load metadata
        meta_path = path / "metadata.json"
        if meta_path.exists():
            with open(meta_path, "r") as f:
                meta = json.load(f)
            self.text_weight = meta.get("text_weight", 0.5)
            self.semantic_weight = meta.get("semantic_weight", 0.5)
            self.embedding_model = meta.get("embedding_model", "text-embedding-3-large")
            self.use_local_embeddings = meta.get("use_local_embeddings", False)

        typer.echo(
            f"Loaded index: {len(self.records)} records, "
            f"TF-IDF={'yes' if self.tfidf_matrix is not None else 'no'}, "
            f"Embeddings={'yes' if self.embeddings is not None else 'no'}"
        )


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@app.command()
def build_index(
    kb_path: str = typer.Option(
        "data/baselines/agentkb_knowledge_base.json",
        help="Path to exported knowledge base JSON",
    ),
    index_dir: str = typer.Option(
        "data/baselines/agentkb_index",
        help="Directory to save the built index",
    ),
    use_local: bool = typer.Option(
        False,
        "--local",
        help="Use local sentence-transformers (all-MiniLM-L6-v2) instead of API",
    ),
    embedding_model: str = typer.Option(
        "text-embedding-3-large",
        help="Embedding model name (for API mode)",
    ),
    batch_size: int = typer.Option(64, help="Batch size for embedding generation"),
    text_weight: float = typer.Option(0.5, help="Weight for TF-IDF score"),
    semantic_weight: float = typer.Option(0.5, help="Weight for semantic score"),
):
    """Build Agent-KB retrieval index from exported knowledge base."""
    retriever = AgentKBRetriever(
        text_weight=text_weight,
        semantic_weight=semantic_weight,
        embedding_model=embedding_model,
        use_local_embeddings=use_local,
    )

    typer.echo(f"Loading knowledge base from {kb_path}...")
    n = retriever.load_knowledge_base(kb_path)
    typer.echo(f"Loaded {n} records")

    typer.echo("Building TF-IDF index...")
    t0 = time.time()
    retriever.build_tfidf_index()
    typer.echo(f"  TF-IDF built in {time.time() - t0:.1f}s")

    typer.echo(f"Building embeddings ({'local' if use_local else 'API: ' + embedding_model})...")
    t0 = time.time()
    retriever.build_embeddings(batch_size=batch_size)
    typer.echo(f"  Embeddings built in {time.time() - t0:.1f}s")

    retriever.save_index(index_dir)
    typer.echo("Done.")


@app.command()
def build_from_neo4j(
    index_dir: str = typer.Option(
        "data/baselines/agentkb_index",
        help="Directory to save the built index",
    ),
    uri: str = typer.Option(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        help="Neo4j bolt URI",
    ),
    user: str = typer.Option("neo4j", help="Neo4j username"),
    password: str = typer.Option("contextgraph123", help="Neo4j password"),
    use_local: bool = typer.Option(
        False, "--local", help="Use local sentence-transformers"
    ),
    embedding_model: str = typer.Option("text-embedding-3-large", help="Embedding model"),
    batch_size: int = typer.Option(64, help="Batch size for embeddings"),
):
    """Export from Neo4j and build index in one step."""
    import subprocess
    import sys
    import tempfile

    # Export to temp file
    tmp_path = Path(tempfile.mkdtemp()) / "kb_export.json"
    typer.echo(f"Exporting from Neo4j ({uri})...")

    # Use the export module directly
    from scripts.baselines.agentkb_export import _connect, _export_playbook_entries, _export_canonical_rules

    driver = _connect(uri, user, password)
    all_records = []
    with driver.session() as session:
        pb = _export_playbook_entries(session)
        typer.echo(f"  PlaybookEntry: {len(pb)}")
        all_records.extend(pb)
        cr = _export_canonical_rules(session)
        typer.echo(f"  CanonicalRule: {len(cr)}")
        all_records.extend(cr)
    driver.close()

    payload = {
        "source_uri": uri,
        "export_type": "agentkb_baseline",
        "counts": {"total": len(all_records)},
        "records": all_records,
    }
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False))

    # Build index
    retriever = AgentKBRetriever(
        embedding_model=embedding_model,
        use_local_embeddings=use_local,
    )
    retriever.load_knowledge_base(str(tmp_path))
    typer.echo(f"Loaded {len(retriever.records)} records")

    typer.echo("Building TF-IDF index...")
    retriever.build_tfidf_index()

    typer.echo(f"Building embeddings ({'local' if use_local else 'API'})...")
    retriever.build_embeddings(batch_size=batch_size)

    retriever.save_index(index_dir)
    typer.echo("Done.")


@app.command()
def query(
    index_dir: str = typer.Option(
        "data/baselines/agentkb_index",
        help="Directory containing the built index",
    ),
    query_text: str = typer.Option(..., "--query", "-q", help="Query text"),
    top_k: int = typer.Option(10, help="Number of results to return"),
    text_weight: float = typer.Option(0.5, help="Weight for TF-IDF score"),
    semantic_weight: float = typer.Option(0.5, help="Weight for semantic score"),
):
    """Query the Agent-KB index with hybrid retrieval."""
    retriever = AgentKBRetriever()
    retriever.load_index(index_dir)

    typer.echo(f"\nQuery: {query_text}")
    typer.echo(f"Weights: text={text_weight}, semantic={semantic_weight}")
    typer.echo("-" * 60)

    t0 = time.time()
    results = retriever.query(
        query_text, top_k=top_k, text_weight=text_weight, semantic_weight=semantic_weight
    )
    elapsed = time.time() - t0

    for i, r in enumerate(results, 1):
        typer.echo(
            f"\n[{i}] {r['id']} (score={r['score']:.4f}, "
            f"tfidf={r['tfidf_score']:.4f}, semantic={r['semantic_score']:.4f})"
        )
        typer.echo(f"    Category: {r['category']}")
        typer.echo(f"    Type: {r['source_type']}")
        if r["error_types"]:
            typer.echo(f"    Errors: {', '.join(r['error_types'])}")
        # Truncate long text
        text = r["text"]
        if len(text) > 200:
            text = text[:200] + "..."
        typer.echo(f"    Text: {text}")

    typer.echo(f"\n({len(results)} results in {elapsed:.3f}s)")


if __name__ == "__main__":
    app()
