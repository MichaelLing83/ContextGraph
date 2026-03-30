# Case Study: sympy__sympy-13647 (Matrix.col_insert bug)

## Problem

**Instance ID**: `sympy__sympy-13647`
**Repo**: sympy/sympy (version 1.1)
**Base commit**: `67e3c956083d0128a621f65ee86a7dacd4f9f19f`

`Matrix.col_insert()` incorrectly shifts columns after insertion. When inserting 2 columns of 2s at position 3 into a 6x6 identity matrix, the bottom-right 3x3 identity block shifts to the top-right instead of staying in place.

**Ground truth fix**: In `sympy/matrices/common.py`, line 89 of `_eval_col_insert`:
```diff
-            return self[i, j - pos - other.cols]
+            return self[i, j - other.cols]
```

## Experiment Setup

- **Agent**: OpenCode v1.2.15 via `opencode run` (non-interactive)
- **Model**: OpenRouter / anthropic/claude-sonnet-4
- **MCP Server**: ContextGraph memory (Neo4j, 45,115 nodes, text-embedding-3-large)
- **Date**: 2026-03-30
- **Environment**: macOS, Python 3.14 (sympy 1.1 incompatible with Python 3.14)

5 runs per group (1 original + 4 replications, all concurrent):
1. **Treatment** (with memory): MCP `query_memory` tool available
2. **Control** (without memory): `--pure` flag, no MCP tools

## Results: Individual Runs (n=5 per group)

### With Memory (Treatment)

| Run | Duration (s) | Events | Text turns | Tool calls | MCP calls | Correct fix |
|-----|-------------|--------|------------|------------|-----------|-------------|
| orig | 271.5 | 159 | 28 | 43 | 1 | Yes |
| run-1 | 208.8 | 124 | 23 | 33 | 1 | Yes |
| run-2 | 166.1 | 90 | 16 | 24 | 1 | Yes |
| run-3 | 213.4 | 124 | 23 | 33 | 1 | Yes |
| run-4 | 238.1 | 121 | 23 | 32 | 1 | Yes |

### Without Memory (Control)

| Run | Duration (s) | Events | Text turns | Tool calls | MCP calls | Correct fix |
|-----|-------------|--------|------------|------------|-----------|-------------|
| orig | 303.9 | 205 | 41 | 54 | 0 | Yes |
| run-1 | 142.3 | 76 | 14 | 20 | 0 | Yes |
| run-2 | 186.3 | 112 | 23 | 29 | 0 | Yes |
| run-3 | 123.2 | 83 | 18 | 21 | 0 | Yes |
| run-4 | 262.7 | 148 | 26 | 40 | 0 | Yes |

## Aggregate Statistics

| Metric | With Memory (mean +/- std) | Without Memory (mean +/- std) |
|--------|---------------------------|-------------------------------|
| **Duration (s)** | 219.6 +/- 38.9 [166-272] | 203.7 +/- 77.6 [123-304] |
| **Total events** | 123.6 +/- 24.4 [90-159] | 124.8 +/- 53.1 [76-205] |
| **Text turns** | 22.6 +/- 4.3 [16-28] | 24.4 +/- 10.4 [14-41] |
| **Tool calls** | 33.0 +/- 6.7 [24-43] | 32.8 +/- 14.3 [20-54] |
| **Correct fix** | 5/5 (100%) | 5/5 (100%) |

**Key finding**: On means, the two groups are essentially **indistinguishable** (duration delta <8%, tool calls delta <1%). The control group has **higher variance** (std 77.6s vs 38.9s for duration), suggesting memory may provide more **consistent** behavior, but the sample is too small to draw statistical conclusions.

### First-run vs Replications

The original single-run comparison (271.5s vs 303.9s, 12% faster with memory) was **not representative** of the broader distribution. The control group's original run was an outlier (spent 8 steps on Python 3.14 compat hacks). Replication runs show the control sometimes finishes faster (run-3: 123.2s).

## Trajectory Analysis

### With Memory (Treatment)

**Phase 1 - Memory Query (Step 1)**
- Called `query_memory` with error description and task context
- Received playbook rules including:
  - "Locate and examine the core implementation function before attempting to resolve each error individually"
  - "Search for the specific file or pattern"
- These strategies guided a source-code-first approach

**Phase 2 - Bug Reproduction Attempt (Steps 2-8)**
- Wrote `reproduce_bug.py`, encountered Python 3.14 incompatibility
- **Immediately pivoted** to source code analysis instead of fighting compatibility

**Phase 3 - Source Code Analysis + Fix (Steps 9-14)**
- Read `sympy/matrices/common.py`
- Located `_eval_col_insert` method, identified the off-by-`pos` bug on line 89
- Applied single-line fix

**Phase 4 - Verification (Steps 15-35)**
- Built 3 pure-Python simulation scripts (no sympy import needed)
- Verified mathematical correctness, regression compatibility, and exact bug scenario

### Without Memory (Control)

**Phase 1 - Bug Reproduction Attempts (Steps 1-20)**
- Tried to run sympy directly, hit `ModuleNotFoundError: mpmath`
- 3 `pip install` attempts (including `--break-system-packages`)
- Hit Python 3.14 incompatibility (`collections.Mapping` moved to `collections.abc`)
- **5 edit operations** to patch sympy's imports for Python 3.14 compatibility
- Total: **8 wasted steps** on environment compatibility before giving up

**Phase 2 - Source Code Analysis + Fix (Steps 21-30)**
- Same correct diagnosis and fix as treatment group

**Phase 3 - Verification (Steps 31-54)**
- More extensive verification scripts (8 write operations vs 5)
- Similar pure-Python simulation approach

## Key Observations

### 1. Memory guided faster strategy selection
The treatment agent's first action was querying memory, which returned strategies emphasizing "locate the core implementation function first." This aligned with bypassing the broken runtime environment and going straight to source analysis.

The control agent spent 8 steps (3 pip installs + 5 compat edits) trying to make sympy run on Python 3.14 before reaching the same conclusion.

### 2. Both agents found the correct fix
The bug was straightforward enough that both agents eventually identified and fixed it correctly. The `j - pos - other.cols` → `j - other.cols` fix matches the SWE-bench ground truth.

### 3. No significant efficiency gains for simple bugs
With 5 runs per group, the initial 12%/26% advantage **did not replicate**:
- Duration: 219.6s vs 203.7s (memory is actually 8% *slower* on average)
- Tool calls: 33.0 vs 32.8 (effectively identical)
- **But** memory group has lower variance (std 38.9 vs 77.6), suggesting more consistent behavior
- The real value of memory may show on harder problems where strategy selection is less obvious

### 4. Environment friction dominated both runs
Python 3.14 incompatibility with sympy 1.1 was the primary obstacle. Both agents eventually worked around it with pure-Python simulations. In a proper SWE-bench Docker environment (Python 3.6-3.9), this wouldn't occur.

## Raw Data

- `with_memory.jsonl` - Original run, treatment (with memory)
- `without_memory.jsonl` - Original run, control (without memory)
- `run{1-4}_with_memory.jsonl` - Replication runs, treatment
- `run{1-4}_without_memory.jsonl` - Replication runs, control
