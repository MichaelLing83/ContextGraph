# ContextGraph - Project Guide

## Overview

ContextGraph is a long-term memory system for coding agents. It builds a **context graph** from past SWE-agent trajectories (stored in Neo4j) and provides this memory to agents (SWE-agent, OpenHands) during problem-solving. The goal is to measure whether past experience improves agent performance on new, unseen problems.

## Project Structure

```
agent_memory/                  # Core library (pip package: agent-memory)
├── memory.py                  # AgentMemory - main entry point (learn, query, close)
├── models.py                  # Data models: Fragment, Strategy, CanonicalRule, PlaybookEntry, etc.
├── neo4j_store.py             # Neo4j graph backend (schema, CRUD, graph export for PPR)
├── playbook.py                # PlaybookRetriever: 3-channel retrieval (cosine + BM25 + PPR) + MMR
├── retriever.py               # General graph retriever (fragment/error retrieval)
├── strategy_extractor.py      # LLM-based strategy extraction from trajectories
├── query_rewriter.py          # LLM query rewriting for better retrieval matching
├── writer.py                  # RawTrajectory → graph nodes (fragments, loops, errors)
├── consolidator.py            # Merge similar fragments into methodologies
├── community.py               # Community detection and summarization
├── loop_detector.py           # Detect repeated action patterns in trajectories
├── embeddings.py              # OpenAI embedding wrapper (text-embedding-3-large, 3072 dim)
├── reranker.py                # Result reranking utilities
├── formatter.py               # Output formatting for agent consumption
├── entity_resolver.py         # Entity resolution
└── evaluation/                # Evaluation framework
    ├── metrics.py             # ProblemResult, EvaluationMetrics, calculate_metrics()
    ├── analyzer.py            # compare_results(), ComparisonReport
    ├── swe_agent_tool.py      # QueryMemoryTool for SWE-agent function calling
    ├── trajectory_parser.py   # Parse .traj files → RawTrajectory
    ├── data_splitter.py       # random_split() with seed
    ├── experiment.py          # OLD simulation experiment - DO NOT USE
    └── graph_builder.py       # Build graph from trajectories

configs/                       # SWE-agent YAML configs for A/B experiments
├── swe_agent_control.yaml     # Control: standard SWE-agent (Claude)
├── swe_agent_treatment.yaml   # Treatment: + QueryMemoryTool (Claude)
├── swe_agent_treatment_rewriter.yaml  # Treatment + query rewriter
├── glm47_control.yaml         # Control: GLM-4.7
└── glm47_treatment.yaml       # Treatment: GLM-4.7 + memory

scripts/                       # Runnable scripts
├── build_context_graph.py     # Build Neo4j graph from training trajectories
├── extract_strategies.py      # LLM strategy extraction from trajectories
├── deduplicate_strategies.py  # Cosine clustering → CanonicalRule nodes + graph linking
├── ingest_playbook.py         # Create PlaybookEntry nodes
├── reembed_all_nodes.py       # Re-embed all nodes (text-embedding-3-large, 3072 dim)
├── extract_anti_patterns.py   # Extract anti-patterns from failures
├── migrate_schema_v2.py       # Schema migration utilities
├── run_real_swe_experiment.py # Real SWE-agent A/B runner (200 problems)
├── run_real_openhands_experiment.py  # Real OpenHands A/B runner
├── run_rewriter_experiment.py # Rewriter ablation experiment
├── run_online_learning_experiment.py # Online learning experiment
├── analyze_online_learning.py # Analysis with pass^k metrics
├── prepare_split.py           # Prepare train/test splits
├── collect_swe_agent_results.py
└── collect_openhands_results.py

experiments/ab_test/           # A/B experiment framework
├── config.py                  # ExperimentConfig, get_config()
├── graph_builder.py           # AgentMemoryGraph, load_graph()
├── openhands_integration.py   # MemoryHooks (pre/post action), ExperimentGroup
├── runner.py                  # Experiment runner
├── simulator.py               # Simulation runner (NOT real — never use)
├── analysis.py                # Analysis utilities
├── collector.py               # Results collector
└── metrics.py                 # A/B metrics

tools/query_memory/            # SWE-agent tool bundle (runs inside Docker containers)
├── config.yaml                # Tool definition (function_calling schema)
├── bin/query_memory           # Bash wrapper — uses Python 3.11+ to avoid testbed Python 3.6
├── bin/query_memory_impl.py   # Actual Python implementation
├── install.sh                 # Installs neo4j driver inside SWE-agent Docker container
└── lib/agent_memory/          # Bundled agent_memory source for Docker container

results/live_experiment/       # All experiment outputs
├── verified_200.json          # 200 selected SWE-bench Verified instance IDs (seed=42)
├── split.json                 # Test IDs for the 200-problem experiment
├── graph_build_stats.json     # Stats from context graph building
└── run_live_analysis.py       # Analysis script with McNemar paired test

tests/                         # pytest test suite
```

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

### Retrieval Pipeline (HippoRAG-style)
The `PlaybookRetriever` uses three channels:
1. **Cosine**: Vector similarity on `canonical_rule_embedding` index
2. **BM25**: Fulltext search on `canonical_rule_text` index
3. **PPR**: Personalized PageRank from seed nodes (ErrorPattern matching query error_type)

