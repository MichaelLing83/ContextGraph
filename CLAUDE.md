# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

ContextGraph is a long-term memory system for coding agents. It builds a **context graph** from past SWE-agent trajectories (stored in Neo4j) and provides this memory to agents (SWE-agent, OpenHands) during problem-solving. The goal is to measure whether past experience improves agent performance on new, unseen problems.

### Design Lineage

The architecture draws from four papers — knowing these helps understand why things are built the way they are:

- **HippoRAG** (Gutierrez et al., 2024) — Personalized PageRank over KG for multi-hop associative retrieval (→ PPR channel in `PlaybookRetriever`)
- **Zep** (Rasmussen et al., 2025) — Episode → Semantic → Community three-layer architecture (→ Fragment → CanonicalRule → Community node hierarchy)
- **A-MEM** (Xu et al., 2025) — Zettelkasten-based dynamic linking and memory evolution (→ entity resolution, consolidation)
- **ExpeL** (Zhao et al., 2024) — Experiential learning from success/failure trajectories (→ strategy extraction pipeline)

## Common Commands

Python runs through the project-local `uv` venv — prefer `uv run …` / `uv pip …` over a bare `python` or `pip`.

```bash
# Env setup (once)
uv venv .venv --python 3.12
uv pip install -e '.[dev]'      # or: uv pip install -e .

# Test
uv run pytest                                    # full suite
uv run pytest tests/test_embeddings.py -q        # one file
uv run pytest -k "retrieval and not slow"       # filter by name

# Neo4j + LiteLLM stack (required before any agent/memory run)
docker compose up -d                             # starts baseline Neo4j + litellm-proxy
docker compose --profile experiment up -d        # also starts online-learning Neo4j (7690)
docker compose down                              # stop (data persists in named volumes)

# Build / refresh the context graph from training trajectories
uv run python scripts/build_context_graph.py    # nodes + edges
uv run python scripts/extract_strategies.py
uv run python scripts/deduplicate_strategies.py
uv run python scripts/reembed_all_nodes.py --batch-size 64

# Run an A/B experiment (example: SWE-agent on 200 SWE-bench-verified tasks)
uv run python scripts/run_real_swe_experiment.py --config configs/swe_agent_treatment.yaml

# Re-sync tool bundle after editing agent_memory/ — the SWE-agent tool ships a COPY
rsync -a --delete agent_memory/ tools/query_memory/lib/agent_memory/

# Refresh / verify the LiteLLM OAuth-style Anthropic key (needed whenever the proxy 401s)
./scripts/sync_oauth_token.sh

# Export the baseline graph as a flat JSON (no edges, no embeddings) for ablations / sharing
uv run python scripts/export_nodes_to_json.py --out data/exports/context_graph_nodes.json
```

## Architecture

### Core Library (`agent_memory/`)

The pip package `agent-memory`. Key entry points:

| Module | Role |
|---|---|
| `memory.py` | `AgentMemory` — main facade (learn, query, close) |
| `models.py` | All data models: Fragment, Strategy, CanonicalRule, PlaybookEntry, etc. |
| `neo4j_store.py` | Neo4j graph backend (schema, CRUD, graph export for PPR) |
| `playbook.py` | `PlaybookRetriever` — 3-channel retrieval (cosine + BM25 + PPR) + MMR reranking |
| `writer.py` | RawTrajectory → graph nodes (fragments, loops, errors) |
| `strategy_extractor.py` | LLM-based strategy extraction from trajectories |
| `evaluation/` | Metrics, trajectory parsing, SWE-agent tool integration |

### Retrieval Pipeline (HippoRAG-style)

```
Query → [error_type extraction] → Seed Nodes (ErrorPattern, Trajectory)
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
              Channel 1          Channel 2          Channel 3
            Cosine Search       BM25 Fulltext     PPR Graph Walk
           (vector index)      (fulltext index)   (damping=0.5)
                    │                  │                  │
                    └──────────────────┼──────────────────┘
                                       ▼
                              RRF Merge → MMR Rerank → Top-K
```

