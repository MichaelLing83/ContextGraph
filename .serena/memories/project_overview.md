# ContextGraph - Project Overview

## Purpose
ContextGraph is a **long-term memory system for coding agents**. It builds a context graph from past SWE-agent trajectories (stored in Neo4j) and provides this memory to agents (SWE-agent, OpenHands) during problem-solving. The goal is to measure whether past experience improves agent performance on new, unseen coding problems via A/B experiments.

## Package
- **Name**: `agent-memory` (pip installable)
- **Python**: >=3.12
- **Dependencies**: neo4j>=5.0, openai>=1.0, numpy>=1.24

## Tech Stack
- **Language**: Python 3.12
- **Database**: Neo4j 5 (Docker container `neo4j-contextgraph`, bolt://localhost:7687)
- **Embeddings**: `text-embedding-3-large` (3072 dim) via ChatAnywhere proxy
- **Agent Frameworks**: SWE-agent v1.1.0, OpenHands v1.3.0+
- **Package Manager**: `uv` (user preference)
- **Testing**: pytest
- **Data Models**: Python dataclasses

## Neo4j Graph Schema

### Nodes (45,115 total)
| Label | Count | Text Field(s) for Embedding |
|---|---|---|
| Fragment | 13,813 | description |
| Strategy | 9,600 | rule_text |
| PlaybookEntry | 8,910 | text |
| CanonicalRule | 7,835 | rule_text |
| Trajectory | 1,795 | summary |
| ProblemSummary | 1,795 | summary_text |
| Community | 781 | summary |
| ErrorPattern | 586 | error_type + error_keywords |

All nodes have 3072-dim embeddings from `text-embedding-3-large`.

### Relationships (73,975 total)
- ADDRESSES_ERROR (18,584): CanonicalRule → ErrorPattern
- HAS_FRAGMENT (13,813): Trajectory → Fragment
- IN_COMMUNITY (13,466): Fragment → Community
- DERIVED_FROM (9,600): Strategy → Trajectory
- MERGED_INTO (8,910): Strategy → CanonicalRule
- CAUSED_ERROR (7,807): Fragment → ErrorPattern
- SUMMARIZES (1,795): ProblemSummary → Trajectory

### Vector Indexes (8 total, all 3072 dim, cosine)
fragment_embedding, trajectory_embedding, strategy_embedding, canonical_rule_embedding, playbook_embedding, community_embedding, error_pattern_embedding, problem_summary_embedding

## Core Architecture

### agent_memory/ (Core Library)
- `memory.py` — `AgentMemory` main entry point (learn, query, close, context manager)
- `models.py` — Data models: `Fragment`, `Strategy`, `CanonicalRule`, `PlaybookEntry`, `ProblemSummary`, `ErrorPattern`, etc.
- `neo4j_store.py` — Neo4j backend (schema init, CRUD, graph export for PPR)
- `playbook.py` — `PlaybookRetriever`: 3-channel retrieval (cosine + BM25 + PPR) with RRF merge + MMR rerank
- `retriever.py` — General graph retriever (uses HAS_FRAGMENT/CAUSED_ERROR relationships)
- `strategy_extractor.py` — LLM-based strategy extraction from trajectories
- `query_rewriter.py` — LLM query rewriting for better retrieval matching
- `writer.py` — `RawTrajectory` → graph nodes (fragments, loops, errors)
- `embeddings.py` — OpenAI embedding wrapper
- `consolidator.py` — Merge similar fragments
- `community.py` — Community detection
- `loop_detector.py` — Detect repeated action patterns
- `reranker.py`, `formatter.py`, `entity_resolver.py`

### Retrieval Pipeline (HippoRAG-style)
1. Query → extract error_type → find seed nodes (ErrorPattern)
2. Three channels: Cosine (vector), BM25 (fulltext), PPR (graph walk, damping=0.5)
3. Node specificity weighting: `s_i = 1/degree(i)` (rare errors weighted higher)
4. RRF merge → MMR rerank (diversity=0.3) → top_k

### tools/query_memory/ (SWE-agent Docker Tool Bundle)
- `bin/query_memory` — Bash wrapper (NOT Python script). Forces Python 3.11+ to avoid testbed Python 3.6 SyntaxError. Must NOT use `exec` (kills pexpect session).
- `bin/query_memory_impl.py` — Actual Python implementation
- `install.sh` — Installs neo4j driver. Does NOT export PYTHONPATH globally (avoids polluting testbed env).
- `lib/agent_memory/` — Bundled copy. Must be synced after modifying `agent_memory/*.py`.

## A/B Experiment Design
- **Training**: 1,795 trajectories from `nebius/SWE-agent-trajectories` (ZERO overlap with SWE-bench)
- **Test**: 200 problems from SWE-bench_Verified (seed=42)
- **Control**: Standard agent, no memory
- **Treatment**: Agent + QueryMemoryTool (SWE-agent) or MemoryHooks (OpenHands)
- **Metrics**: pass@k, pass^k (consistency), token consumption, failure ratio
- **Models**: Claude Sonnet (ChatAnywhere), GLM-4.7 (Zhipu AI)

## Infrastructure
- **Neo4j**: Docker with named volume `neo4j-contextgraph-data`, auth `neo4j/contextgraph123`
- **Embeddings**: ChatAnywhere (`api.chatanywhere.org/v1`), `text-embedding-3-large`, 3072 dim
- **LLM APIs**: ChatAnywhere (Claude), Zhipu AI (GLM-4.7)
- **API keys**: `.env` file (gitignored)
- **Remote Server**: `ssh jie`, project at `~/codes/ContextGraph/`, SWE-agent at `~/codes/SWE-agent/`

## Important Gotchas
1. Never run simulation experiments — only real agent runs
2. SWE-agent must be installed from GitHub (editable mode)
3. SWE-agent tool bundle paths: use ABSOLUTE paths in YAML configs (relative resolves from SWE-agent install dir)
4. Docker required for both SWE-agent and OpenHands
5. On Linux: `host.docker.internal` needs `--add-host` flag
6. Training trajectories use role-based chat format, NOT .traj format
7. `.env` is gitignored
8. After modifying `agent_memory/`, sync `tools/query_memory/lib/agent_memory/`
