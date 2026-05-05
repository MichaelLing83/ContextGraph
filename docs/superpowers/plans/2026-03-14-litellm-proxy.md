# LiteLLM Proxy Integration — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a self-hosted LiteLLM proxy that routes Claude requests through Anthropic (OAuth token) and embeddings through ChatAnywhere, replacing direct API calls.

**Architecture:** A Docker-compose stack with LiteLLM proxy + Neo4j. SWE-agent and agent_memory both call `http://localhost:4000` instead of ChatAnywhere directly. The proxy routes Claude models to Anthropic API and embedding models to ChatAnywhere.

**Tech Stack:** LiteLLM proxy (Docker), docker-compose, existing OpenAI SDK calls (no library changes)

**Spec:** `docs/superpowers/specs/2026-03-14-litellm-proxy-design.md`

---

## Chunk 1: LiteLLM Proxy Config + Docker Compose

### Task 1: Create LiteLLM proxy config

**Files:**
- Create: `configs/litellm_config.yaml`

- [ ] **Step 1: Create the LiteLLM proxy config**

```yaml
# configs/litellm_config.yaml
# LiteLLM proxy configuration — routes models to appropriate providers.
#
# Claude models → Anthropic API (uses ANTHROPIC_API_KEY, which is the OAuth token)
# Embedding models → ChatAnywhere (uses CHATANYWHERE_API_KEY)
# GPT models → OpenAI API (reserved, uses OPENAI_API_KEY)

model_list:
  # --- Claude models (Anthropic) ---
  - model_name: claude-sonnet-4-20250514
    litellm_params:
      model: anthropic/claude-sonnet-4-20250514
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: claude-sonnet-4-5-20250514
    litellm_params:
      model: anthropic/claude-sonnet-4-5-20250514
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: claude-haiku-4-5-20251001
    litellm_params:
      model: anthropic/claude-haiku-4-5-20251001
      api_key: os.environ/ANTHROPIC_API_KEY

  # --- Embedding models (ChatAnywhere) ---
  - model_name: text-embedding-3-large
    litellm_params:
      model: openai/text-embedding-3-large
      api_key: os.environ/CHATANYWHERE_API_KEY
      api_base: https://api.chatanywhere.org/v1

  - model_name: text-embedding-3-small
    litellm_params:
      model: openai/text-embedding-3-small
      api_key: os.environ/CHATANYWHERE_API_KEY
      api_base: https://api.chatanywhere.org/v1

  # --- GLM models (Zhipu AI) ---
  - model_name: GLM-4.7
    litellm_params:
      model: openai/GLM-4.7
      api_key: os.environ/ZHIPU_API_KEY
      api_base: https://open.bigmodel.cn/api/coding/paas/v4

  # --- GPT models (OpenAI — reserved for GPT Pro) ---
  # Uncomment when GPT Pro subscription is available:
  # - model_name: gpt-4o
  #   litellm_params:
  #     model: openai/gpt-4o
  #     api_key: os.environ/OPENAI_API_KEY

general_settings:
  forward_client_headers_to_llm_api: true
  master_key: os.environ/LITELLM_MASTER_KEY

litellm_settings:
  drop_params: true
  num_retries: 3
  request_timeout: 300
```

- [ ] **Step 2: Verify YAML syntax**

Run: `python -c "import yaml; yaml.safe_load(open('configs/litellm_config.yaml'))"`
Expected: No error

- [ ] **Step 3: Commit**

```bash
git add configs/litellm_config.yaml
git commit -m "feat: add LiteLLM proxy config with multi-provider routing"
```

---

### Task 2: Create docker-compose.yml

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Create docker-compose.yml**

