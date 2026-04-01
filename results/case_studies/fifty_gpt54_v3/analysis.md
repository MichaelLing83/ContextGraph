# 50-Problem GPT-5.4 A/B Experiment v3 (Final)

## Summary

**ContextGraph memory provides +6pp resolve rate on SWE-bench Verified (50% vs 44%, n=50).**

This is the clean experiment after fixing two confounders discovered in v1/v2:
1. OpenCode early exit bug (GPT-5.4 exits after 1 step when CLAUDE.md missing)
2. MCP config placeholder not replaced (v2 "original" group had no memory)

## Setup

| Parameter | Value |
|-----------|-------|
| Model | GPT-5.4 via OpenRouter (`openrouter/openai/gpt-5.4`) |
| Agent | OpenCode v1.2.15 (`opencode run --format json`) |
| Memory | ContextGraph original graph (Neo4j port 7687) |
| Graph size | 45,115 nodes (8,910 PlaybookEntry, 7,835 CanonicalRule) |
| Embedding | text-embedding-3-large (3072 dim) via ChatAnywhere |
| Retrieval | Cosine + BM25 + PPR with RRF merge, top_k=10, MMR diversity=0.3 |
| Problems | 50 random from SWE-bench Verified (seed=2026) |
| Problem source | `results/case_studies/fifty_gpt54/problems.json` |
| Repos | 9 (astropy, django, matplotlib, psf/requests, xarray, pytest, sklearn, sphinx, sympy) |
| GT lines | 1-232, median=8, mean=23 |
| Timeout | 900s per run |
| Concurrency | max 10 |
| CLAUDE.md | Placed in each work dir (early exit fix) |
| Date | 2026-04-01 |

### Groups

| Group | Config | MCP | Description |
|-------|--------|-----|-------------|
| original | `configs/opencode_gpt54_original.json` | contextgraph-memory (port 7687) | Baseline graph from 1,795 trajectories |
| nomem | `configs/opencode_gpt54_nomem.json` | none (`--pure` not used, but no MCP defined) | No memory access |

### Prompt (identical for both groups except final line)

```
You are a coding agent tasked with fixing a bug in a Python repository.

## Bug Report ({problem_id})
{problem_text}

## Instructions
1. Reproduce the bug to confirm it.
2. Find and fix the root cause in the source code.
3. Verify your fix works.

# Only for original group:
IMPORTANT: You MUST call the query_memory MCP tool at least once before making any code changes.
```

## Results

### SWE-bench Verified

| Metric | Original Memory | No Memory |
|--------|----------------|-----------|
| Submitted | 50 | 50 |
| Completed (ran tests) | 50 | 50 |
| **Resolved** | **25 (50%)** | **22 (44%)** |
| Empty patch | 0 | 0 |
| Errors | 0 | 0 |
| Timeouts | 0 | 0 |
| Early exits (<10s) | 0 | 0 |
| query_memory calls | 50/50 | N/A |

### Paired Comparison (McNemar test)

|  | NoMem resolved | NoMem unresolved |
|--|----------------|------------------|
| **Memory resolved** | 19 | 6 |
| **Memory unresolved** | 3 | 22 |

- McNemar chi2 = 1.00 (p > 0.05, not statistically significant at n=50)
- Memory independently solved **6** problems nomem could not
- NoMem independently solved **3** problems memory could not
- Ratio: 2:1 in favor of memory

### Only Memory Resolved (6)
- `astropy__astropy-14539` — FITS diff VLA comparison
- `django__django-14725` — Formset disallow new object creation
- `django__django-15375` — Aggregates exclude empty queryset
- `django__django-15382` — Exists-subquery filter with empty queryset
- `django__django-16631` — SECRET_KEY_FALLBACKS not used for sessions
- `matplotlib__matplotlib-20488` — Matplotlib bug

### Only NoMem Resolved (3)
- `django__django-11532` — Email non-ASCII domain crash
- `django__django-15732` — Cannot drop unique_together on single field
- `scikit-learn__scikit-learn-25973` — SequentialFeatureSelector splits issue

## Version History

