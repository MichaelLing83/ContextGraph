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

Two runs on the same problem:
1. **Treatment** (with memory): MCP `query_memory` tool available
2. **Control** (without memory): `--pure` flag, no MCP tools

## Results Comparison

| Metric | With Memory | Without Memory | Delta |
|--------|-------------|----------------|-------|
| **Duration** | 271.5s (4.5min) | 303.9s (5.1min) | +32.3s (+12%) |
| **Total events** | 159 | 205 | +46 (+29%) |
| **Text turns** | 28 | 41 | +13 (+46%) |
| **Tool calls** | 43 | 54 | +11 (+26%) |
| **Correct fix** | Yes | Yes | Same |

### Tool Call Breakdown

| Tool | With Memory | Without Memory |
|------|-------------|----------------|
| bash | 14 | 18 |
| read | 11 | 12 |
| todowrite | 6 | 7 |
| write | 5 | 8 |
| edit | 4 | 6 |
| grep | 2 | 2 |
| glob | 0 | 1 |
| query_memory (MCP) | 1 | 0 |

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

### 3. Efficiency gains are modest for simple bugs
For this well-hinted, single-line bug:
- 12% time savings
- 26% fewer tool calls
- The real value of memory may show on harder problems where strategy selection is less obvious

### 4. Environment friction dominated both runs
Python 3.14 incompatibility with sympy 1.1 was the primary obstacle. Both agents eventually worked around it with pure-Python simulations. In a proper SWE-bench Docker environment (Python 3.6-3.9), this wouldn't occur.

## Raw Data

- `with_memory.jsonl` - Full OpenCode JSON event stream (treatment)
- `without_memory.jsonl` - Full OpenCode JSON event stream (control)
