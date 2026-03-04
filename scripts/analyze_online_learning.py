#!/usr/bin/env python3
"""Analyze online learning experiment results.

Loads results.json from an online learning experiment and computes:
1. pass@k and pass^k table for both groups
2. Learning curve: rolling pass@1 over problem sequence
3. Per-attempt improvement: attempt-specific success rate
4. Retry benefit isolation: treatment - control = memory contribution
5. McNemar's paired test on per-problem outcomes

Usage:
    python scripts/analyze_online_learning.py
    python scripts/analyze_online_learning.py --results results/online_learning/results.json
    python scripts/analyze_online_learning.py --window 20  # rolling window size
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = REPO_ROOT / "results" / "online_learning" / "results.json"


def load_results(path: Path) -> dict:
    """Load results.json."""
    if not path.exists():
        print(f"Error: results file not found: {path}", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def compute_pass_at_k(problems: List[dict], k: int) -> float:
    """Fraction of problems where ANY of first k attempts succeeded."""
    if not problems:
        return 0.0
    n = len(problems)
    return sum(1 for p in problems if any(p["attempts"][:k])) / n


def compute_pass_hat_k(problems: List[dict], k: int) -> float:
    """Fraction of problems where ALL first k attempts succeeded (pass^k)."""
    if not problems:
        return 0.0
    n = len(problems)
    return sum(
        1 for p in problems
        if len(p["attempts"][:k]) == k and all(p["attempts"][:k])
    ) / n


def print_pass_table(data: dict) -> None:
    """Print pass@k and pass^k table for all groups."""
    k_attempts = data.get("k_attempts", 3)
    n = data.get("n_problems", 0)

    groups = []
    for name in ["control", "treatment"]:
        if name in data:
            groups.append((name, data[name]["problems"]))

    if not groups:
        print("No group data found.")
        return

    # Header
    ks = list(range(1, k_attempts + 1))
    header = f"{'Metric':<20}"
    for name, _ in groups:
        header += f"  {name:>12}"
    if len(groups) == 2:
        header += f"  {'delta':>12}"
    print(header)
    print("-" * len(header))

    # pass@k rows
    for k in ks:
        row = f"{'pass@' + str(k):<20}"
        vals = []
        for name, problems in groups:
            v = compute_pass_at_k(problems, k)
            vals.append(v)
            row += f"  {v:>11.1%}"
        if len(vals) == 2:
            delta = vals[1] - vals[0]
            sign = "+" if delta >= 0 else ""
            row += f"  {sign}{delta:>10.1%}"
        print(row)

    print()

    # pass^k rows
    for k in ks:
        row = f"{'pass^' + str(k):<20}"
        vals = []
        for name, problems in groups:
            v = compute_pass_hat_k(problems, k)
            vals.append(v)
            row += f"  {v:>11.1%}"
        if len(vals) == 2:
            delta = vals[1] - vals[0]
            sign = "+" if delta >= 0 else ""
            row += f"  {sign}{delta:>10.1%}"
        print(row)

    print(f"\nn={n} problems, k={k_attempts} attempts each")


def print_per_attempt_rates(data: dict) -> None:
    """Print success rate broken down by attempt number."""
    k_attempts = data.get("k_attempts", 3)

    print(f"\n{'=' * 60}")
    print("PER-ATTEMPT SUCCESS RATES")
    print(f"{'=' * 60}")

    header = f"{'Attempt':<12}"
    group_names = [g for g in ["control", "treatment"] if g in data]
    for name in group_names:
        header += f"  {name:>12}"
    if len(group_names) == 2:
        header += f"  {'delta':>12}"
    print(header)
    print("-" * len(header))

    for a in range(1, k_attempts + 1):
        row = f"{'#' + str(a):<12}"
        vals = []
        for name in group_names:
            problems = data[name]["problems"]
            # Count problems that have this attempt
            with_attempt = [p for p in problems if len(p["attempts"]) >= a]
            if with_attempt:
                rate = sum(1 for p in with_attempt if p["attempts"][a - 1]) / len(with_attempt)
            else:
                rate = 0.0
            vals.append(rate)
            row += f"  {rate:>11.1%}"
        if len(vals) == 2:
            delta = vals[1] - vals[0]
            sign = "+" if delta >= 0 else ""
            row += f"  {sign}{delta:>10.1%}"
        print(row)


def print_learning_curve(data: dict, window: int = 20) -> None:
    """Print rolling pass@1 over problem sequence (treatment only)."""
    if "treatment" not in data:
        return

    problems = data["treatment"]["problems"]
    problem_order = data.get("problem_order", [p["id"] for p in problems])

    # Build ordered pass@1 results
    id_to_attempts = {p["id"]: p["attempts"] for p in problems}
    ordered_pass1 = []
    for iid in problem_order:
        attempts = id_to_attempts.get(iid, [])
        ordered_pass1.append(attempts[0] if attempts else False)

    if len(ordered_pass1) < window:
        return

    print(f"\n{'=' * 60}")
    print(f"LEARNING CURVE (rolling pass@1, window={window})")
    print(f"{'=' * 60}")

    # Print at intervals
    step = max(1, len(ordered_pass1) // 10)
    print(f"{'Problem #':<12}  {'Rolling pass@1':>15}")
    print("-" * 30)
    for i in range(window, len(ordered_pass1) + 1, step):
        w = ordered_pass1[i - window:i]
        rate = sum(w) / len(w)
        print(f"{i:<12}  {rate:>14.1%}")

    # Always print the last point
    w = ordered_pass1[-window:]
    rate = sum(w) / len(w)
    print(f"{len(ordered_pass1):<12}  {rate:>14.1%}")


def print_retry_benefit(data: dict) -> None:
    """Isolate the memory contribution: treatment pass@k - control pass@k."""
    if "control" not in data or "treatment" not in data:
        return

    k_attempts = data.get("k_attempts", 3)

    print(f"\n{'=' * 60}")
    print("RETRY BENEFIT ISOLATION (memory contribution)")
    print(f"{'=' * 60}")
    print("Memory contribution = treatment pass@k - control pass@k")
    print("(positive = memory helps beyond pure retry benefit)\n")

    for k in range(1, k_attempts + 1):
        ctrl = compute_pass_at_k(data["control"]["problems"], k)
        treat = compute_pass_at_k(data["treatment"]["problems"], k)
        delta = treat - ctrl
        sign = "+" if delta >= 0 else ""
        print(f"  pass@{k}: {sign}{delta:.1%} (treatment {treat:.1%} - control {ctrl:.1%})")


def print_mcnemar_test(data: dict) -> None:
    """McNemar's paired test on per-problem pass@1 outcomes."""
    if "control" not in data or "treatment" not in data:
        return

    ctrl_problems = {p["id"]: p for p in data["control"]["problems"]}
    treat_problems = {p["id"]: p for p in data["treatment"]["problems"]}
    common_ids = sorted(set(ctrl_problems) & set(treat_problems))

    if not common_ids:
        return

    # Build contingency table
    # a: both succeed, b: ctrl succeeds & treat fails
    # c: ctrl fails & treat succeeds, d: both fail
    a = b = c = d = 0
    for iid in common_ids:
        ctrl_pass = any(ctrl_problems[iid]["attempts"])
        treat_pass = any(treat_problems[iid]["attempts"])
        if ctrl_pass and treat_pass:
            a += 1
        elif ctrl_pass and not treat_pass:
            b += 1
        elif not ctrl_pass and treat_pass:
            c += 1
        else:
            d += 1

    print(f"\n{'=' * 60}")
    print("McNEMAR'S PAIRED TEST (pass@k, all attempts)")
    print(f"{'=' * 60}")
    print(f"  n = {len(common_ids)} paired problems")
    print(f"  Both pass:  {a}")
    print(f"  Only ctrl:  {b}")
    print(f"  Only treat: {c}")
    print(f"  Both fail:  {d}")

    # McNemar statistic (with continuity correction)
    discordant = b + c
    if discordant == 0:
        print("  No discordant pairs — test not applicable.")
        return

    chi2 = (abs(b - c) - 1) ** 2 / (b + c)
    print(f"  Chi-squared (corrected): {chi2:.3f}")

    # p-value from chi-squared with 1 df
    try:
        from scipy.stats import chi2 as chi2_dist
        p_value = 1 - chi2_dist.cdf(chi2, df=1)
        sig = "***" if p_value < 0.001 else "**" if p_value < 0.01 else "*" if p_value < 0.05 else "n.s."
        print(f"  p-value: {p_value:.4f} {sig}")
    except ImportError:
        print("  (install scipy for p-value computation)")


