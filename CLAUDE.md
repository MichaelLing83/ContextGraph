# ContextGraph - Project Guide

## Overview

ContextGraph is a long-term memory system for coding agents. It builds a **context graph** from past SWE-agent trajectories (stored in Neo4j) and provides this memory to agents (SWE-agent, OpenHands) during problem-solving. The goal is to measure whether past experience improves agent performance on new, unseen problems.

## Project Structure

```
agent_memory/              # Core library (pip package: agent-memory)
├── memory.py              # AgentMemory - main entry point (learn, query, close)
├── models.py              # Data models (MemoryQuery, MemoryResult, etc.)
├── neo4j_store.py         # Neo4j graph database backend
├── writer.py              # RawTrajectory → graph nodes (fragments, loops, errors)
├── retriever.py           # Query graph for relevant past experiences
├── consolidator.py        # Merge similar fragments into methodologies
├── loop_detector.py       # Detect repeated action patterns in trajectories
├── embeddings.py          # OpenAI embedding wrapper
└── evaluation/            # Evaluation framework (PR #11)
    ├── metrics.py         # ProblemResult, EvaluationMetrics, calculate_metrics()
    ├── analyzer.py        # compare_results(), ComparisonReport
    ├── swe_agent_tool.py  # QueryMemoryTool for SWE-agent function calling
    ├── trajectory_parser.py  # Parse .traj files → RawTrajectory
    ├── data_splitter.py   # random_split() with seed
    ├── experiment.py      # OLD simulation experiment (not real) - DO NOT USE for real runs
    └── graph_builder.py   # Build graph from trajectories

configs/                   # SWE-agent YAML configs for A/B experiment
├── swe_agent_control.yaml    # Control: standard SWE-agent, no memory
└── swe_agent_treatment.yaml  # Treatment: + QueryMemoryTool + Neo4j env vars

scripts/                   # Runnable scripts
├── build_context_graph.py    # Build Neo4j graph from training trajectories
├── run_real_swe_experiment.py   # Real SWE-agent A/B runner (200 problems)
├── run_real_openhands_experiment.py  # Real OpenHands A/B runner (200 problems)
├── run_evaluation.py         # General evaluation runner
├── prepare_split.py          # Prepare train/test splits
├── collect_swe_agent_results.py
└── collect_openhands_results.py

experiments/ab_test/       # A/B experiment framework
├── config.py              # ExperimentConfig, get_config()
├── graph_builder.py       # AgentMemoryGraph, load_graph()
├── openhands_integration.py  # MemoryHooks (pre/post action), ExperimentGroup, MemoryContext
├── runner.py              # Experiment runner
├── simulator.py           # Simulation runner (NOT real)
├── analysis.py            # Analysis utilities
├── collector.py           # Results collector
└── metrics.py             # A/B metrics

tools/query_memory/        # SWE-agent tool bundle for querying Neo4j memory
├── config.yaml            # Tool definition (function_calling schema)
├── bin/query_memory        # Executable that queries Neo4j from inside Docker
├── install.sh             # Installs Python deps inside SWE-agent Docker container
└── lib/                   # Bundled agent_memory source for Docker container

results/live_experiment/   # All experiment outputs
├── verified_200.json      # 200 selected SWE-bench Verified instance IDs (seed=42)
├── split.json             # Test IDs for the 200-problem experiment
├── graph_build_stats.json # Stats from context graph building
└── run_live_analysis.py   # Analysis script with McNemar paired test
```

## A/B Experiment Design

### Goal
Measure whether a context graph built from past experiences improves agent performance on **new, unseen** coding problems.

### Training Data
- **Source**: `nebius/SWE-agent-trajectories` on Hugging Face (80K+ trajectories, 319 repos)
- **Used**: 3,591 trajectories (role-based chat format, not standard .traj)
- **Split**: ~1,795 train / ~1,796 test (seed=42)
- **Key insight**: These trajectories have **ZERO overlap** with SWE-bench (Lite/Verified/Full) — completely different repos. This is by design: avoids data leakage, tests knowledge transferability.

### Test Data
- **Source**: `princeton-nlp/SWE-bench_Verified` (500 problems)
- **Selected**: 200 problems (seed=42, deterministic)
- **Cached in**: `results/live_experiment/verified_200.json`

### Groups
- **Control**: Standard agent, no memory context
- **Treatment**: Agent + QueryMemoryTool (SWE-agent) or MemoryHooks (OpenHands)

### Metrics
1. **pass@k** (k=1,3,5) — success rate
2. **Token consumption** — cost efficiency
3. **Failure attempt ratio** — loop/error rate

### Result Format
```json
{
  "agent": "swe-agent",
  "n_problems": 200,
  "control": {
    "problems": [{"id": "instance_id", "attempts": [true, false], "tokens": [1234, 5678]}]
  },
  "treatment": {
    "problems": [{"id": "instance_id", "attempts": [true], "tokens": [1234]}]
  }
}
```

