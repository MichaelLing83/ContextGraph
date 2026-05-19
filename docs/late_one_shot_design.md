# Late-One-Shot Memory Discipline (Round 6)

## Motivation: what Rounds 1-5 ruled out

The SWE-bench Pro 99-instance experiment (gpt-5.4, call_limit=75) ran 15
memory-enabled variants against a no-memory baseline (no_memory_v2 =
17/77 = 22.08%). Every variant lost or tied:

| Round | Variant family | Best result | vs no_memory_v2 |
|---|---|---|---|
| 1 | eager / reactive / self_limit prompt | 20% | tie |
| 2 | retry_on_fail / critic | 17% | -5pp |
| 3 | safe / smart / persistent | 17-20% | -2 to -5pp |
| 4 | content_topk1 / content_repo_only / content_empty | 14-19% | -3 to -8pp |
| 5 | pre_context / doubled_budget / hard_constrain | 0-11% | -11 to -22pp |

Best single memory result so far is `hybrid_reactive_v2` at 18/81 =
22.22% — statistically indistinguishable from no_memory_v2 (McNemar
p = 0.623, 5 wins / 5 losses among the 75 instances they both completed).

Three failure modes recur across the negative results:

1. **Tool-call budget cost.** `content_empty` (server returns nothing)
   still loses 2-6 instances to no_memory among shared cases. Just
   *having the tool exposed* costs ~5 calls per query and drains the
   75-call budget. The memory's benefit has to clear that bar before
   it shows up.
2. **Cross-repo contamination.** The graph is built from 1795
   trajectories whose 41 source repos have zero overlap with SWE-bench
   Pro's 11 repos (chosen deliberately to avoid leakage). Generic
   strategies from `flask` / `ansible` mislead agents working in
   `navidrome` / `tutanota`.
3. **Premature commitment.** When memory is injected before the agent
   explores (`pre_context`, eager prompts), the agent locks in a
   strategy before it has any real perception of the bug. SWE-agent's
   early edits dominate trajectory direction, and a wrong early edit
   inside a 75-call budget is unrecoverable.

## The hypothesis behind late-one-shot

Memory retrieval is bottlenecked by **query quality**. The three query
sources we tried had progressively poorer signal:

| Query input | Information content | Round result |
|---|---|---|
| Raw `problem_statement` (user-language PR description) | Lowest | pre_context: 2/84 |
| `current_error` extracted by reactive prompt | Medium | reactive_v2: 18/81 (best) |
| Agent's structured perception after first repro failure | Highest | this proposal |

The proposal: forbid memory queries until the agent has run a
reproduction script and observed a real failure. At that moment, the
agent has the maximum signal (concrete error tokens, files it has
touched, the last command, an emerging hypothesis) for the lowest
information cost. Constrain the agent to exactly one query, packed
with that structured perception, and forbid further queries.

This dominates `hybrid_reactive_v2` on three axes:

- **Query richness**: `current_error` alone vs full perception summary.
- **Timing**: reactive_v2 fires after 3 failures (often turn 30+);
  late-one-shot fires after the first repro failure (typically turn
  10-15), preserving more budget for editing.
- **Determinism**: agent does not spend turns deciding whether to
  query — the protocol fires it automatically when the trigger
  condition is met.

## Implementation

`configs/swe_agent_late_one_shot.yaml` enforces the discipline through
the instance_template. No code changes to the tool or HTTP server are
required — the structured perception summary is packed into the
existing `current_error` field, and the existing
`contextgraph_pro_server` retrieval already treats that field as the
primary text signal.

### Discipline encoded in the prompt

This is prompt-level enforcement, not a hard runtime gate. A model that
deviates from the protocol (calling before failure, calling twice) will
not be blocked by tooling — see the "What it does NOT test" section
below for the follow-up that would harden this. The expectation is that
gpt-5.4 respects an explicit numbered protocol; that assumption is part
of what this experiment tests.

We intentionally keep the surface area minimal for this iteration. The
trade-off is explicit: tighter enforcement (a wrapper tool that counts
calls or a history processor that rejects out-of-protocol invocations)
adds code paths whose own bugs would confound the experiment's signal.
If the prompt-only protocol shows even a hint of beating no_memory_v2,
the follow-up promotes it to a runtime gate; if it ties or loses, the
runtime gate would not have changed the conclusion.

1. `query_memory` is forbidden until a reproduction script has run and
   produced a real failure.
2. At that moment, the agent must call `query_memory` exactly once
   with `current_error` packed as:

   ```
   ERROR: <one-line summary>
   FILES_TOUCHED: <comma-separated>
   LAST_COMMAND: <command that triggered the failure>
   HYPOTHESIS: <one sentence>
   ```

3. `phase` is set to `"post_repro_failure"` and `task_description` is
   a short PR-intent summary.
4. Further `query_memory` calls are explicitly forbidden by the prompt.

### How to evaluate

Run the config against the same 99-instance Pro subset (
`/tmp/instances_100_ready_overlay.json`) using the standard
sweagent run-batch harness pointed at port 8013 (`neo4j-contextgraph`
baseline). Compare resolved counts to `no_memory_v2` (17/77) and
`hybrid_reactive_v2` (18/81) using McNemar on the paired-instances
intersection.

Success criterion (the one that has eluded Rounds 1-5):

- ≥1pp resolved-rate gain over `no_memory_v2`, **or**
- McNemar p < 0.10 with more wins than losses on the shared subset.

### What it does NOT test

- Whether richer retrieval (structured query packing) needs a server
  schema change to fully exploit the additional signal. The current
  server tokenizes the whole `current_error` string; a future PR could
  add explicit `files_touched` / `hypothesis` fields and weight them
  separately in retrieval.
- Whether multiple queries at *different* well-chosen moments (e.g.
  one after the first repro failure, one after the candidate patch
  fails its second test) outperform one. The constraint here is
  deliberately strict to isolate the perceive-then-retrieve signal.
- Whether the discipline can be enforced mechanically (a history
  processor that rejects `query_memory` calls before a failed
  reproduction has been observed and after the first successful call)
  rather than relying on prompt obedience. The current PR keeps the
  surface area minimal — if prompt-only discipline produces a real
  signal in the experiment, a follow-up can promote it to a wrapper
  tool or sweagent history processor for stricter enforcement.

### Risk

If late-one-shot still ties no_memory_v2, the experimental conclusion
is that **memory built from cross-repo trajectories cannot help
SWE-bench Pro under a fixed budget**, and the next step is to change
the memory source (e.g. build an in-repo graph from the same repos'
historical PRs) rather than the query protocol.
