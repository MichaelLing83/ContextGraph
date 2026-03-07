# Experiment Progress

## Context Graph Status (2026-03-06)

### Neo4j Graph
- **Container**: `neo4j-contextgraph` with named volume `neo4j-contextgraph-data`
- **Nodes**: 45,115 total
  - Fragment: 13,813 | Strategy: 9,600 | PlaybookEntry: 8,910 | CanonicalRule: 7,835
  - Trajectory: 1,795 | ProblemSummary: 1,795 | Community: 781 | ErrorPattern: 586
- **Relationships**: 73,975 total
- **Embeddings**: ALL nodes re-embedded with `text-embedding-3-large` (3072 dim) via ChatAnywhere
- **Vector indexes**: 8 indexes, all 3072 dim, cosine similarity

### Graph Build Pipeline
1. `build_context_graph.py` → Trajectory, Fragment, ErrorPattern nodes + HAS_FRAGMENT, CAUSED_ERROR edges
2. `extract_strategies.py` → Strategy nodes + DERIVED_FROM edges (LLM extraction)
3. `deduplicate_strategies.py` → CanonicalRule nodes + MERGED_INTO, ADDRESSES_ERROR edges (cosine clustering)
4. `ingest_playbook.py` → PlaybookEntry nodes
5. Community detection → Community nodes + IN_COMMUNITY edges
6. ProblemSummary → SUMMARIZES edges
7. `reembed_all_nodes.py` → All embeddings updated to 3072 dim

### Embedding Details
- **Model**: text-embedding-3-large (3072 dimensions)
- **Provider**: ChatAnywhere (`https://api.chatanywhere.org/v1`)
- **Node text fields**: Fragment→description, Trajectory→summary, Strategy→rule_text, CanonicalRule→rule_text, PlaybookEntry→text, Community→summary, ErrorPattern→error_type+error_keywords, ProblemSummary→summary_text
- **Note**: Fragment uses `description` field (NOT `content` — that property doesn't exist)

## SWE-agent Experiments

### Claude Sonnet (ChatAnywhere)
- Control + Treatment configs in `configs/swe_agent_control.yaml` / `swe_agent_treatment.yaml`
- Previous runs completed (see results/)

### GLM-4.7 (Zhipu AI) — Pilot
- Configs: `configs/glm47_control.yaml` / `configs/glm47_treatment.yaml`
- Treatment 3-instance pilot: All 3 completed with `submitted` status
- **Key fixes applied**:
  - `query_memory` bash wrapper forces Python 3.11+ (avoids testbed Python 3.6 SyntaxError)
  - Absolute bundle path in YAML config (SWE-agent resolves relative from its own install dir)
  - No `exec` in bash wrapper (kills pexpect session)
  - install.sh does NOT export PYTHONPATH (avoids polluting testbed env)
  - Bundled agent_memory synced to latest version

## Key Debugging Lessons (SWE-agent Tool Bundle)
1. SWE-bench Docker images have `testbed` conda env (can be Python 3.6) activated via PATH
2. `#!/usr/bin/env python3` in tool scripts resolves to testbed's Python → SyntaxError on neo4j
3. SWE-agent's `_convert_path_to_abspath()` resolves relative bundle paths from SWE-agent install dir
4. `exec` in bash wrapper replaces shell process → pexpect EOF when Python exits
5. PYTHONPATH exported from install.sh pollutes testbed env → SyntaxError from any Python 3.6 import

## TODO
- Run full GLM-4.7 A/B experiment (200 problems × 3 attempts)
- Continue with plan: Playbook dedup + HippoRAG PPR integration (plan file exists)
- OpenHands experiment