- **PPR (Personalized PageRank)**: From seed nodes, discovers multi-hop associated rules
- **Node Specificity**: `s_i = 1/degree(i)` — rare error patterns weighted higher
- **MMR Diversity**: `diversity=0.3` to avoid redundant results

### Other Key Directories

- **`configs/`** — SWE-agent YAML configs for A/B experiments (control vs. treatment, Claude vs. GLM-4.7)
- **`scripts/`** — Runnable scripts for graph building, strategy extraction, experiment running, analysis
- **`experiments/ab_test/`** — A/B experiment framework (runner, collector, metrics, OpenHands integration)
- **`tools/query_memory/`** — SWE-agent tool bundle that runs inside Docker containers; ships a bundled copy of `agent_memory/`
- **`results/live_experiment/`** — Experiment outputs, 200-problem test set, analysis script with McNemar paired test

## Neo4j Graph Schema

### Nodes (45,115 total)
| Label | Count | Key Properties | Embedding |
|---|---|---|---|
| Fragment | 13,813 | description, fragment_type, action_sequence, outcome | 3072 dim |
| Strategy | 9,600 | rule_text, category, prefix, section | 3072 dim |
| PlaybookEntry | 8,910 | text, section, prefix | 3072 dim |
| CanonicalRule | 7,835 | rule_text, category, member_count, avg_confidence | 3072 dim |
| Trajectory | 1,795 | summary, success | 3072 dim |
| ProblemSummary | 1,795 | summary_text | 3072 dim |
| Community | 781 | summary | 3072 dim |
| ErrorPattern | 586 | error_type, error_keywords | 3072 dim |

### Relationships (73,975 total)
| Type | Count | From → To |
|---|---|---|
| ADDRESSES_ERROR | 18,584 | CanonicalRule → ErrorPattern |
| HAS_FRAGMENT | 13,813 | Trajectory → Fragment |
| IN_COMMUNITY | 13,466 | Fragment → Community |
| DERIVED_FROM | 9,600 | Strategy → Trajectory |
| MERGED_INTO | 8,910 | Strategy → CanonicalRule |
| CAUSED_ERROR | 7,807 | Fragment → ErrorPattern |
| SUMMARIZES | 1,795 | ProblemSummary → Trajectory |

### Embeddings
- **Model**: `text-embedding-3-large` (3072 dimensions)
- **Provider**: ChatAnywhere proxy (`https://api.chatanywhere.org/v1`)
- **All 45,115 nodes** have embeddings
- **Vector indexes**: One per node label (cosine similarity)

## A/B Experiment Design

### Goal
Measure whether a context graph built from past experiences improves agent performance on **new, unseen** coding problems.

### Training Data
- **Source**: `nebius/SWE-agent-trajectories` on Hugging Face (80K+ trajectories, 319 repos)
- **Used**: 3,591 trajectories (role-based chat format, not standard .traj)
- **Split**: ~1,795 train / ~1,796 test (seed=42)
- **Key insight**: These trajectories have **ZERO overlap** with SWE-bench — avoids data leakage, tests knowledge transferability.

### Test Data
- **Source**: `princeton-nlp/SWE-bench_Verified` (500 problems)
- **Selected**: 200 problems (seed=42, deterministic)
- **Cached in**: `results/live_experiment/verified_200.json`

### Groups
- **Control**: Standard agent, no memory context
- **Treatment**: Agent + QueryMemoryTool (SWE-agent) or MemoryHooks (OpenHands)

### Metrics
1. **pass@k** (k=1,3,5) — success rate
2. **pass^k** — consistency metric (all k attempts succeed)
3. **Token consumption** — cost efficiency
4. **Failure attempt ratio** — loop/error rate

## Infrastructure

### Neo4j Instances