def print_cost_summary(data: dict) -> None:
    """Print cost summary per group."""
    print(f"\n{'=' * 60}")
    print("COST SUMMARY")
    print(f"{'=' * 60}")

    for name in ["control", "treatment"]:
        if name not in data:
            continue
        problems = data[name]["problems"]
        total_cost = sum(sum(p.get("costs", [])) for p in problems)
        total_tokens = sum(sum(p.get("tokens", [])) for p in problems)
        n_attempts = sum(len(p["attempts"]) for p in problems)
        print(f"\n  {name.upper()}:")
        print(f"    Total cost: ${total_cost:.2f}")
        print(f"    Total tokens: {total_tokens:,}")
        print(f"    Total attempts: {n_attempts}")
        if n_attempts > 0:
            print(f"    Avg cost/attempt: ${total_cost / n_attempts:.2f}")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze online learning experiment results",
    )
    parser.add_argument(
        "--results", type=Path, default=DEFAULT_RESULTS,
        help=f"Path to results.json (default: {DEFAULT_RESULTS})",
    )
    parser.add_argument(
        "--window", type=int, default=20,
        help="Rolling window size for learning curve (default: 20)",
    )
    args = parser.parse_args()

    data = load_results(args.results)

    print(f"{'=' * 60}")
    print("ONLINE LEARNING EXPERIMENT ANALYSIS")
    print(f"{'=' * 60}")
    print(f"Type: {data.get('experiment_type', 'unknown')}")
    print(f"Problems: {data.get('n_problems', '?')}")
    print(f"Attempts: {data.get('k_attempts', '?')}")

    print(f"\n{'=' * 60}")
    print("PASS@K AND PASS^K TABLE")
    print(f"{'=' * 60}")
    print_pass_table(data)

    print_per_attempt_rates(data)
    print_learning_curve(data, window=args.window)
    print_retry_benefit(data)
    print_mcnemar_test(data)
    print_cost_summary(data)


if __name__ == "__main__":
    main()