```yaml
# docker-compose.yml
# Runs Neo4j (graph DB) + LiteLLM proxy (API gateway).
#
# Usage:
#   docker compose up -d            # Start both services
#   docker compose up -d neo4j      # Start Neo4j only
#   docker compose up -d litellm    # Start LiteLLM only
#   docker compose logs -f litellm  # Follow proxy logs

services:
  neo4j:
    image: neo4j:5
    container_name: neo4j-contextgraph
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      NEO4J_AUTH: neo4j/contextgraph123
    volumes:
      - neo4j-contextgraph-data:/data
    restart: unless-stopped

  litellm:
    image: ghcr.io/berriai/litellm:main-latest
    container_name: litellm-proxy
    ports:
      - "4000:4000"
    volumes:
      - ./configs/litellm_config.yaml:/app/config.yaml
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    env_file:
      - .env
    restart: unless-stopped
    depends_on:
      - neo4j

volumes:
  neo4j-contextgraph-data:
    external: true
```

- [ ] **Step 2: Verify compose file**

Run: `docker compose config --quiet`
Expected: No error (validates syntax and variable interpolation)

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add docker-compose for Neo4j + LiteLLM proxy"
```

---

### Task 3: Update `.env.example` with new variables

**Files:**
- Create: `.env.example`

- [ ] **Step 1: Create `.env.example`**

```bash
# === LiteLLM Proxy ===
LITELLM_MASTER_KEY=sk-litellm-your-master-key-here

# === Anthropic (Claude Max subscription — OAuth token) ===
ANTHROPIC_API_KEY=sk-ant-oat01-your-oauth-token-here

# === ChatAnywhere (embeddings) ===
CHATANYWHERE_API_KEY=sk-your-chatanywhere-key-here

# === Zhipu AI (GLM-4.7) ===
# ZHIPU_API_KEY=your-zhipu-key-here

# === OpenAI (GPT Pro — reserved) ===
# OPENAI_API_KEY=sk-your-openai-key-here

# === Neo4j ===
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=contextgraph123
```

- [ ] **Step 2: Update real `.env`**

Add the new variables to the existing `.env` file. Rename the current `OPENAI_API_KEY` to `CHATANYWHERE_API_KEY`. Add `ANTHROPIC_API_KEY` with the OAuth token. Add `LITELLM_MASTER_KEY` with a generated key.

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "feat: add .env.example with LiteLLM proxy variables"
```

---

## Chunk 2: Update SWE-agent Configs

### Task 4: Update litellm_model_registry.json

**Files:**
- Modify: `configs/litellm_model_registry.json`

- [ ] **Step 1: Update model registry with proxy-compatible names**

The model names must match what SWE-agent sends to LiteLLM (its internal LiteLLM, not the proxy). Since our proxy speaks OpenAI-compatible protocol, SWE-agent's internal LiteLLM uses the `openai/` prefix. The model name after the prefix must match a `model_name` in our proxy config.

```json
{
  "openai/claude-sonnet-4-20250514": {
    "max_tokens": 8192,
    "max_input_tokens": 200000,
    "max_output_tokens": 64000,
    "input_cost_per_token": 0.000003,
    "output_cost_per_token": 0.000015,
    "litellm_provider": "openai",
    "mode": "chat",
    "supports_function_calling": true,
    "supports_vision": true
  },
  "openai/GLM-4.7": {
    "max_tokens": 8192,
    "max_input_tokens": 128000,
    "max_output_tokens": 16384,
    "input_cost_per_token": 0.0,
    "output_cost_per_token": 0.0,
    "litellm_provider": "openai",
    "mode": "chat",
    "supports_function_calling": true,
    "supports_vision": true
  }
}
```

Note: The registry stays the same because SWE-agent's internal LiteLLM sees the proxy as an OpenAI-compatible endpoint. The `openai/claude-sonnet-4-20250514` name tells SWE-agent's LiteLLM to use the OpenAI provider protocol, and the proxy maps `claude-sonnet-4-20250514` to the real Anthropic provider.

- [ ] **Step 2: Commit if changed (may be no-op)**

```bash
git add configs/litellm_model_registry.json
git commit -m "docs: clarify litellm model registry naming for proxy setup"
```

---

### Task 5: Update SWE-agent YAML configs to use proxy

**Files:**
- Modify: `configs/swe_agent_control.yaml:6-8`
- Modify: `configs/swe_agent_treatment.yaml:6-8`
- Modify: `configs/swe_agent_treatment_rewriter.yaml:6-8`

- [ ] **Step 1: Update `swe_agent_control.yaml`**

