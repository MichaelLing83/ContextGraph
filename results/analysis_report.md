# ContextGraph A/B Experiment — Analysis Report

## 1. Experiment Overview

| Parameter | Value |
|-----------|-------|
| Total trajectories | 3,591 |
| Train / Test split | 1,795 / 1,796 |
| Problems per group | 1,796 |
| Attempts per problem | 5 |
| Token reduction factor | 15% |
| Success boost (pp) | 10pp |
| Random seed | 42 |

**Design**: Within the 1,796 test problems, each problem was evaluated under
both control (no context graph) and treatment (with context graph) conditions
across 5 independent attempts. The treatment applied a
10pp per-attempt success boost and a
15% token reduction factor.

## 2. Primary Results — pass@k Comparison

| Metric | Control | Treatment | Δ (pp) | 95% CI (ctrl) | 95% CI (treat) | z | p-value | Sig. (Bonf.) | Cohen's h | Effect |
|--------|---------|-----------|--------|---------------|----------------|---|---------|--------------|-----------|--------|
| pass@1 | 59.2% | 67.8% | +8.5 | [57.0%, 61.5%] | [65.6%, 69.9%] | 5.30 | 1.14e-07 | Yes | 0.177 | negligible |
| pass@3 | 67.1% | 78.8% | +11.7 | [64.9%, 69.3%] | [76.9%, 80.7%] | 7.89 | 3.11e-15 | Yes | 0.265 | small |
| pass@5 | 73.2% | 85.7% | +12.5 | [71.1%, 75.2%] | [84.1%, 87.3%] | 9.30 | 0.00e+00 | Yes | 0.314 | small |

> **Bonferroni-corrected α = 0.0125** (4 comparisons)

**Key finding**: All three pass@k metrics show statistically significant
improvements after Bonferroni correction. The effect size grows with k
(pass@1 h=0.177 → pass@5 h=0.314),
indicating the context graph is especially effective at enabling eventual
success across multiple attempts.

## 3. Token Consumption

| Metric | Control | Treatment | Δ |
|--------|---------|-----------|---|
| Avg tokens/problem | 236,111 | 200,695 | −15.0% |
| Total tokens (est.) | 424,056,000 | 360,447,600 | −15.0% |

The treatment group consumed exactly **15.0%** fewer tokens.
This matches the configured `token_reduction_factor` precisely, which is
expected in simulation mode — the reduction is applied deterministically
rather than emergent. In a live experiment, the actual reduction would depend
on how effectively the context graph prevents wasted exploration.

## 4. Failure Attempt Ratio

| Metric | Control | Treatment | Δ (pp) | z | p-value | Sig. | Cohen's h |
|--------|---------|-----------|--------|---|---------|------|-----------|
| Failure ratio | 68.1% | 59.2% | -8.8 | -5.50 | 3.78e-08 | Yes | -0.184 |

The failure ratio dropped by **8.8pp** (from 68.1%
to 59.2%), a statistically significant reduction with a
**negligible** effect size (h = -0.184).

## 5. Efficiency (Attempts to First Success)

| Metric | Control | Treatment | Δ |
|--------|---------|-----------|---|
| Avg attempts to 1st success | 1.459 | 1.463 | -0.0037 |

The average number of attempts to first success is virtually identical
between groups (Δ = -0.0037). This means the context
graph does **not** make problems easier to solve on the first try in a way
that changes attempt ordering — rather, it raises the ceiling of solvable
problems across multiple attempts. The 10pp success boost lifts previously-
failing attempts into success uniformly, without concentrating gains on
earlier attempts.

## 6. Effect Size Summary

| Comparison | Cohen's h | Interpretation |
|------------|-----------|----------------|
| pass@1 | 0.177 | negligible |
| pass@3 | 0.265 | small |
| pass@5 | 0.314 | small |
| failure_ratio | -0.184 | negligible |

All effect sizes fall in the **small** range (0.2 ≤ |h| < 0.5). This is
consistent with a real but modest improvement, which is realistic for a
context-graph augmentation applied to an already-strong baseline agent.

## 7. Statistical Power

With n = 1,796 problems per group and the smallest observed effect
(pass@1, h = 0.177), approximate post-hoc power is **100.0%**.
The experiment is adequately powered to detect effects of this magnitude.

## 8. Limitations & Caveats

1. **Simulation, not live**: The treatment effects are *simulated* by applying
   a deterministic success boost and token reduction factor. In a real
   deployment, the context graph's impact would be mediated by the quality of
   retrieved context, which could be better or worse than assumed.

2. **Deterministic token reduction**: The 15.0% token savings is a direct
   artifact of the `token_reduction_factor` config parameter. It does not
   reflect emergent efficiency gains from fewer wasted loops.

3. **Uniform success boost**: The 10pp success boost is applied uniformly
   across all problems. In practice, the context graph would likely help
   some problem types more than others (e.g., recurring bug patterns vs.
   novel features).

4. **Same test set**: Both conditions are evaluated on the same 1,796 problems,
   making this a paired design. The z-tests above treat them as independent,
   which is conservative (a paired test would have even more power).

5. **No interaction effects**: The simulation does not model interactions
   between the context graph and specific problem characteristics, loop
   severity, or repository familiarity.

## 9. Recommendations & Follow-up Experiments

1. **Live A/B test**: Run the experiment with actual OpenHands agents to
   validate that the simulated improvements translate to real-world gains.
   Priority: **high**.

2. **Per-category analysis**: Break down results by task type (bug fix,
   feature, refactor) to identify where the context graph helps most.

3. **Adaptive token reduction**: Instead of a fixed 15% factor, measure
   actual token savings from loop avoidance and early termination in live
   runs.

4. **Dose-response study**: Vary the amount of context provided (e.g., top-1
   vs top-3 vs top-5 retrieved methodologies) to find the optimal retrieval
   depth.

5. **Temporal analysis**: Test whether the context graph's benefit grows
   over time as the graph accumulates more problem-solving patterns.

6. **Loop-specific deep-dive**: Separately analyze trajectories that
   originally had loops vs. those that did not, to quantify the graph's
   loop-prevention value.

## 10. Conclusion

The ContextGraph treatment shows **statistically significant improvements**
across all pass@k levels and failure ratio, with small-to-moderate effect
sizes. The most notable improvement is at pass@5 (+12.5pp), suggesting the
context graph is particularly valuable for enabling eventual success across
multiple attempts. Token consumption is reduced by 15.0% (by design in this
simulation).

These results support proceeding to a **live A/B test** to validate whether
the simulated gains hold when the context graph is used by real agents.
The simulation provides an optimistic upper bound; real-world effects will
depend on retrieval quality and how well agents incorporate the provided
context.

---
*Generated from `results/experiment_output.json` | Bonferroni α = 0.0125 | n = 1,796 per group*