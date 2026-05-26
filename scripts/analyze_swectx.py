"""Compare no_memory vs contextgraph on the SWE-ContextBench Lite leave-out dev set.

Reads per-method preds.json files written by SWE-agent, evaluates them with the
official SWE-bench Lite harness, and prints resolve rates + a McNemar paired test.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Dict, Set

PROJECT_ROOT = Path(__file__).parent.parent
RESULTS_DIR = PROJECT_ROOT / "results" / "swectx"
METHODS = ("no_memory", "contextgraph")


def _exact_binomial_p_two_sided(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    log_term = math.lgamma(n + 1) - n * math.log(2)
    p = 0.0
    for i in range(k + 1):
        p += math.exp(log_term - math.lgamma(i + 1) - math.lgamma(n - i + 1))
    return min(1.0, 2 * p)


def _load_resolved(method: str, run_id: str | None = None) -> Set[str]:
    """Read swebench harness output directory for a method and return the set of
    resolved instance IDs."""
    if run_id is None:
        run_id = f"swectx_dev_{method}"
    log_dir = PROJECT_ROOT / "logs" / "run_evaluation" / run_id / "output"
    resolved: Set[str] = set()
    if not log_dir.exists():
        return resolved
    for report in log_dir.glob("*/report.json"):
        with open(report) as f:
            data = json.load(f)
        for iid, payload in data.items():
            if payload.get("resolved"):
                resolved.add(iid)
    return resolved


def _load_evaluated(method: str, run_id: str | None = None) -> Set[str]:
    if run_id is None:
        run_id = f"swectx_dev_{method}"
    log_dir = PROJECT_ROOT / "logs" / "run_evaluation" / run_id / "output"
    seen: Set[str] = set()
    if not log_dir.exists():
        return seen
    for report in log_dir.glob("*/report.json"):
        seen.add(report.parent.name)
    return seen


def run_eval_for_method(method: str, max_workers: int = 6, dataset: str = "SWE-bench/SWE-bench_Lite"):
    """Invoke swebench.harness.run_evaluation on this method's predictions."""
    preds = RESULTS_DIR / method / "output" / "preds.json"
    if not preds.exists():
        print(f"[{method}] no preds.json at {preds}, skipping eval")
        return False
    run_id = f"swectx_dev_{method}"
    print(f"\n=== Evaluating {method} (run_id={run_id}) ===")
    cmd = [
        sys.executable, "-m", "swebench.harness.run_evaluation",
        "--dataset_name", dataset,
        "--predictions_path", str(preds),
        "--max_workers", str(max_workers),
        "--run_id", run_id,
        "--report_dir", str(RESULTS_DIR / method / "eval"),
    ]
    # cmd is constructed list-form with hard-coded module names and known
    # paths under PROJECT_ROOT; no shell interpretation, no untrusted input.
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT))  # nosec B603  # nosem
    return proc.returncode == 0


def summarize(methods: list[str], total_test: int) -> Dict[str, Dict]:
    rows = {}
    for m in methods:
        resolved = _load_resolved(m)
        evaluated = _load_evaluated(m)
        rows[m] = {
            "resolved": sorted(resolved),
            "evaluated": sorted(evaluated),
            "n_resolved": len(resolved),
            "n_evaluated": len(evaluated),
            "rate_over_total": len(resolved) / total_test if total_test else 0.0,
            "rate_over_evaluated": len(resolved) / len(evaluated) if evaluated else 0.0,
        }
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--methods", default=",".join(METHODS))
    ap.add_argument("--max-workers", type=int, default=6)
    ap.add_argument("--dataset", default="SWE-bench/SWE-bench_Lite")
    ap.add_argument("--skip-eval", action="store_true",
                    help="Skip the swebench harness call (use existing eval logs)")
    args = ap.parse_args()
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]

    selected = json.load(open(RESULTS_DIR / "selected_instances.json"))
    total_test = selected.get("n", len(selected.get("instance_ids", [])))
    print(f"Test set: {total_test} instances")

    if not args.skip_eval:
        for m in methods:
            run_eval_for_method(m, max_workers=args.max_workers, dataset=args.dataset)

    rows = summarize(methods, total_test)

    print("\n" + "=" * 78)
    print("RESOLVE RATES")
    print("=" * 78)
    print(f"{'Method':<15} {'Evaluated':>10} {'Resolved':>9} {'Rate/total':>11} {'Rate/eval':>10}")
    print("-" * 78)
    for m in methods:
        r = rows[m]
        print(f"{m:<15} {r['n_evaluated']:>10} {r['n_resolved']:>9} "
              f"{r['rate_over_total']*100:>10.2f}% {r['rate_over_evaluated']*100:>9.2f}%")

    if len(methods) >= 2 and "no_memory" in methods:
        baseline = "no_memory"
        baseline_resolved = set(rows[baseline]["resolved"])
        print("\n" + "=" * 78)
        print(f"McNEMAR PAIRED (resolved indicator on full test set, "
              f"unresolved/missing = failure for both)")
        print("=" * 78)
        print(f"{'Method':<15} {'wins':>6} {'losses':>7} {'both':>5} {'neither':>8} {'p':>8}")
        print("-" * 78)
        for m in methods:
            if m == baseline:
                continue
            m_resolved = set(rows[m]["resolved"])
            wins = len(m_resolved - baseline_resolved)
            losses = len(baseline_resolved - m_resolved)
            both = len(m_resolved & baseline_resolved)
            neither = total_test - wins - losses - both
            p = _exact_binomial_p_two_sided(wins, losses)
            print(f"{m:<15} {wins:>6} {losses:>7} {both:>5} {neither:>8} {p:>8.4f}")

    out = RESULTS_DIR / "analysis_summary.json"
    with open(out, "w") as f:
        json.dump({
            "test_set_size": total_test,
            "methods": rows,
        }, f, indent=2, default=str)
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