Change the model section:
```yaml
  model:
    name: openai/claude-sonnet-4-20250514
    api_base: http://localhost:4000/v1
    api_key: ${LITELLM_MASTER_KEY}
```

- [ ] **Step 2: Update `swe_agent_treatment.yaml`**

Same model section change. Also update `propagate_env_variables` — the embedding API calls inside Docker containers now go through the proxy too:
```yaml
    env_variables:
      # ... existing env vars ...
      # Embedding calls go through the LiteLLM proxy on the host
      OPENAI_API_BASE: http://host.docker.internal:4000/v1
    propagate_env_variables:
      - CONTEXT_GRAPH_ROOT
      - LITELLM_MASTER_KEY
```

And in `query_memory_impl.py`, `OPENAI_API_KEY` is used for the embedding client. Since the proxy handles auth, the Docker container just needs to send requests to the proxy with the master key. We propagate `LITELLM_MASTER_KEY` and inside the container, the tool uses it as the API key.

- [ ] **Step 3: Update `swe_agent_treatment_rewriter.yaml`**

Same model + env var changes. Also update rewriter env vars:
```yaml
      REWRITER_API_BASE: http://host.docker.internal:4000/v1
    propagate_env_variables:
      - CONTEXT_GRAPH_ROOT
      - LITELLM_MASTER_KEY
      - REWRITER_API_KEY
```

Since the rewriter also goes through the proxy now, set `REWRITER_API_KEY` to the master key in `.env`.

- [ ] **Step 4: Verify YAML syntax for all configs**

Run: `python -c "import yaml; [yaml.safe_load(open(f'configs/{f}')) for f in ['swe_agent_control.yaml','swe_agent_treatment.yaml','swe_agent_treatment_rewriter.yaml']]"`
Expected: No error

- [ ] **Step 5: Commit**

```bash
git add configs/swe_agent_control.yaml configs/swe_agent_treatment.yaml configs/swe_agent_treatment_rewriter.yaml
git commit -m "feat: point SWE-agent configs to LiteLLM proxy"
```

---

### Task 6: Update GLM configs (optional, for completeness)

**Files:**
- Modify: `configs/glm47_control.yaml:7-8`
- Modify: `configs/glm47_treatment.yaml:7-8`

- [ ] **Step 1: Update both GLM configs**

```yaml
  model:
    name: openai/GLM-4.7
    api_base: http://localhost:4000/v1
    api_key: ${LITELLM_MASTER_KEY}
```

For `glm47_treatment.yaml`, also update:
```yaml
      REWRITER_API_BASE: http://host.docker.internal:4000/v1
```

- [ ] **Step 2: Commit**

```bash
git add configs/glm47_control.yaml configs/glm47_treatment.yaml
git commit -m "feat: point GLM configs to LiteLLM proxy"
```

---

## Chunk 3: Update query_memory Tool for Docker

### Task 7: Update query_memory_impl.py for proxy compatibility

**Files:**
- Modify: `tools/query_memory/bin/query_memory_impl.py:48-57`

- [ ] **Step 1: Update default API base and key env var names**

The Docker container receives `OPENAI_API_BASE` (set to `http://host.docker.internal:4000/v1` in the SWE-agent config) and `LITELLM_MASTER_KEY` (propagated from host). Update the impl to use these:

```python
    # Embedding configuration
    embedding_api_key = os.environ.get("LITELLM_MASTER_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    embedding_base_url = os.environ.get("OPENAI_API_BASE", "http://host.docker.internal:4000/v1")
    embedding_model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-large")

    # Query rewriter configuration
    rewriter_enabled = os.environ.get("REWRITER_ENABLED", "").lower() in ("1", "true", "yes")
    rewriter_api_base = os.environ.get("REWRITER_API_BASE", "http://host.docker.internal:4000/v1")
    rewriter_api_key = os.environ.get("REWRITER_API_KEY", "") or os.environ.get("LITELLM_MASTER_KEY", "")
    rewriter_model = os.environ.get("REWRITER_MODEL", "claude-sonnet-4-20250514")
```

