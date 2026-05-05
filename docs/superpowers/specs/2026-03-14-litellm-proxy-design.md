# LiteLLM Proxy Integration Design

## Goal

Replace direct API calls to ChatAnywhere with a self-hosted LiteLLM proxy that:
1. Uses Claude Max subscription (OAuth token) for LLM calls
2. Routes embeddings through ChatAnywhere (existing)
3. Is extensible for GPT Pro subscription later

## Architecture

```
SWE-agent ──┐
             ├──→ LiteLLM Proxy (localhost:4000)
agent_memory ┘        │
                      ├── claude-* → Anthropic API (OAuth token)
                      ├── text-embedding-* → ChatAnywhere
                      └── gpt-* → OpenAI API (reserved)
```

## Components

### 1. `configs/litellm_config.yaml`

Model routing configuration for the LiteLLM proxy:
- **Claude models**: Route to Anthropic provider, using OAuth token as `api_key`
- **Embedding models**: Route to ChatAnywhere (`text-embedding-3-large`)
- **OpenAI models**: Reserved section for future GPT Pro support
- `forward_client_headers_to_llm_api: true` for OAuth token forwarding

### 2. `docker-compose.yml`

Unifies Neo4j and LiteLLM proxy into one file:
- `neo4j` service: Same config as current `docker run` command (ports 7474/7687, named volume)
- `litellm` service: Official `ghcr.io/berriai/litellm` image, port 4000, mounts `configs/litellm_config.yaml`
- Shared `.env` file for secrets

### 3. SWE-agent Config Updates

Update `configs/swe_agent_control.yaml` and `configs/swe_agent_treatment.yaml`:
- `model.api_base` → `http://localhost:4000/v1`
- `model.name` → `claude-sonnet-4-20250514` (native Anthropic name, no `openai/` prefix)
- `model.api_key` → `${LITELLM_MASTER_KEY}`
- Update `litellm_model_registry.json` with correct model metadata

### 4. agent_memory API Updates

Files that create `OpenAI(base_url=...)` clients:
- `agent_memory/embeddings.py` — point to proxy for embeddings
- `agent_memory/strategy_extractor.py` — point to proxy for LLM
- `agent_memory/query_rewriter.py` — point to proxy for LLM
- `scripts/reembed_all_nodes.py` — point to proxy for embeddings
- `scripts/extract_strategies.py` — point to proxy for LLM

All use OpenAI SDK, which is compatible with LiteLLM's `/v1` endpoint.

### 5. `.env` Updates

New variables:
```
ANTHROPIC_API_KEY=sk-ant-oat01-...   # Claude Max OAuth token
LITELLM_MASTER_KEY=sk-litellm-...    # Proxy access key
```

Kept variables:
```
OPENAI_API_KEY=sk-2AYW...            # ChatAnywhere (for embeddings)
OPENAI_API_BASE=https://api.chatanywhere.org  # ChatAnywhere endpoint
```

### 6. tools/query_memory/ (Docker container)

- `bin/query_memory_impl.py`: Change default `OPENAI_API_BASE` to proxy URL
- SWE-agent config: Propagate `LITELLM_MASTER_KEY` as env variable
- Container needs `--add-host=host.docker.internal:host-gateway` (already required for treatment group)

## What Does NOT Change

- Neo4j schema, data, volumes
- Retrieval pipeline (PlaybookRetriever, PPR, MMR)
- Experiment scripts logic (only API base URLs change)
- Test data, split files, verified_200.json

## GPT Pro Extensibility

Adding GPT Pro later requires only:
1. Add `OPENAI_PRO_API_KEY` to `.env`
2. Add OpenAI model entries to `litellm_config.yaml`
3. Add model metadata to `litellm_model_registry.json`

## Risk: OAuth Token Restriction

Anthropic may reject OAuth tokens from non-Claude-Code clients (client fingerprinting). Mitigation:
- `forward_client_headers_to_llm_api: true` makes LiteLLM transparent
- If rejected, fall back to ChatAnywhere as before (config is preserved)
