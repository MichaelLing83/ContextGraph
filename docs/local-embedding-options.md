# Local Embedding Model Options

Current setup: `text-embedding-3-large` (3072 dim) via ChatAnywhere proxy. This is OpenAI closed-source, cannot be self-hosted.

## Top Open-Source Alternatives (128GB unified memory Mac)

| Model | MTEB Score | Dim | Memory | Notes |
|-------|-----------|-----|--------|-------|
| `Alibaba-NLP/gte-Qwen2-7B-instruct` | 72.05 | 3584 | ~15GB | Strongest open-source, dim close to 3072 |
| `nvidia/NV-Embed-v2` | 72.31 | 4096 | ~16GB | MTEB leaderboard top |
| `BAAI/bge-en-icl` | 71.67 | 4096 | ~15GB | Strong ICL capability |
| `Snowflake/snowflake-arctic-embed-l-v2.0` | 71.01 | 1024 | ~1.3GB | Small and efficient |
| `BAAI/bge-large-en-v1.5` | 64.23 | 1024 | ~1.3GB | Proven, widely used |
| `nomic-ai/nomic-embed-text-v1.5` | 62.28 | 768 | ~550MB | Good quality-to-size ratio |
| `all-MiniLM-L6-v2` | 56.26 | 384 | ~80MB | Fastest, minimal resources |

## Deployment Methods

### Option 1: sentence-transformers (simplest)

```bash
uv add sentence-transformers
uv run python -c "
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('Alibaba-NLP/gte-Qwen2-7B-instruct')
emb = model.encode('test query')
print(f'dim={len(emb)}')
"
```

### Option 2: vllm as OpenAI-compatible server (drop-in replacement)

```bash
vllm serve Alibaba-NLP/gte-Qwen2-7B-instruct \
  --task embedding --port 8100 --dtype float16
# Then set: OPENAI_API_BASE=http://localhost:8100/v1
```

## Migration Impact

- All 45,115 nodes in Neo4j must be re-embedded (dimensions and vector space differ)
- Use `scripts/reembed_all_nodes.py` for bulk re-embedding
- gte-Qwen2-7B on M2: ~50-100 items/sec → ~8-15 min for full reembed
- Neo4j vector indexes must be recreated with new dimensions