Key changes:
- `embedding_api_key` falls back from `LITELLM_MASTER_KEY` to `OPENAI_API_KEY` (backward compat)
- `embedding_base_url` defaults to proxy on Docker host
- `rewriter_api_key` same fallback pattern
- `rewriter_api_base` defaults to proxy

- [ ] **Step 2: Sync to bundled copy**

Run: `cp tools/query_memory/bin/query_memory_impl.py tools/query_memory/bin/query_memory_impl.py` (this is the same file, no bundled copy needed for impl.py — but sync the agent_memory lib):

```bash
rsync -av --delete agent_memory/ tools/query_memory/lib/agent_memory/ \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='evaluation/'
```

- [ ] **Step 3: Commit**

```bash
git add tools/query_memory/bin/query_memory_impl.py tools/query_memory/lib/agent_memory/
git commit -m "feat: update query_memory tool to use LiteLLM proxy"
```

---

## Chunk 4: Smoke Test

### Task 8: Start proxy and verify end-to-end

- [ ] **Step 1: Update `.env` with real credentials**

Ensure `.env` has:
```
ANTHROPIC_API_KEY=sk-ant-oat01-...  # your Claude Max OAuth token
CHATANYWHERE_API_KEY=sk-...          # existing ChatAnywhere key
LITELLM_MASTER_KEY=sk-litellm-$(openssl rand -hex 16)
```

- [ ] **Step 2: Start the stack**

```bash
docker compose up -d
```

Wait for both containers to be healthy:
```bash
docker compose ps
```
Expected: Both `neo4j-contextgraph` and `litellm-proxy` show "Up"

- [ ] **Step 3: Test LLM routing (Claude via Anthropic)**

```bash
curl -s http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-20250514",
    "messages": [{"role": "user", "content": "Say hello in one word"}],
    "max_tokens": 10
  }'
```

Expected: JSON response with a completion from Claude. If OAuth token is rejected, you'll get an auth error — fall back to ChatAnywhere by changing the provider in `litellm_config.yaml`.

- [ ] **Step 4: Test embedding routing (ChatAnywhere)**

```bash
curl -s http://localhost:4000/v1/embeddings \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "text-embedding-3-large",
    "input": "test embedding"
  }'
```

Expected: JSON response with a 3072-dimensional embedding vector.

- [ ] **Step 5: Test from Python (agent_memory)**

```python
from openai import OpenAI
import os

client = OpenAI(
    base_url="http://localhost:4000/v1",
    api_key=os.environ["LITELLM_MASTER_KEY"],
)

# Test embedding
resp = client.embeddings.create(input="hello", model="text-embedding-3-large")
print(f"Embedding dims: {len(resp.data[0].embedding)}")

# Test chat
resp = client.chat.completions.create(
    model="claude-sonnet-4-20250514",
    messages=[{"role": "user", "content": "Say hi"}],
    max_tokens=10,
)
print(f"Chat response: {resp.choices[0].message.content}")
```

Expected: `Embedding dims: 3072` and a chat response.

- [ ] **Step 6: Commit any fixes**

```bash
git add -A
git commit -m "fix: adjustments from smoke testing LiteLLM proxy"
```

---

### Task 9: Update CLAUDE.md and README

**Files:**
- Modify: `CLAUDE.md` (Infrastructure section)

- [ ] **Step 1: Add LiteLLM proxy section to CLAUDE.md**

Add to the Infrastructure section:
```markdown
### LiteLLM Proxy
- **Container**: `litellm-proxy` (image: `ghcr.io/berriai/litellm:main-latest`)
- **Port**: 4000 (OpenAI-compatible API)
- **Config**: `configs/litellm_config.yaml`
- **Start**: `docker compose up -d` (starts both Neo4j and LiteLLM)
- **Routes**:
  - `claude-*` → Anthropic API (OAuth token)
  - `text-embedding-*` → ChatAnywhere
  - `GLM-*` → Zhipu AI
  - `gpt-*` → OpenAI (reserved for GPT Pro)
```

Update the "Running the Full Experiment" section to use `docker compose up -d` instead of the manual `docker run` command.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add LiteLLM proxy to infrastructure docs"
```