Channels are merged via **RRF (Reciprocal Rank Fusion)**, then reranked with **MMR** (diversity=0.3).

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

### Neo4j
- **Container**: `neo4j-contextgraph` (image: `neo4j:5`)
- **Volume**: `neo4j-contextgraph-data` (persistent)
- **Ports**: 7474 (HTTP), 7687 (Bolt)
- **Auth**: Set via `NEO4J_AUTH` env var (see `.env`)
- **Start**: `docker compose up -d neo4j` (or see LiteLLM Proxy below)

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
   # Health check
   curl http://localhost:4000/health

   # Test chat completion
   curl -s http://localhost:4000/v1/chat/completions \
     -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
     -H "Content-Type: application/json" \
     -d '{"model":"claude-sonnet-4-20250514","messages":[{"role":"user","content":"hi"}],"max_tokens":5}'

   # Test embedding
   curl -s http://localhost:4000/v1/embeddings \
     -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
     -H "Content-Type: application/json" \
     -d '{"model":"text-embedding-3-large","input":"test"}'
   ```

6. **Web UI**: `http://localhost:4000/ui` (login with master key)

#### Key Migration from Old Setup

If migrating from the old ChatAnywhere-only setup:
- Old `OPENAI_API_KEY` → rename to `CHATANYWHERE_API_KEY` in `.env`
- Old `OPENAI_API_BASE` → no longer needed (proxy handles routing)
- SWE-agent configs now point to `http://localhost:4000/v1` instead of provider URLs
- Docker containers use `${LITELLM_PROXY_HOST:-host.docker.internal}:4000` to reach the proxy

### API Providers (via LiteLLM Proxy)
- **Embeddings**: ChatAnywhere (`https://api.chatanywhere.org`), model `text-embedding-3-large`
- **LLM (Claude)**: ChatAnywhere (`https://api.chatanywhere.org`), model `claude-sonnet-4-20250514`
- **LLM (GLM)**: Zhipu AI (`https://open.bigmodel.cn/api/coding/paas/v4`), model `GLM-4.7`
- **API keys**: In `.env` file (gitignored), see `.env.example` for template

### Python Environment
- **Use `uv`** for virtual environment management (user preference)
- **Python**: >=3.12
- **Create**: `uv venv .venv --python 3.12`
- **Install**: `uv pip install -e '.[dev]'`

### SWE-agent (v1.1.0)
- **Install**: Clone `https://github.com/SWE-agent/SWE-agent.git` tag v1.1.0, then `uv pip install -e .` (editable mode required)
- **CLI**: `python -m sweagent run-batch --config <yaml>`
- **Tool bundle path**: Use **absolute path** in YAML config (`/home/jie/codes/ContextGraph/tools/query_memory`), NOT relative — SWE-agent resolves relative paths from its own install dir

### OpenHands (v1.3.0+)
- **Install**: `uv pip install openhands-ai`
- **Integration**: `MemoryHooks` in `experiments/ab_test/openhands_integration.py`

## Important Gotchas

1. **Never run simulation experiments** — only real agent runs
2. **SWE-agent pip install** from PyPI gives wrong package. Always install from GitHub (editable)
3. **SWE-agent tool bundle paths**: Relative paths resolve from SWE-agent install dir (`~/codes/SWE-agent/`). Always use absolute paths in YAML configs
4. **Docker required** for both SWE-agent and OpenHands
5. **Treatment group on Linux**: Add `--add-host=host.docker.internal:host-gateway` to Docker
6. **SWE-bench testbed Python**: Can be Python 3.6. The `query_memory` bash wrapper forces Python 3.11+ to avoid SyntaxError in neo4j driver. Do NOT use `exec` in the wrapper (kills pexpect session)
7. **Trajectory format**: Training trajectories use role-based chat format (`role`/`text` fields), NOT standard `.traj`
8. **`.env` file** is gitignored — contains API keys
9. **Sync bundled agent_memory**: After modifying `agent_memory/*.py`, sync to `tools/query_memory/lib/agent_memory/`
10. **Neo4j data**: Use named Docker volume (`neo4j-contextgraph-data`) for persistence. After rebuild: `python scripts/build_context_graph.py`

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
- **Current feature branch**: `feat/playbook-retrieval-improvements`

## Running the Full Experiment

```bash
# 1. Start Neo4j + LiteLLM proxy
docker compose up -d

# 2. Build context graph (~12 min for 1,795 trajectories)
python scripts/build_context_graph.py

# 3. Extract strategies + deduplicate + playbook
python scripts/extract_strategies.py
python scripts/deduplicate_strategies.py
python scripts/ingest_playbook.py

# 4. Re-embed all nodes with text-embedding-3-large (3072 dim)
python scripts/reembed_all_nodes.py --batch-size 50

# 5. Run SWE-agent A/B experiment
python scripts/run_real_swe_experiment.py

# 6. Run OpenHands A/B experiment
python scripts/run_real_openhands_experiment.py --n 200

# 7. Analyze results
python results/live_experiment/run_live_analysis.py
```
