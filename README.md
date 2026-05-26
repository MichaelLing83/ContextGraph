# ContextGraph

A **long-term memory system for coding agents**. Builds a context graph from past SWE-agent trajectories (stored in Neo4j) and retrieves relevant debugging strategies during problem-solving.

**Obsidian vault mode** (no Neo4j): ingest markdown notes into a separate graph vault as `[[wikilinks]]` + `#cg/*` tags, then search and summarize from the CLI. See **[docs/obsidian-vault.md](docs/obsidian-vault.md)**.

## Design References

1. **HippoRAG** (Gutierrez et al., 2024) — Hippocampal indexing theory for retrieval: Personalized PageRank over KG for multi-hop associative retrieval
2. **Zep** (Rasmussen et al., 2025) — Temporal knowledge graph: Episode → Semantic → Community three-layer architecture
3. **A-MEM** (Xu et al., 2025) — Zettelkasten-based dynamic linking and memory evolution
4. **ExpeL** (Zhao et al., 2024) — Experiential learning from success/failure trajectories

## Architecture

### Graph Schema (Neo4j)

```
Trajectory (1,795) ──HAS_FRAGMENT──▶ Fragment (13,813) ──CAUSED_ERROR──▶ ErrorPattern (586)
     │                                    │
     │ DERIVED_FROM                       │ IN_COMMUNITY
     ▼                                    ▼
Strategy (9,600) ──MERGED_INTO──▶ CanonicalRule (7,835) ──ADDRESSES_ERROR──▶ ErrorPattern
     │
     └── PlaybookEntry (8,910)

Community (781) ◀── IN_COMMUNITY
ProblemSummary (1,795) ◀── SUMMARIZES ── Trajectory
```

**Totals**: ~45K nodes, ~74K edges. All embeddings: `text-embedding-3-large` (3072 dim).

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

## Project Structure

```
agent_memory/                  # Core library (pip package: agent-memory)
├── memory.py                  # AgentMemory main entry point
├── models.py                  # Dataclasses: Fragment, Strategy, CanonicalRule, etc.
├── neo4j_store.py             # Neo4j backend (schema, CRUD, graph export)
├── playbook.py                # PlaybookRetriever: 3-channel + PPR retrieval
├── retriever.py               # General graph retriever
├── strategy_extractor.py      # LLM-based strategy extraction from trajectories
├── query_rewriter.py          # LLM query rewriting for better retrieval
├── writer.py                  # RawTrajectory → graph nodes
├── embeddings.py              # OpenAI embedding wrapper
├── consolidator.py            # Merge similar fragments
├── community.py               # Community detection
├── loop_detector.py           # Repeated action pattern detection
├── reranker.py                # Result reranking
├── formatter.py               # Output formatting
├── entity_resolver.py         # Entity resolution
├── vault/                     # Obsidian markdown vault graph (wikilinks + tags)
└── evaluation/                # Evaluation framework
    ├── metrics.py             # ProblemResult, calculate_metrics()
    ├── analyzer.py            # compare_results(), ComparisonReport
    ├── swe_agent_tool.py      # QueryMemoryTool for function calling
    └── ...

configs/                       # SWE-agent YAML configs
├── swe_agent_control.yaml     # Control: standard SWE-agent
├── swe_agent_treatment.yaml   # Treatment: + memory (Claude)
├── glm47_control.yaml         # GLM-4.7 control
└── glm47_treatment.yaml       # GLM-4.7 + memory

scripts/                       # Runnable scripts
├── build_obsidian_graph.py    # Build graph vault from markdown (Obsidian-native)
├── query_obsidian_vault.py    # Search + --summary over graph vault
├── build_vault_graph.py       # Ingest markdown into Neo4j (optional)
├── build_context_graph.py     # Build Neo4j graph from training trajectories
├── extract_strategies.py      # LLM strategy extraction
├── deduplicate_strategies.py  # Cosine clustering → CanonicalRule
├── ingest_playbook.py         # Create PlaybookEntry nodes
├── reembed_all_nodes.py       # Re-embed all nodes (text-embedding-3-large)
├── extract_anti_patterns.py   # Extract anti-patterns from failures
├── run_real_swe_experiment.py # Real SWE-agent A/B runner
├── run_rewriter_experiment.py # Rewriter ablation experiment
├── run_online_learning_experiment.py
├── analyze_online_learning.py
└── ...

tools/query_memory/            # SWE-agent tool bundle (runs inside Docker)
├── config.yaml                # Function calling schema
├── bin/query_memory           # Bash wrapper (uses Python 3.11+)
├── bin/query_memory_impl.py   # Python implementation
├── install.sh                 # Installs neo4j driver in Docker
└── lib/agent_memory/          # Bundled library for Docker

experiments/ab_test/           # A/B experiment framework
tests/                         # pytest test suite
results/live_experiment/       # Experiment outputs
```

## Quick Start

```bash
# 1. Setup
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e '.[dev]'

# 2. Start Neo4j
docker run -d --name neo4j-contextgraph -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/contextgraph123 neo4j:5

# 3. Build context graph (~12 min for 1,795 trajectories)
python scripts/build_context_graph.py

# 4. Extract strategies + deduplicate + create playbook
python scripts/extract_strategies.py
python scripts/deduplicate_strategies.py
python scripts/ingest_playbook.py

# 5. Re-embed all nodes with text-embedding-3-large (3072 dim)
python scripts/reembed_all_nodes.py --batch-size 50

# 6. Run A/B experiment
python scripts/run_real_swe_experiment.py --group control
python scripts/run_real_swe_experiment.py --group treatment
```

## A/B Experiment

### Design
- **Training**: 1,795 trajectories from `nebius/SWE-agent-trajectories` (zero overlap with SWE-bench)
- **Test**: 200 problems from SWE-bench_Verified (seed=42)
- **Control**: Standard agent, no memory
- **Treatment**: Agent + `query_memory` tool (retrieves from context graph)
- **Metrics**: pass@k, token consumption, failure ratio

### Agents Supported
- **SWE-agent v1.1.0**: Via `query_memory` tool bundle (function calling)
- **OpenHands v1.3.0+**: Via `MemoryHooks` (conversation instructions injection)

### Models Tested
- Claude Sonnet (via ChatAnywhere proxy)
- GLM-4.7 (via Zhipu AI API)

## Infrastructure

| Component | Details |
|---|---|
| Neo4j | Docker `neo4j:5`, auth `neo4j/contextgraph123`, ports 7474/7687 |
| Embeddings | `text-embedding-3-large` (3072 dim) via ChatAnywhere proxy |
| Python | >=3.12, managed with `uv` |
| Docker | Required for SWE-agent and OpenHands |

## Documentation

- [Obsidian vault knowledge graph](docs/obsidian-vault.md) — build, search, and summarize from markdown vaults

## Resources

- [SWE-bench](https://www.swebench.com/)
- [SWE-agent](https://swe-agent.com/)
- [HippoRAG Paper](https://arxiv.org/abs/2405.14831)
