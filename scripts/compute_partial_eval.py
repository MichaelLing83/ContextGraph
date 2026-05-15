#!/usr/bin/env python3
"""Manually compute eval_results.json from individual *_output.json files.

Walks a method's `eval_partial/` directory, reads the first `*_output.json`
in each instance subdir (produced by SWE-bench's partial harness), and
writes `eval_partial/eval_results.json` keyed by instance id.

Paths default to the SWE-bench Pro layout used during development but are
overridable via flags / env var so the script is reusable across hosts.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path

import pandas as pd


# Default results root resolved at runtime, relative to this script.
# The dev host historically used /home/jie/codes/ContextGraph/... but that
# breaks anywhere else. Callers can override with $CONTEXTGRAPH_RESULTS_ROOT
# or --results-root.
_DEFAULT_RESULTS_ROOT = str(
    Path(__file__).resolve().parent.parent / "results" / "swebench_pro"
)


def _parse_test_list(
    raw: object,
    *,
    instance_id: str | None = None,
    column: str | None = None,
) -> set:
    """Parse a fail_to_pass/pass_to_pass CSV cell into a set of test names.

    The CSV stores these as Python list literals (e.g. "['t1', 't2']") or
    JSON arrays. Uses ast.literal_eval (NOT eval) so a malicious / corrupt
    CSV cannot execute arbitrary code. Returns an empty set for empty /
    null cells; logs a warning (with instance_id + column for debuggability)
    and returns an empty set for malformed cells rather than crashing the
    whole run.

    Only list/tuple/set results are accepted — a bare JSON string would
    otherwise iterate per character and produce a set of letters, silently
    breaking the resolved comparison.
    """
    def _context_suffix() -> str:
        bits = []
        if instance_id:
            bits.append(f"instance_id={instance_id}")
        if column:
            bits.append(f"column={column}")
        return f" ({', '.join(bits)})" if bits else ""

    def _coerce(parsed: object) -> set:
        if isinstance(parsed, (list, tuple, set)):
            return set(parsed)
        sys.stderr.write(
            f"warning: parsed test list is {type(parsed).__name__}, expected "
            f"sequence{_context_suffix()}; treating as empty\n"
        )
        return set()

    if raw is None:
        return set()
    # pd.isna recognises NaN/NaT/pd.NA/None on scalars; guard against
    # non-scalar inputs (arrays, Series) which would return arrays or
    # raise TypeError.
    try:
        if pd.isna(raw):
            return set()
    except (TypeError, ValueError):
        pass
    if isinstance(raw, (list, tuple, set)):
        return set(raw)
    text = str(raw).strip()
    if not text:
        return set()
    # Try JSON first (fast and accepts the canonical literal form).
    try:
        return _coerce(json.loads(text))
    except (ValueError, TypeError):
        pass
    # Fall back to ast.literal_eval for Python repr — wraps every error it
    # can raise (ValueError, SyntaxError, MemoryError, TypeError, RecursionError).
    try:
        return _coerce(ast.literal_eval(text))
    except (ValueError, SyntaxError, MemoryError, TypeError, RecursionError) as e:
        sys.stderr.write(
            f"warning: could not parse test list{_context_suffix()}: {e!r}; "
            "treating as empty\n"
        )
        return set()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute partial evaluation results for SWE-bench Pro runs.",
    )
    parser.add_argument(
        "method",
        help="Method name whose results to evaluate "
             "(used to locate the results subdirectory).",
    )
    parser.add_argument(
        "--results-root",
        default=os.environ.get("CONTEXTGRAPH_RESULTS_ROOT", _DEFAULT_RESULTS_ROOT),
        help="Root directory containing per-method result dirs. "
             "Defaults to $CONTEXTGRAPH_RESULTS_ROOT or "
             f"{_DEFAULT_RESULTS_ROOT}.",
    )
    parser.add_argument(
        "--csv-path",
        default=None,
        help="Path to the raw CSV with fail_to_pass / pass_to_pass columns. "
             "Defaults to `<results-root>/raw_sample_99.csv`.",
    )
    parser.add_argument(
        "--output-name",
        default="eval_results.json",
        help="Filename written under <results-root>/<method>/eval_partial/.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    results_root = Path(args.results_root).expanduser().resolve()
    out_dir = results_root / args.method / "eval_partial"
    csv_path = (
        Path(args.csv_path).expanduser()
        if args.csv_path is not None
        else results_root / "raw_sample_99.csv"
    )

    if not out_dir.exists():
        sys.stderr.write(f"error: eval_partial directory not found: {out_dir}\n")
        return 2
    if not csv_path.exists():
        sys.stderr.write(f"error: CSV not found: {csv_path}\n")
        return 2

    csv = pd.read_csv(csv_path)
    try:
        csv = csv.set_index("instance_id", drop=False)
    except KeyError:
        sys.stderr.write(
            f"error: CSV {csv_path} is missing required 'instance_id' column\n"
        )
        return 2
    if not csv.index.is_unique:
        dup = csv.index[csv.index.duplicated()].unique().tolist()[:5]
        sys.stderr.write(
            f"error: CSV {csv_path} has duplicate instance_id values; "
            f"first few: {dup}\n"
        )
        return 2

    results: dict[str, bool] = {}
    for d in out_dir.iterdir():
        if not d.is_dir():
            continue
        iid = d.name
        # Path.glob iteration order is filesystem-dependent. Sort so that
        # a directory with multiple *_output.json files produces a
        # deterministic eval_results.json across runs.
        outs = sorted(d.glob("*_output.json"))
        if not outs:
            continue
        if len(outs) > 1:
            sys.stderr.write(
                f"warning: instance {iid} has {len(outs)} *_output.json files; "
                f"using {outs[0].name}\n"
            )
        output_path = outs[0]
        # Catch only the failure modes we actually expect from this loop —
        # file/JSON I/O, missing/wrong-shape fields in the parsed output,
        # and DataFrame indexing surprises. A bare `except Exception` would
        # swallow programming errors (e.g. typos) and hide them in stdout.
        try:
            with open(output_path) as f:
                output = json.load(f)
            if iid not in csv.index:
                continue
            raw = csv.loc[iid]
            passed_tests = {x["name"] for x in output["tests"] if x["status"] == "PASSED"}
            f2p = _parse_test_list(raw["fail_to_pass"], instance_id=iid, column="fail_to_pass")
            p2p = _parse_test_list(raw["pass_to_pass"], instance_id=iid, column="pass_to_pass")
            results[iid] = (f2p | p2p) <= passed_tests
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
            sys.stderr.write(
                f"warning: {type(e).__name__} processing {output_path}: {e}\n"
            )

    out_file = out_dir / args.output_name
    with open(out_file, "w") as f:
        json.dump(results, f)

    passed = sum(1 for v in results.values() if v)
    total = len(results)
    if total:
        print(f"{args.method}: {passed}/{total} = {passed/total*100:.2f}%")
    else:
        print(f"{args.method}: no results")
    return 0


if __name__ == "__main__":
    sys.exit(main())