## Infrastructure

### Neo4j
- **Container**: `neo4j-contextgraph` (image: `neo4j:5`)
- **Ports**: 7474 (HTTP), 7687 (Bolt)
- **Auth**: `neo4j` / `contextgraph123`
- **Start**: `docker run -d --name neo4j-contextgraph -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/contextgraph123 neo4j:5`
- **Graph must be rebuilt** after container recreation: `python scripts/build_context_graph.py`
  - Reads train trajectory paths from `results/live_experiment/split.json` (key: `train_files`)
  - Takes ~12 min for 1,795 trajectories (~2.5 files/sec)

### API Proxy
- **Base URL**: `https://crs.itssx.com/api`
- **Model**: `claude-sonnet-4-20250514`
- **Cost limit**: $3 per instance
- **API key**: In `.env` file (`ANTHROPIC_API_KEY`)

### Python Environment
- **Use `uv`** for virtual environment management (user preference)
- **Python**: >=3.12 (OpenHands requirement)
- **Create**: `uv venv .venv --python 3.12`
- **Install**: `uv pip install -e '.[dev]'`
- **Extra deps**: `sweagent` (from git, editable), `openhands-ai`, `datasets`, `python-dotenv`

### SWE-agent (v1.1.0)
- **Install**: Clone `https://github.com/SWE-agent/SWE-agent.git` tag v1.1.0, then `uv pip install -e .` (editable mode required — pip install from git misses config/tools dirs)
- **CLI**: `python -m sweagent run-batch --config <yaml>` (NOT `python -m sweagent.run.run`)
- **Needs**: Docker (creates per-instance containers for SWE-bench evaluation)
- **Config dir**: Set `SWE_AGENT_CONFIG_DIR` env var if installed from git outside project

### OpenHands (v1.3.0+)
- **Install**: `uv pip install openhands-ai`
- **Integration**: `MemoryHooks` in `experiments/ab_test/openhands_integration.py`
  - Hooks inject memory context via `conversation_instructions` parameter
  - `use_host_network=True` for Docker→host Neo4j access
- **API**: `from openhands.core.main import create_runtime, run_controller`

## Important Gotchas

1. **Never run simulation experiments** — user explicitly rejected simulated results. Only real agent runs.
2. **SWE-agent pip install** from PyPI (`sweagent`) gives wrong package (togetherunidiff). Always install from GitHub.
3. **SWE-agent editable install** required — non-editable install misses `config/` and `tools/` directories that `__init__.py` asserts exist.
4. **Docker required** for both SWE-agent and OpenHands — they run SWE-bench instances in containers.
5. **Neo4j data is ephemeral** unless using a named Docker volume. After container recreation, run `build_context_graph.py`.
6. **Treatment group on Linux**: The `NEO4J_URI` in `swe_agent_treatment.yaml` uses `host.docker.internal`. On Linux, add `--add-host=host.docker.internal:host-gateway` to Docker run command.
7. **trajectory format**: Training trajectories from nebius use role-based chat format (`role`/`text` fields), NOT standard SWE-agent `.traj` format. The `build_context_graph.py` script handles this via `parse_chat_trajectory()`.
8. **.env file** is gitignored — contains `ANTHROPIC_API_KEY` and `ANTHROPIC_API_BASE`. Must be present on each machine.

## Remote Server (jie)

- **SSH**: `ssh jie`
- **Project path**: `~/codes/ContextGraph/`
- **SWE-agent repo**: `~/codes/SWE-agent/` (editable install into project venv)
- **Docker**: Available, Neo4j container `neo4j-contextgraph`
- **Python**: 3.12.3 (`/usr/bin/python3.12`)
- **uv**: `~/.local/bin/uv`
- **Disk**: `/home/jie` has 1.5T available
- **GPU**: Linux x86_64 server
- **Note**: `host.docker.internal` needs `--add-host` flag on Linux Docker

## Git & Branching

- **Main branch**: `main`
- **Remote**: GitHub (`wzh4464/ContextGraph`)
- **PR #11**: Merged — evaluation pipeline with simulation mode
- **Untracked files**: configs/, scripts/, tools/, experiments/ab_test/results/, results/live_experiment/ — these contain real experiment infrastructure not yet committed

## Running the Full Experiment

```bash
# 1. Start Neo4j
docker run -d --name neo4j-contextgraph -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/contextgraph123 neo4j:5

# 2. Build context graph (needs training trajectories downloaded)
python scripts/build_context_graph.py

# 3. Run SWE-agent A/B (control + treatment, 200 problems)
python scripts/run_real_swe_experiment.py

# 4. Run OpenHands A/B (control + treatment, 200 problems)
python scripts/run_real_openhands_experiment.py --n 200

# 5. Analyze results
python results/live_experiment/run_live_analysis.py
```