| Container | Bolt Port | Volume | Purpose | Writable? |
|-----------|-----------|--------|---------|-----------|
| `neo4j-contextgraph` | **7687** | `neo4j-contextgraph-data` | **Baseline (READ-ONLY)** — 8,910 PlaybookEntry from 1,795 trajectories. Never modify. | No |
| `neo4j-contextgraph-enhanced` | 7688 | `neo4j-contextgraph-enhanced` | +7 hand-written strategies (Approach C/D test) | No |
| `neo4j-contextgraph-repospecs` | 7689 | `neo4j-contextgraph-repospecs` | +25 repo-specific strategies from v3 resolved problems | No |
| `neo4j-contextgraph-online` | 7690 | `neo4j-contextgraph-online` | Online learning — treatment writes here during experiments | Yes |

- **Auth**: All use `neo4j/contextgraph123`
- **Baseline (7687) is READ-ONLY**: All experiments that modify the graph must use a copy (7688-7690) or create a new container from the dump at `/tmp/neo4j.dump`
- **Start baseline + proxy**: `docker compose up -d`
- **Start with online-learning instance**: `docker compose --profile experiment up -d`
- **Start standalone containers** (enhanced, repospecs): `docker start neo4j-contextgraph-{enhanced,repospecs}`
- **Create new from baseline**: `docker volume create <name> && docker run --rm -v <name>:/data -v /tmp:/backup neo4j:5 neo4j-admin database load neo4j --from-path=/backup --overwrite-destination`

### LiteLLM Proxy
- **Container**: `litellm-proxy` (image: `ghcr.io/berriai/litellm:main-stable`)
- **Port**: 4000 (OpenAI-compatible API)
- **Config**: `configs/litellm_config.yaml`
- **Start**: `docker compose up -d` (starts both Neo4j and LiteLLM)
- **Routes**:
  - `claude-*` → ChatAnywhere (OpenAI-compatible)
  - `text-embedding-*` → ChatAnywhere
  - `GLM-*` → Zhipu AI
  - `gpt-*` → OpenAI (reserved for GPT Pro)

#### LiteLLM Setup

1. **Copy `.env.example` to `.env`**:
   ```bash
   cp .env.example .env
   ```

2. **Fill in required keys in `.env`**:
   ```bash
   # Generate a random master key for the proxy
   echo "LITELLM_MASTER_KEY=sk-litellm-$(openssl rand -hex 16)" >> .env

   # ChatAnywhere key (for embeddings) — copy from existing OPENAI_API_KEY
   echo "CHATANYWHERE_API_KEY=<your-chatanywhere-key>" >> .env

   # (Optional) Zhipu AI key for GLM-4.7
   # echo "ZHIPU_API_KEY=..." >> .env
   ```

3. **Sync Claude Max OAuth token** (auto-reads from `~/.claude/.credentials.json`):
   ```bash
   ./scripts/sync_oauth_token.sh
   ```
   This reads the `accessToken` from Claude Code's credentials and writes it as `ANTHROPIC_API_KEY` in `.env`. Re-run when the token expires (~8-12h).

4. **Start the proxy**:
   ```bash
   docker compose up -d
   ```

5. **Verify**:
   ```bash
   curl http://localhost:4000/health
   ```

6. **Web UI**: `http://localhost:4000/ui` (login with master key)

### API Providers (via LiteLLM Proxy)
- **Embeddings**: ChatAnywhere (`https://api.chatanywhere.org`), model `text-embedding-3-large`
- **LLM (Claude)**: ChatAnywhere (`https://api.chatanywhere.org`), model `claude-sonnet-4-20250514`
- **LLM (GLM)**: Zhipu AI (`https://open.bigmodel.cn/api/coding/paas/v4`), model `GLM-4.7`
- **API keys**: In `.env` file (gitignored), see `.env.example` for template

### Python Environment
- **Use `uv`** for virtual environment management (never bare `pip`/`python`)
- **Python**: pyproject declares `requires-python = ">=3.10"`, but dev/target is 3.12
- **Create**: `uv venv .venv --python 3.12`
- **Install**: `uv pip install -e '.[dev]'`
- **Run**: always `uv run <cmd>` so tooling uses the venv interpreter

