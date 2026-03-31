# 50-Problem GPT-5.4 A/B Experiment Analysis

## Setup

- **Model**: GPT-5.4 via OpenRouter (`openrouter/openai/gpt-5.4`)
- **Agent**: OpenCode v1.2.15, `opencode run --format json`
- **Memory**: ContextGraph original (Neo4j port 7687, 8,910 PlaybookEntry, 45,115 total nodes)
- **Problems**: 50 random from SWE-bench Verified (seed=2026, 9 repos, GT 1-232 lines, median 8)
- **Groups**: original (with memory MCP) vs nomem (--pure)
- **Timeout**: 900s per run
- **Date**: 2026-03-31
- **Tag**: `v0.3.0-fifty-experiment`

## SWE-bench Verified Results

| Metric | Original Memory | No Memory |
|--------|----------------|-----------|
| Submitted | 50 | 50 |
| Completed (ran tests) | 24 | 10 |
| **Resolved** | **12 (24%)** | **7 (14%)** |
| Empty patch | 26 | 40 |
| Timeouts | 0 | 1 |

### Paired Comparison (McNemar)

|  | NoMem resolved | NoMem unresolved |
|--|----------------|------------------|
| **Memory resolved** | 2 | 10 |
| **Memory unresolved** | 5 | 33 |

- McNemar chi2 = 1.67 (p > 0.05, not significant at n=50)
- Memory independently solved 10 problems nomem could not
- NoMem independently solved 5 problems memory could not

## Critical Finding: OpenCode Early Exit Bug

### The Problem

**60% of all runs (60/100) terminated after a single step (<10s, 1-4 tool calls).**

| | Early exit (<10s) | Normal (>=10s) |
|--|-------------------|----------------|
| **original (memory)** | 20/50 (40%) | 30/50 (60%) |
| **nomem** | 39/50 (78%) | 11/50 (22%) |

### Root Cause

97% (58/60) of early exits share this pattern:

1. First tool call: `read /Users/zihanwu/.claude/CLAUDE.md` → **error** (file outside work dir)
2. Agent emits 1-4 tool calls (grep, bash, glob)
3. Agent outputs text: "I'm checking X before editing..."
4. **Agent stops** — only 1 step executed (mean 1.1 steps vs 11.4 for normal runs)

GPT-5.4 under `opencode run` fails to recover from the initial `read` error and exits prematurely. The LLM generates a planning response but OpenCode terminates the session after a single agentic step.

### Why Memory Reduces Early Exits

The `query_memory` MCP call creates an **additional interaction turn**. After receiving the memory response, the agent enters a second step, which is enough to break out of the 1-step exit pattern. This is an **activation effect** — the memory content itself is less important than the fact that the MCP call keeps the agent running.

Evidence:
- Memory group early exit rate: 40% (20/50)
- NoMem group early exit rate: 78% (39/50)
- The 10 "memory-only" resolved problems all had normal run durations (67-105s)
- The 5 "nomem-only" resolved problems all had normal run durations (66-90s)
- When both groups run normally, they have similar resolve rates

### Implication for Results

The 24% vs 14% headline result is **partially confounded** by the early exit bug. Memory's true effect on resolve rate (controlling for early exits) is smaller than the raw numbers suggest. The memory group's advantage comes in significant part from avoiding premature termination.

## Resolved Problems Detail

### Only memory resolved (10)
- astropy__astropy-14539
- django__django-13933
- django__django-14725
- django__django-15382
- django__django-16595
- django__django-17087
- pydata__xarray-3677
- pydata__xarray-6461
- sphinx-doc__sphinx-8721
- sympy__sympy-15017

### Only nomem resolved (5)
- django__django-12155
- django__django-14373
- django__django-15375
- scikit-learn__scikit-learn-25102
- scikit-learn__scikit-learn-25973

### Both resolved (2)
- psf__requests-1142
- sympy__sympy-24066

## Mitigation

Place a minimal `CLAUDE.md` in each work directory before running OpenCode to prevent the initial read error that triggers early exit.
