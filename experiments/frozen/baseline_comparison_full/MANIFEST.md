# Frozen Experiment: 5-Way Baseline Comparison (Full 500)

Frozen on: 2026-05-08
Experiment run: 2026-05-05 ~ 2026-05-07

## Experiment Summary

5-way baseline comparison on ALL 500 SWE-bench Verified problems with GPT-5.4 (via LiteLLM proxy), unlimited budget (cost_limit=0).

### Methods
1. **no_memory** — vanilla SWE-agent (control)
2. **expel** — flat rule list injected into system prompt
3. **faiss** — FAISS vector retrieval (SWE-Bench-CL style)
4. **agentkb** — TF-IDF + semantic hybrid (Agent-KB style)
5. **contextgraph** — Neo4j graph + 3-channel PPR retrieval (ours)
6. **contextgraph_clean** — same as contextgraph but with 4 garbage rules removed from graph

### Results (458 common completed instances)

| Method | Resolved | Rate |
|--------|----------|------|
| contextgraph_clean | 322* | 67.2%* |
| faiss | 306 | 66.8% |
| contextgraph | 304 | 66.4% |
| agentkb | 301 | 65.7% |
| expel | 296 | 64.6% |
| no_memory | 292 | 63.8% |

*Projected from 42-instance retest (+18 gained, 0 lost vs original contextgraph)

## Git State

### Local repo (this machine)
- **Commit**: `1c2f548` (branch: main)
- **Remote**: `origin/main` at `1c2f548`

### Remote server (ssh jie)
- **Commit**: `a221f83` (3 commits behind local main)
- **Uncommitted changes** on remote (working tree modifications used during experiment):

| File | SHA256 |
|------|--------|
| scripts/run_baseline_comparison.py | `c61a5326...` |
| tools/query_memory/bin/query_memory | `cfb1fd37...` |
| tools/query_memory/install.sh | `18922721...` |

### Remote-only files (not in git, copied to this frozen directory):

| File | SHA256 |
|------|--------|
| scripts/run_baseline_comparison_full.py | `e48d19b9...` |
| scripts/baselines/faiss_server.py | `ce214896...` |
| scripts/baselines/agentkb_server.py | `84b88b68...` |

## Tool Versions (remote server)

| Tool | Version |
|------|---------|
| Python | 3.12.3 |
| SWE-agent | 1.1.0 (commit `0f3acafa`) |
| SWE-ReX | 1.4.0 |
| swebench | 4.1.0 |
| Neo4j | 5.26.21 |
| Docker | 29.4.0 |
| neo4j (Python) | 6.1.0 |
| openai | 2.24.0 |
| litellm | 1.81.15 |
| numpy | 2.4.2 |
| fastapi | 0.133.0 |
| uvicorn | 0.42.0 |
| typer | 0.24.1 |

## Neo4j Instances

| Container | Port | Volume | Purpose |
|-----------|------|--------|---------|
| neo4j-contextgraph | 7687 | neo4j-contextgraph-data | Baseline graph (READ-ONLY) |
| neo4j-contextgraph-clean | 7691 | neo4j-contextgraph-clean | Baseline minus 4 garbage rules |

### Removed rules (Strategy D)
- `rule_c866de1ede28` — "When encountering multiple error types during a feature fix, locate and examine the core implementation function (e.g., dvc/repo/move.py)..." (mc=1, hit_rate=56%)
- `rule_18a719265b09` — DVC CLI path reference (mc=1)
- `rule_17f494ed7f37` — DVC command directory reference (mc=1)
- `rule_a6bf0784a68b` — DVC remote storage reference (mc=1)

## Directory Structure

```
experiments/frozen/baseline_comparison_full/
├── MANIFEST.md                          # This file
├── run_baseline_comparison_full.py      # Main experiment runner (remote-only)
├── analysis/
│   ├── mcnemar_v2.py                    # McNemar paired statistical test
│   ├── analyze_cg_deep.py              # Deep root cause analysis (CG-HURT)
│   ├── analyze_cg_losses2.py           # Loss analysis vs faiss
│   ├── analyze_rule_frequency.py       # Rule frequency across trajectories
│   ├── cg_hurt_instances.py            # Export CG-HURT instance set
│   ├── compare_clean.py               # Compare clean vs original CG
│   ├── extract_and_eval_clean.py       # Extract preds + run SWE-bench eval
│   └── lookup_rule.py                  # Look up rules in Neo4j
├── infra/
│   ├── prebuild_overlay.py             # Pre-build swerex overlay images
│   ├── setup_clean_neo4j.sh            # Create clean Neo4j from baseline
│   ├── clean_rules.py                  # Remove garbage rules from graph
│   └── run_cg_retest.py               # Re-run CG on differential instances
└── data/
    ├── verified_500.json               # 500 test instance IDs
    ├── failed_105.json                 # 105 Docker-failed instance IDs
    ├── cg_retest_instances.json        # 42 differential instance IDs
    ├── gpt-5.4-no_memory.no_memory_v2.json
    ├── gpt-5.4-expel.expel_v2.json
    ├── gpt-5.4-faiss.faiss_v2.json
    ├── gpt-5.4-agentkb.agentkb_v2.json
    ├── gpt-5.4-contextgraph.contextgraph_v2.json
    └── output.contextgraph_clean.json
```

## How to Reproduce

### 1. Start infrastructure
```bash
docker compose up -d                    # Neo4j + LiteLLM proxy
# Start baseline servers:
uv run python scripts/baselines/faiss_server.py serve --port 8002
uv run python scripts/baselines/agentkb_server.py --port 8001
NEO4J_URI=bolt://localhost:7687 uv run python scripts/baselines/contextgraph_server.py --port 8003
```

### 2. Run experiment
```bash
uv run python scripts/run_baseline_comparison_full.py run no_memory
uv run python scripts/run_baseline_comparison_full.py run expel
uv run python scripts/run_baseline_comparison_full.py run faiss
uv run python scripts/run_baseline_comparison_full.py run agentkb
uv run python scripts/run_baseline_comparison_full.py run contextgraph
```

### 3. Verify with SWE-bench
```bash
uv run python scripts/run_baseline_comparison_full.py verify
```

### 4. Run Strategy D (clean graph)
```bash
bash infra/setup_clean_neo4j.sh         # Copy baseline Neo4j to port 7691
python infra/clean_rules.py              # Remove garbage rules
NEO4J_URI=bolt://localhost:7691 uv run python scripts/baselines/contextgraph_server.py --port 8004
python infra/run_cg_retest.py            # Re-run on 42 differential instances
python analysis/compare_clean.py         # Compare results
```

### 5. Statistical analysis
```bash
python analysis/mcnemar_v2.py            # McNemar paired test
python analysis/analyze_rule_frequency.py # Rule distribution analysis
```

## Trajectories (not committed)

Raw .traj files are on the remote server at:
```
/home/jie/codes/ContextGraph/results/baseline_comparison_full/{method}/output/{instance_id}/{instance_id}.traj
```
Total ~2,500 trajectory files (~42GB). Not practical to commit; access via `ssh jie`.
