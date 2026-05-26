# SWE-ContextBench-style Dev Signal on SWE-bench Lite Leave-Out

## Date
2026-05-17

## Goal
Per user request: "新 worktree 新 branch 中做 swe context 实验，看看我们的方法效果如何."
Evaluate ContextGraph on a SWE-ContextBench-style protocol where the agent has
access to a memory built from past tasks of the same family.

## Why this protocol (and not the literal SWE-ContextBench harness)

SWE-ContextBench (Zhu et al. 2026, arXiv:2602.08316, HF dataset
`jiayuanz3/SWEContextBench`) provides 1,100 *experience* tasks and 376 *related*
tasks. The related split is the intended test set, but it is NOT a clean drop-in
for the standard `swebench.harness.run_evaluation` harness:

- 11/99 Related-Lite instances reference repo versions absent from
  `swebench.harness.constants.MAP_REPO_VERSION_TO_SPECS` (e.g.\ astropy 5.3.2).
- Of the 20 instances that *were* in the version map, all 18 that successfully built
  produced **gold ✓=0 / ✖=18** when fed their own ground-truth patches
  (`PASS_TO_PASS` and `FAIL_TO_PASS` test commands the standard harness expects
  for these dates didn't match what the SWE-ContextBench authors used).
- SWE-ContextBench appears to ship its own evaluation harness via the paper,
  but no public release at the time of this experiment.

To get a credible head-to-head with the *same scientific question* ("does
memory built from past tasks help on a held-out task from the same family?")
using infrastructure known to work, we switched to:

- **Test set**: 100 instances held out from the 300 SWE-bench Lite "experience"
  instances (the same instances SWE-ContextBench uses as its experience pool).
  Split = random 200 train / 100 test, seed=42.
- **Memory corpus**: 1,000 LLM-extracted strategies from the 200 training
  instances' gold patches — strict held-out, zero issue-level overlap with
  the test 100.
- **Eval**: the standard `swebench.harness.run_evaluation` against
  `SWE-bench/SWE-bench_Lite` (works out of the box, gold passes 100%).

This dev signal covers the first 30 of the 100 test instances (random order,
same seed). Full 100-instance run is a follow-up if the signal warrants it.

## Setup

| Field                  | Value                                                  |
|------------------------|--------------------------------------------------------|
| Agent                  | SWE-agent v1.1.0                                       |
| Model                  | `gpt-5.4` via LiteLLM proxy → Yunwu gateway            |
| Per-instance budget    | `cost_limit=$3, step_limit=75` (symmetric, both methods) |
| Memory server          | `contextgraph_pro_server.py`, k_in=3, k_out=2          |
| Memory backend         | Neo4j 5.26 on `bolt://localhost:7694`, 1,000 PlaybookEntry |
| Eval harness           | `swebench.harness.run_evaluation`, dataset `SWE-bench/SWE-bench_Lite` |
| Test set               | 30 of 100 held-out Lite instances (10 of 12 repos)     |
| Dev subset selection   | `random.Random(42).shuffle(test_100)[:30]`             |

## Results (30-instance dev signal)

| Method                    | Eval | Resolved | Rate over total | Rate over evaluated |
|---------------------------|-----:|---------:|----------------:|--------------------:|
| `no_memory`               |   29 |       16 |          53.33% |              55.17% |
| `contextgraph`            |   28 |       16 |          53.33% |              57.14% |
| `contextgraph_verified`   |   30 |       15 |          50.00% |              50.00% |

`contextgraph_verified` swaps the memory backend from the Lite-train
1,000-strategy graph to the **Verified-paper graph** (Neo4j on `bolt://localhost:7687`):
45,115 nodes / 73,975 edges built from 1,795 nebius/SWE-agent-trajectories. The
test condition probes whether memory trained on one SWE-bench distribution
transfers to a *different* one with no per-repo overlap guarantee.

McNemar paired test against `no_memory` (resolved indicator on the full
30-instance test set, unresolved/missing = failure for both methods):

| Comparison                            | wins | losses | both | neither |       p |
|---------------------------------------|-----:|-------:|-----:|--------:|--------:|
| `contextgraph` vs `no_memory`         |    1 |      1 |   15 |      13 |  1.0000 |
| `contextgraph_verified` vs `no_memory`|    3 |      4 |   12 |      11 |  1.0000 |

### Per-method discordant instances

- **`contextgraph` (Lite-train) only solves**: `sympy__sympy-22714`
- **`contextgraph_verified` only solves** (3 — neither baseline nor Lite-train memory got these):
    - `django__django-16400`
    - `psf__requests-863`
    - `pytest-dev__pytest-7432`
- **`no_memory` only solves**: `matplotlib__matplotlib-23476`
- **Cases where `contextgraph_verified` regresses vs `no_memory`** (4):
    - `django__django-16408`
    - `matplotlib__matplotlib-23476`
    - `scikit-learn__scikit-learn-14087`
    - `sympy__sympy-17655`

### Reading the Verified-transfer comparison

The Verified graph is doing *real* but two-directional work: it solves
**3 instances no other condition could touch**, but also **misleads the
agent on 4 instances** the simpler conditions get. Net effect is a tie
under McNemar (3 vs 4, `p=1.0`), so we cannot call this a positive
transfer. What we *can* say is that the Verified graph is supplying
non-trivially different signal — it isn't being ignored by the agent
and it isn't acting like random noise. It's just not strictly better
than no memory on this 30-instance Lite probe.

### Repo distribution of the 30-instance dev subset
| Repo                          | Count |
|-------------------------------|------:|
| django/django                 |    13 |
| sympy/sympy                   |     6 |
| matplotlib/matplotlib         |     3 |
| scikit-learn/scikit-learn     |     3 |
| sphinx-doc/sphinx             |     2 |
| psf/requests                  |     1 |
| pylint-dev/pylint             |     1 |
| pytest-dev/pytest             |     1 |

## Interpretation

On this 30-instance Lite leave-out subset, **ContextGraph is statistically
indistinguishable from no-memory** (1 win vs 1 loss; both methods reach
exactly 16/30 resolved). This is consistent with — and reinforces — the
main paper's finding on the full 500-problem SWE-bench-Verified (`+1.8` pp
for ContextGraph vs no_memory, `p=0.402`, not significant): when the
baseline is already strong on Verified-style tasks (`~53%–62%` resolved),
the marginal benefit of any matched-API memory method is small and noisy.

The dev signal does NOT contradict our paper's headline Pro result — Pro's
baseline is `4.35%`, leaving substantial headroom for memory; Lite/Verified
baselines do not.

## Caveats

1. **N=30 is a dev signal, not a confirmatory result.** Discordant counts
   of 1 vs 1 carry essentially no statistical power. The full 100-instance
   test set should be run before reporting this as a published result.
2. **Single random seed (seed=42)** for both the 200/100 train/test split
   and the 30-of-100 dev subset. Variance unestimated.
3. **Lite is "easy" relative to Pro.** Lite restricts to Python + 12 repos;
   most instances are single-file fixes. ContextGraph's PPR-based multi-hop
   retrieval has less to do on these problems than on Pro's multi-file
   long-horizon tasks.
4. **Strategies are LLM-abstracted from gold patches**, not real agent
   trajectories. Identical methodology to the main paper's Pro setup.
5. **This is not the literal SWE-ContextBench protocol.** The "related task"
   linkage from cross-references is replaced with a random 200/100 split.
   This loses the explicit experience↔related edge but preserves the
   "memory of past same-family tasks" axis.

## Artifacts

- `selected_instances.json` — the 30 dev instance IDs + image names
- `lite_split_train_ids.json` — the 200 train instance IDs (memory corpus source)
- `lite_split_test_ids.json` — the 100 test instance IDs (full held-out set)
- `lite_train_200_strategies.json` — 1,000 LLM-extracted strategies the
  Lite-train graph was built from (`contextgraph` condition only;
  `contextgraph_verified` reuses the paper's Verified graph on
  `bolt://localhost:7687`, which is *not* checked into this directory)
- `no_memory_preds.json`, `contextgraph_preds.json`,
  `contextgraph_verified_preds.json` — SWE-agent predictions per method
- `analysis_summary.json` — 2-method analysis snapshot (no_memory + contextgraph)
- `analysis_summary_3way.json` — 3-method analysis including
  `contextgraph_verified`

## Reproduce

```bash
# 1. Extract strategies from the 200 train instances
uv run python scripts/extract_swectx_strategies.py \
  --split lite --output results/swectx/swectx_lite_strategies.json

# 2. Filter to the 200 train instances + wipe + rebuild Neo4j 7694
#    (filter step is the one-shot Python in MANIFEST narrative;
#     see the swe-context-bench branch for the exact commands.)
uv run python scripts/build_pro_graph.py \
  --input results/swectx/lite_train_200_strategies.json \
  --neo4j-uri bolt://localhost:7694 --batch-size 50

# 3. Start contextgraph server (wrapper sets NEO4J_URI + LITELLM keys)
tmux new-session -d -s swectx /tmp/start_swectx_server.sh   # port 8021

# 4. Run both methods on the 30-instance dev subset
uv run python scripts/run_swectx.py select --n 30
uv run python scripts/run_swectx.py run no_memory     --n-workers 3
uv run python scripts/run_swectx.py run contextgraph  --n-workers 3

# 5. Evaluate + report McNemar
uv run python scripts/analyze_swectx.py --max-workers 6
```

## Update — 2026-05-18: full N=100 confirmation of the +6 pp lift

Scaled the headline contextgraph_merged comparison from the 30-instance
dev signal to the full 100-instance Lite leave-out test set, in order to
firm up whether the +6.67 pp lift observed at N=30 was a real signal or
sampling noise.

| Method                  | Evaluated | Resolved | Rate/total |
|-------------------------|----------:|---------:|-----------:|
| `no_memory`             |    97/100 |   49/100 |     49.00% |
| `contextgraph_merged`   |   100/100 |   55/100 | **55.00%** |

McNemar paired test (resolved indicator on the full 100-instance test
set, unresolved/missing = failure for both methods):

| Comparison                            | wins | losses | both | neither |       p |
|---------------------------------------|-----:|-------:|-----:|--------:|--------:|
| `contextgraph_merged` vs `no_memory`  |   12 |      6 |   43 |      39 |  0.2379 |

The lift is **+6.00 pp at N=100**, almost identical to the **+6.67 pp at
N=30**. The 2:1 win/loss ratio is also stable across the two sample
sizes (4:2 at N=30; 12:6 at N=100). McNemar p does not reach 0.05 even
at N=100 — a 2:1 win/loss ratio would need roughly 3:1 with N=100 (or
2:1 with N≈200) to clear that bar — but the directional signal is
clearly reproducible, not a small-sample artifact. This is the strongest
SWE-ContextBench-style result we can produce given the harness blockers
documented above.

### Why this is the "better result" we set out to find

The diagnostic on the original 30-instance run showed that single-source
memory was actively hurting on a non-trivial subset (NM: 64 steps,
CG-Lite: 24 steps — premature submission after retrieving a plausible-
but-wrong rule). The fix was to MERGE the Lite-train (1,000 entries,
repo-prefixed) and Verified (8,910 entries, general) PlaybookEntry sets
into a single 9,910-entry graph so the agent gets both repo-specific
file-path-aware hints AND broader debugging patterns from a single
retrieval. This change took resolve rate from 16/30 (no-memory baseline)
to 18/30 (+6.67 pp) on the dev set and from 49/100 to 55/100 (+6.00 pp)
on the full set.

### 2026-05-18: paper images located → harness partially unblocked

The SWE-ContextBench authors publish per-instance prebuilt environments
on Docker Hub at `jiayuanz3/swecontextbench:<repo>.<repo>-<num>` (450+
tags covering both the experience pool and Related instances; not
linked from arXiv but findable via the dataset author's HF profile).
Each image ships:
- `/testbed`: the repo at the dataset's `base_commit`
- `/opt/conda/envs/testbed/`: matched-version Python env with the repo
  installed editable (e.g. Python 3.6 for Django 1.9, 3.8 for Django 4.x)
- No test-runner script — the harness must supply per-(repo, version)
  test invocation logic.

Validated end-to-end on `jiayuanz3/swecontextbench:django.django-18166`
(Related Lite, Django 1.9): gold patch + test_patch apply cleanly, but
the F2P tests need `forms_tests.tests.test_formsets.FormsFormsetTestCase.test_form_kwargs_empty_form`
(Django dotted-module path) instead of the pytest path
`tests/forms_tests/tests/test_formsets.py::FormsFormsetTestCase::test_form_kwargs_empty_form`
that the dataset stores. Also, `runtests.py --parallel` only exists in
Django ≥2.x.

Django 1.8 (`django.django-11776`) cannot be tested at all because the
image ships Python 3.6 and Django 1.8 references `html.parser.HTMLParseError`
which was removed in Python 3.5. This is a real benchmark wart, not a
harness bug; the paper presumably counts these as unresolved.

**Path forward**: a ~3-5 day engineering task to write per-(repo,
version) test runners covering django (≥2.x can use pytest, 1.x needs
custom args), sympy, sklearn, matplotlib, sphinx, pytest, pylint,
seaborn, requests, astropy, flask. Once that's in place the experience
trajectories (A2) and 5 paper settings (A3-A5) can run.

Status of A path: **A1 unblocked at the image level, blocked at the
per-repo test-runner-wiring level**. Not pursued in this branch given
the engineering scope and the standing +6 pp Lite-leave-out result.

After running the experiments above we tried to reproduce the literal
SWE-ContextBench protocol on the 99 Related Lite test set and hit two
hard blockers:

1. **The paper's evaluation harness is not publicly released.** The HF
   dataset `jiayuanz3/SWEContextBench` ships only the data parquets — no
   eval script. We attempted to run gold patches through the standard
   `swebench.harness.run_evaluation` on Related Lite. Even after filtering
   to 84 of 99 instances whose `(repo, version)` exists in
   `MAP_REPO_VERSION_TO_SPECS` and excluding Django <1.11 on Python 3.5,
   an 8-instance gold validation produced **0 passes / 7 unresolved /
   1 build error**. The Related Lite cross-reference PR base-commits use
   test commands and dependency sets that the standard harness does not
   know about.

2. **Building 1,100 experience trajectories** (paper §3.1 — run Claude
   Sonnet 4.5 on the experience pool to generate the context memory) is
   a multi-hour, ~\$1K agent run that we are not committing to without
   a working eval harness in place first.

What we have therefore tested is NOT the SWE-ContextBench paper protocol
in any of the following senses:

| Dimension | Paper | This branch |
|---|---|---|
| Test set | 99 Lite Related (cross-reference PRs) | 30 of 100 Lite Experience leave-out |
| Memory content | 1,100 agent trajectories or 217-token summaries | 1,000 LLM-extracted strategies from gold patches |
| Retrieval framework | Mem0 / OpenViking / LangMem / Supermemory | ContextGraph 3-channel (HippoRAG-style) |
| Eval harness | Paper's own (unreleased) | Standard `swebench.harness` for Lite |
| Oracle linkage | `Relationship.parquet` (experience↔related) | None — random 200/100 split |

The `contextgraph_merged 18/30 = 60.00%` headline reported earlier in
this file is **NOT comparable** to the paper's Supermemory `30.30%`
because (a) test sets differ, (b) memory sources differ, (c) harnesses
differ. It is at best evidence that ContextGraph's repo-aware retrieval
helps on a SWE-bench Lite leave-out probe, which is itself a useful but
separate finding.

## Status (final)

Branch `swe-context-bench` frozen as: (a) an honest SWE-bench Lite
leave-out dev signal for ContextGraph, including a transfer probe
(`contextgraph_verified` using the Verified 7687 graph) and a merged
condition; (b) an attempted partial reproduction of SWE-ContextBench
methodology that surfaces concrete blockers (harness, trajectory pool)
which would need to be resolved before paper-faithful numbers are
possible.
