# ContextGraph - Project Overview

## Purpose
ContextGraph is a **long-term memory system for coding agents**. It builds a context graph from past SWE-agent trajectories (stored in Neo4j) and provides this memory to agents (SWE-agent, OpenHands) during problem-solving. The goal is to measure whether past experience improves agent performance on new, unseen coding problems via A/B experiments.

## Package
- **Name**: `agent-memory` (pip installable)
- **Python**: >=3.10 (>=3.12 for OpenHands)
- **Dependencies**: neo4j>=5.0, openai>=1.0, numpy>=1.24

## Tech Stack
- **Language**: Python 3.12
- **Database**: Neo4j 5 (Docker container `neo4j-contextgraph`, bolt://localhost:7687)
- **Embeddings**: OpenAI API
- **Agent Frameworks**: SWE-agent v1.1.0, OpenHands v1.3.0+
- **Package Manager**: `uv` (user preference)
- **Testing**: pytest
- **Data Models**: Python dataclasses
- **Build**: setuptools

## Core Architecture

### agent_memory/ (Core Library)
- `memory.py` — `AgentMemory` main entry point (learn, query, close, context manager)
- `models.py` — Data models: `Trajectory`, `Fragment`, `State`, `Methodology`, `ErrorPattern` (all dataclasses with to_dict/from_dict)
- `neo4j_store.py` — Neo4j graph database backend
- `writer.py` — `RawTrajectory` → graph nodes (fragments, loops, errors)
- `retriever.py` — Query graph for relevant past experiences (**REWRITTEN** to match actual Neo4j schema: uses HAS_FRAGMENT/CAUSED_ERROR relationships, no Methodology/RESOLVED_BY)
- `consolidator.py` — Merge similar fragments into methodologies
- `loop_detector.py` — Detect repeated action patterns in trajectories
- `embeddings.py` — OpenAI embedding wrapper

### agent_memory/evaluation/ (Evaluation Framework)
- `metrics.py` — `ProblemResult`, `EvaluationMetrics`, `calculate_metrics()`
- `analyzer.py` — `compare_results()`, `ComparisonReport`
- `swe_agent_tool.py` — `QueryMemoryTool` for SWE-agent function calling
- `trajectory_parser.py` — Parse .traj files → RawTrajectory
- `data_splitter.py` — `random_split()` with seed
- `experiment.py` — OLD simulation experiment (DO NOT USE for real runs)
- `graph_builder.py` — Build graph from trajectories

### experiments/ab_test/ (A/B Experiment Framework)
- `config.py` — `ExperimentConfig`, `get_config()` with nested dataclass configs
- `graph_builder.py` — `AgentMemoryGraph`, `load_graph()`
- `openhands_integration.py` — `MemoryHooks` (pre/post action), `ExperimentGroup`, `MemoryContext`
- `runner.py` — Experiment runner
- `simulator.py` — Simulation runner (NOT real — never use)
- `analysis.py` — Analysis utilities
- `collector.py` — Results collector
- `metrics.py` — A/B metrics

### scripts/ (Runnable Scripts)
- `build_context_graph.py` — Build Neo4j graph from training trajectories (chat format)
- `run_real_swe_experiment.py` — Real SWE-agent A/B runner (200 problems)
- `run_real_openhands_experiment.py` — Real OpenHands A/B runner
- `prepare_split.py` — Prepare train/test splits
- `collect_swe_agent_results.py` / `collect_openhands_results.py`

### configs/ (SWE-agent YAML Configs)
- `swe_agent_control.yaml` — Control: standard SWE-agent, no memory
- `swe_agent_treatment.yaml` — Treatment: + QueryMemoryTool + Neo4j env vars

### tools/query_memory/ (SWE-agent Tool Bundle for Docker)
- `config.yaml` — Tool definition (function_calling schema)
- `bin/query_memory` — Executable that queries Neo4j from inside Docker
- `install.sh` — Installs Python deps inside SWE-agent Docker container
- `lib/` — Bundled agent_memory source for Docker container

### results/live_experiment/ (Experiment Outputs)
- `verified_200.json` — 200 selected SWE-bench Verified instance IDs
- `split.json` — Train/test split
- `graph_build_stats.json` — Stats from context graph building
- `run_live_analysis.py` — Analysis script with McNemar paired test
- Various result JSON files and analysis reports

### tests/ (Test Suite)
- Unit tests for all core modules
- Evaluation-specific tests in `tests/evaluation/`
- `conftest.py` — Neo4j fixtures

## A/B Experiment Design
- **Training**: 3,591 trajectories from `nebius/SWE-agent-trajectories` (ZERO overlap with SWE-bench)
- **Test**: 200 problems from SWE-bench_Verified (seed=42)
- **Control**: Standard agent, no memory
- **Treatment**: Agent + QueryMemoryTool (SWE-agent) or MemoryHooks (OpenHands)
- **Metrics**: pass@k (k=1,3,5), token consumption, failure attempt ratio

## Neo4j Graph Schema (Actual)
- **Nodes**: Trajectory (1,795), Fragment (13,813), ErrorPattern (586) — **NO Methodology nodes**
- **Relationships**: HAS_FRAGMENT (13,813), CAUSED_ERROR (7,807) — **NO RESOLVED_BY**
- **Key properties**: Trajectory.success, Fragment.fragment_type/description/phase, ErrorPattern.error_type/message

## Infrastructure
- **Neo4j**: Docker container, auth `neo4j/contextgraph123`, ports 7474/7687
- **API Proxy**: ChatAnywhere (`api.chatanywhere.org/v1`), OpenAI-compatible format via litellm
- **Old API Proxy**: `https://crs.itssx.com/api`, model `claude-sonnet-4-20250514`, $3/instance limit
- **API key**: `.env` file (gitignored)
- **Remote Server**: `ssh jie`, project at `~/codes/ContextGraph/`

## Important Gotchas
1. Never run simulation experiments — only real agent runs
2. SWE-agent must be installed from GitHub (editable mode required)
3. Docker required for both SWE-agent and OpenHands
4. Neo4j data ephemeral unless using named Docker volume
5. On Linux: `host.docker.internal` needs `--add-host` flag
6. Training trajectories use role-based chat format, NOT .traj format
7. `.env` is gitignored
