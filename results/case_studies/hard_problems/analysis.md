# Hard Problems A/B Test: ContextGraph Memory vs No Memory

## Experiment Setup

- **Agent**: OpenCode v1.2.15 via `opencode run` (non-interactive)
- **Model**: OpenRouter / anthropic/claude-sonnet-4
- **MCP Server**: ContextGraph memory (Neo4j, 45,115 nodes, text-embedding-3-large)
- **Date**: 2026-03-30
- **Timeout**: 600s per run
- **Runs**: 5 per group per problem (50 total)
- **Concurrency**: max 10 simultaneous runs

### Problems Selected (by GT patch size)

| # | Problem ID | GT Lines | Description |
|---|-----------|----------|-------------|
| 1 | django__django-13513 | 44 | Debug error view ignores `__suppress_context__` (PEP 415) |
| 2 | sympy__sympy-14531 | 38 | StrPrinter settings not respected by subexpressions |
| 3 | django__django-15561 | 35 | AlterField should be noop for choices on SQLite |
| 4 | scikit-learn__scikit-learn-10297 | 34 | RidgeClassifierCV store_cv_values parameter bug |
| 5 | matplotlib__matplotlib-24870 | 33 | Auto-detect bool arrays in contour() |

## Key Result: Completion Rate

| Problem | Memory (completed/5) | No Memory (completed/5) |
|---------|---------------------|------------------------|
| django__django-13513 | **5/5 (100%)** | **0/5 (0%)** - all timed out |
| sympy__sympy-14531 | **5/5 (100%)** | **5/5 (100%)** |
| django__django-15561 | **5/5 (100%)** | **5/5 (100%)** |
| scikit-learn__scikit-learn-10297 | **5/5 (100%)** | **3/5 (60%)** - 2 timed out |
| matplotlib__matplotlib-24870 | **4/5 (80%)** | **0/5 (0%)** - all timed out |
| **TOTAL** | **24/25 (96%)** | **13/25 (52%)** |

### Headline Finding

**Memory-augmented agents complete within timeout 96% of the time vs 52% for no-memory agents.** The difference is driven by 3 problems where no-memory agents frequently time out while memory-augmented agents finish reliably.

## Detailed Results

### django__django-13513 (GT: 44 lines)

| Group | Completed | Timed Out | Mean Duration |
|-------|-----------|-----------|---------------|
| Memory | 5/5 | 0 | 418s |
| No Memory | 0/5 | 5 | N/A (all >600s) |

**Strongest result.** Memory group completed all 5 runs; no-memory group timed out every time. The memory module likely provided relevant Django debugging strategies that prevented the agent from going down unproductive paths.

### sympy__sympy-14531 (GT: 38 lines)

| Group | Completed | Timed Out | Mean Duration |
|-------|-----------|-----------|---------------|
| Memory | 5/5 | 0 | 290s |
| No Memory | 5/5 | 0 | 281s |

**No significant difference.** Both groups completed reliably with similar durations. The StrPrinter bug was apparently straightforward enough for both approaches.

### django__django-15561 (GT: 35 lines)

| Group | Completed | Timed Out | Mean Duration |
|-------|-----------|-----------|---------------|
| Memory | 5/5 | 0 | 290s |
| No Memory | 5/5 | 0 | 138s |

**No-memory was faster.** Both completed reliably, but no-memory agents were 2x faster. The hint in the problem statement ("add choices to non_database_attrs") made this problem easy to solve directly. Memory overhead (MCP call + processing playbook) slowed the memory group without adding value.

### scikit-learn__scikit-learn-10297 (GT: 34 lines)

| Group | Completed | Timed Out | Mean Duration |
|-------|-----------|-----------|---------------|
| Memory | 5/5 | 0 | 290s |
| No Memory | 3/5 | 2 | 486s |

**Memory group more reliable.** Memory group completed 100% vs 60% for no-memory. Among completed runs, memory was also faster (290s vs 486s).

### matplotlib__matplotlib-24870 (GT: 33 lines)

| Group | Completed | Timed Out | Mean Duration |
|-------|-----------|-----------|---------------|
| Memory | 4/5 | 1 | 479s |
| No Memory | 0/5 | 5 | N/A (all >600s) |

**Strong memory advantage.** Memory group completed 80% vs 0% for no-memory. The matplotlib codebase is large and complex; memory strategies likely helped the agent navigate more efficiently.

## Analysis

### Why memory helps on hard problems but not easy ones

1. **Hard problems have more search space**: On easy single-line bugs (sympy-13647), both groups quickly find the fix. On harder problems involving multiple files, unfamiliar codebases, or non-obvious fix locations, memory strategies reduce wasted exploration.

2. **Timeout prevention**: The primary benefit of memory is not speed but **preventing the agent from getting stuck**. On 3 of 5 hard problems, no-memory agents frequently failed to complete within 10 minutes while memory agents almost always finished.

3. **Diminishing returns on well-hinted problems**: When the problem description includes a strong hint (django-15561), memory adds overhead without value. The benefit is greatest when the agent must discover the approach independently.

### Caveats

- **No correctness evaluation**: We measured completion, not whether the fix was correct. A completed run may have applied a wrong fix.
- **Python 3.14 environment**: These runs happened on Python 3.14, which is incompatible with some older repo versions. In a proper SWE-bench Docker environment, results would differ.
- **Small sample size**: 5 runs per group per problem. Statistical significance tests would require more runs.
- **Concurrent execution**: Runs competed for API rate limits, which may have affected timing consistency.

## Raw Data

Each problem directory contains:
- `result_mem_{1-5}.jsonl` - Memory group run trajectories
- `result_nomem_{1-5}.jsonl` - No-memory group run trajectories
- `run_results.json` - Run status index (completed vs timed out)