| Version | Issue | Original | NoMem | Delta | Valid? |
|---------|-------|----------|-------|-------|--------|
| v1 | Early exit bug + broken MCP | 24% | 14% | +10pp | No — early exit confound |
| v2 | Early exit fixed, MCP still broken | 48% | 56% | -8pp | No — both groups = nomem |
| v3 | All fixed | **50%** | **44%** | **+6pp** | **Yes** |

### v1 → v2: Early Exit Bug

60% of v1 runs terminated after 1 step (<10s) because OpenCode/GPT-5.4 tried to read
`/Users/.../.claude/CLAUDE.md` from the work directory and failed. The MCP query_memory
call gave the memory group an extra interaction turn, reducing its early exit rate
(40% vs 78%). Fix: place a minimal `CLAUDE.md` in each work dir.

### v2 → v3: MCP Config Bug

The config file `configs/opencode_gpt54_original.json` had `"REPO_ROOT"` as a literal
string in the `--directory` argument, so the MCP server never started. The v2 "original"
group was identical to "nomem" (0/50 query_memory calls). Fix: replace placeholder with
actual absolute path.

## Reproduction

### Prerequisites

1. Neo4j running with ContextGraph data (port 7687)
2. OpenCode v1.2.15 installed
3. OpenRouter API key with GPT-5.4 access
4. Python environment with `agent-memory` installed (`uv pip install -e .`)

### Environment Variables

```bash
export NEO4J_PASSWORD=<neo4j password>
export OPENAI_API_KEY=<chatanywhere key for embeddings>
export OPENAI_API_BASE=https://api.chatanywhere.org
export OPENROUTER_API_KEY=<openrouter key>
export FIFTY_TEST_DIR=/tmp/swe-bench-test/fifty_v3
```

### Run

```bash
# Run 50-problem A/B test
uv run python scripts/run_fifty_gpt54.py

# Extract diffs (after completion)
python3 -c "
import json, subprocess
from pathlib import Path
BASE = Path('$FIFTY_TEST_DIR')
problems = json.load(open('results/case_studies/fifty_gpt54/problems.json'))
for group in ['original', 'nomem']:
    preds = []
    for p in problems:
        diff = subprocess.run(['git', 'diff', 'HEAD'], cwd=str(BASE/p['id']/group),
                              capture_output=True, text=True).stdout.strip()
        preds.append({'instance_id': p['id'], 'model_name_or_path': f'gpt54_{group}',
                       'model_patch': diff + '\n' if diff else ''})
    json.dump(preds, open(f'predictions_{group}.json', 'w'), indent=2)
"

# Run SWE-bench verification
for group in original nomem; do
  uv run python -m swebench.harness.run_evaluation \
    --dataset_name princeton-nlp/SWE-bench_Verified \
    --split test \
    --predictions_path predictions_${group}.json \
    --max_workers 8 \
    --run_id fiftyv3_${group}
done
```

### Key Files

| File | Description |
|------|-------------|
| `scripts/run_fifty_gpt54.py` | Runner script (clones repos, runs OpenCode, saves results) |
| `configs/opencode_gpt54_original.json` | OpenCode config with MCP memory server |
| `configs/opencode_gpt54_nomem.json` | OpenCode config without MCP |
| `tools/mcp_server/server.py` | MCP server wrapping ContextGraph query |
| `results/case_studies/fifty_gpt54/problems.json` | 50 problem definitions (instance IDs, base commits) |
| `results/case_studies/fifty_gpt54_v3/predictions_{original,nomem}.json` | Agent-generated patches |
| `results/case_studies/fifty_gpt54_v3/gpt54v3_{original,nomem}.*json` | SWE-bench evaluation reports |
| `results/case_studies/fifty_gpt54_v3/run_results.json` | OpenCode run status (timeouts, return codes) |

### Software Versions

| Component | Version |
|-----------|---------|
| OpenCode | 1.2.15 |
| GPT-5.4 | via OpenRouter (`openrouter/openai/gpt-5.4`) |
| Neo4j | 5 (Docker) |
| swebench | 4.1.0+ |
| agent-memory | 0.1.0 (editable install) |
| MCP SDK | 1.26.0 |
| Python | 3.14 (runner), Docker containers per SWE-bench spec (per problem) |
| uv | latest |