### SWE-agent (v1.1.0)
- **Install**: Clone `https://github.com/SWE-agent/SWE-agent.git` tag v1.1.0, then `uv pip install -e .` (editable mode required)
- **CLI**: `python -m sweagent run-batch --config <yaml>`
- **Tool bundle path**: Use **absolute path** in YAML config (e.g. `/Users/zihanwu/Public/codes/ContextGraph/tools/query_memory`). Relative paths resolve against SWE-agent's own install dir, not the project root.

### OpenHands (v1.3.0+)
- **Install**: `uv pip install openhands-ai`
- **Integration**: `MemoryHooks` in `experiments/ab_test/openhands_integration.py`

## Important Gotchas

1. **Never run simulation experiments** — only real agent runs.
2. **Always run SWE-bench verify before analyzing results** — after an A/B run completes, extract diffs and run `swebench.harness.run_evaluation` with `--dataset_name princeton-nlp/SWE-bench_Verified`. Raw completion/timeout numbers are not enough; resolved counts from `swebench` are ground truth.
3. **SWE-agent pip install** from PyPI gives the wrong package. Always install from GitHub, editable.
4. **SWE-agent tool bundle paths**: SWE-agent resolves relative paths from its own install dir (`~/codes/SWE-agent/`). Always pass absolute paths in YAML configs.
5. **Docker required** for both SWE-agent and OpenHands runs.
6. **Treatment group on Linux**: add `--add-host=host.docker.internal:host-gateway` so the container can reach the LiteLLM proxy.
7. **SWE-bench testbed Python**: can be as old as 3.6. The `tools/query_memory` bash wrapper pins Python 3.11+ to avoid `SyntaxError` in the neo4j driver. Do NOT use `exec` in the wrapper — it kills the pexpect session.
8. **Trajectory format**: training trajectories use role-based chat format (`role`/`text` fields), NOT the standard `.traj` layout.
9. **`.env` is gitignored** — holds API keys and the OAuth token for the LiteLLM proxy.
10. **Sync bundled `agent_memory`**: after modifying `agent_memory/*.py`, mirror the change into `tools/query_memory/lib/agent_memory/` (the SWE-agent tool ships a copy).
11. **Neo4j volume**: data lives in the `neo4j-contextgraph-data` named volume. After a rebuild, re-run `uv run python scripts/build_context_graph.py`.
12. **PRs go to `wzh4464` forks only** — never target upstream (`OpenAutoCoder/live-swe-agent`, etc.) unless the user explicitly says so.
13. **live-SWE-agent**: submodule at `vendor/live-swe-agent` (fork: `wzh4464/live-swe-agent`). Install: `cd vendor/live-swe-agent && uv pip install -e .`

## Remote Server

- **SSH**: `ssh jie`
- **Project path**: `~/codes/ContextGraph/`
- **SWE-agent repo**: `~/codes/SWE-agent/` (editable install into project venv)
- **Docker**: Available, Neo4j container with named volume
- **Python**: 3.12.3
- **uv**: `~/.local/bin/uv`

## Git & Branching

- **Main branch**: `main`
- **Remote**: GitHub (`wzh4464/ContextGraph`)

## Running the Full Experiment

```bash
# 1. Start Neo4j + LiteLLM proxy
docker compose up -d

# 2. Build context graph (~12 min for 1,795 trajectories)
uv run python scripts/build_context_graph.py

# 3. Extract strategies + deduplicate + playbook
uv run python scripts/extract_strategies.py
uv run python scripts/deduplicate_strategies.py
uv run python scripts/ingest_playbook.py

# 4. Re-embed all nodes with text-embedding-3-large (3072 dim)
uv run python scripts/reembed_all_nodes.py --batch-size 50

# 5. Run SWE-agent A/B experiment
uv run python scripts/run_real_swe_experiment.py

# 6. Run OpenHands A/B experiment
uv run python scripts/run_real_openhands_experiment.py --n 200

# 7. Analyze results
uv run python results/live_experiment/run_live_analysis.py
```
