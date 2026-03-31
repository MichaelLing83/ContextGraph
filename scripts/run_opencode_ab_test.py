#!/usr/bin/env python3
"""Prepare and run 5x5 A/B tests for hard SWE-bench problems."""

import json
import os
import subprocess
import shutil
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE = Path(os.environ.get("AB_TEST_DIR", "/tmp/swe-bench-test/hard"))
OPENCODE_CONFIG = REPO_ROOT / "opencode.json"

PROBLEMS = [
    {
        "id": "django__django-13513",
        "repo_url": "https://github.com/django/django.git",
        "base_commit": "6599608c4d0befdcb820ddccce55f183f247ae4f",
        "gt_lines": 44,
        "problem": (
            "debug error view doesn't respect exc.__suppress_context__ (PEP 415)\n\n"
            "Consider a view that raises an exception with raise ... from None. "
            "The debug error view still shows the chained exception context, "
            "ignoring __suppress_context__. The technical_500.html template should "
            "check exc_value.__suppress_context__ and not display the chained "
            "exception when it is True."
        ),
    },
    {
        "id": "sympy__sympy-14531",
        "repo_url": "https://github.com/sympy/sympy.git",
        "base_commit": "205da797006360fc629110937e39a19c9561313e",
        "gt_lines": 38,
        "problem": (
            "StrPrinter settings are not respected by certain subexpressions\n\n"
            "For example:\n"
            ">>> sstr(x + S(1)/2, sympy_integers=True)\n"
            "'x + S(1)/2'\n"
            ">>> sstr(Eq(x, S(1)/2), sympy_integers=True)\n"
            "'Eq(x, 1/2)'\n\n"
            "The Eq subexpression doesn't respect the sympy_integers=True setting "
            "because _print_Relational doesn't pass the settings through."
        ),
    },
    {
        "id": "django__django-15561",
        "repo_url": "https://github.com/django/django.git",
        "base_commit": "6991880109e35c879b71b7d9d9c154baeec12b89",
        "gt_lines": 35,
        "problem": (
            "AlterField operation should be noop when adding/changing choices on SQLite.\n\n"
            "While writing a test case, I found that for SQLite, even a seemingly "
            "db-transparent change like adding choices triggers an unnecessary "
            "ALTER TABLE operation. The fix should add 'choices' to the "
            "non_database_attrs tuple in the SQLite schema editor."
        ),
        "hint": "Adding choices to non_database_attrs in django/db/backends/base/schema.py should fix it.",
    },
    {
        "id": "scikit-learn__scikit-learn-10297",
        "repo_url": "https://github.com/scikit-learn/scikit-learn.git",
        "base_commit": "b90661d6a46aa3619d3eec94d5281f5888add501",
        "gt_lines": 34,
        "problem": (
            "linear_model.RidgeClassifierCV's Parameter store_cv_values issue\n\n"
            "RidgeClassifierCV inherits from _BaseRidgeCV which has store_cv_values "
            "parameter but the documentation says it should be store_cv_results. "
            "The parameter store_cv_values doesn't work correctly and raises an error. "
            "Need to fix parameter handling to support store_cv_results properly."
        ),
    },
    {
        "id": "matplotlib__matplotlib-24870",
        "repo_url": "https://github.com/matplotlib/matplotlib.git",
        "base_commit": "6091437be9776139d3672cde28a19cbe6c09dcd5",
        "gt_lines": 33,
        "problem": (
            "[ENH]: Auto-detect bool arrays passed to contour()\n\n"
            "When calling plt.contour(boolean_2d_array), the default levels don't "
            "work well for boolean data. The function should auto-detect boolean "
            "input arrays and set appropriate default levels=[0.5] so that the "
            "boundary line is drawn correctly without manual level specification."
        ),
    },
]

RUNS_PER_PROBLEM = 5

def _load_env():
    """Build child-process env from current env (which should have .env loaded)."""
    required = ["OPENROUTER_API_KEY", "NEO4J_PASSWORD", "OPENAI_API_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing env vars (source .env first): {', '.join(missing)}")
    return {**os.environ}


def prepare_repo(problem_id, repo_url, base_commit, run_id, with_memory):
    """Clone repo and checkout base commit."""
    suffix = "mem" if with_memory else "nomem"
    work_dir = BASE / f"{problem_id}" / f"{suffix}_{run_id}"

    if work_dir.exists():
        shutil.rmtree(work_dir)

    # Clone with shallow history
    master_dir = BASE / "repos" / problem_id.split("__")[0].replace("-", "_")
    if not master_dir.exists():
        master_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--bare", repo_url, str(master_dir)],
            check=True, capture_output=True, timeout=300,
        )

    work_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", str(master_dir), str(work_dir)],
        check=True, capture_output=True, timeout=120,
    )
    subprocess.run(
        ["git", "checkout", base_commit],
        cwd=str(work_dir), check=True, capture_output=True, timeout=30,
    )

    if with_memory:
        shutil.copy(str(OPENCODE_CONFIG), str(work_dir / "opencode.json"))

    return work_dir


def run_opencode(problem_id, problem_text, work_dir, run_id, with_memory):
    """Run opencode on a problem."""
    suffix = "mem" if with_memory else "nomem"
    out_file = BASE / f"{problem_id}" / f"result_{suffix}_{run_id}.jsonl"

    prompt = f"""You are a coding agent tasked with fixing a bug in a Python repository.

## Bug Report ({problem_id})

{problem_text}

## Instructions
1. Reproduce the bug to confirm it.
2. Find and fix the root cause in the source code.
3. Verify your fix works.
"""
    if with_memory:
        prompt += "\nIMPORTANT: You MUST call the query_memory MCP tool at least once before making any code changes."

    cmd = [
        "opencode", "run",
        "--dir", str(work_dir),
        "--format", "json",
        "--title", f"{problem_id}-{suffix}-{run_id}",
    ]
    if not with_memory:
        cmd.append("--pure")
    cmd.append(prompt)

    with open(out_file, "w") as f:
        result = subprocess.run(
            cmd, stdout=f, stderr=subprocess.STDOUT,
            env=_load_env(), timeout=600,
        )

    return {
        "problem_id": problem_id,
        "run_id": run_id,
        "with_memory": with_memory,
        "returncode": result.returncode,
        "output_file": str(out_file),
    }


def main():
    BASE.mkdir(parents=True, exist_ok=True)

    # Step 1: Clone all repos (sequential to avoid conflicts)
    print("=== Cloning repositories ===")
    repos_done = set()
    for p in PROBLEMS:
        repo_key = p["repo_url"]
        if repo_key not in repos_done:
            master = BASE / "repos" / p["id"].split("__")[0].replace("-", "_")
            if not master.exists():
                print(f"  Cloning {p['repo_url']}...")
                master.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["git", "clone", "--bare", p["repo_url"], str(master)],
                    check=True, timeout=600,
                )
            repos_done.add(repo_key)
    print(f"  {len(repos_done)} repos ready")

    # Step 2: Prepare all work directories
    print("\n=== Preparing work directories ===")
    tasks = []
    for p in PROBLEMS:
        for run_id in range(1, RUNS_PER_PROBLEM + 1):
            for with_memory in [True, False]:
                suffix = "mem" if with_memory else "nomem"
                work_dir = BASE / p["id"] / f"{suffix}_{run_id}"
                if work_dir.exists():
                    shutil.rmtree(work_dir)
                work_dir.parent.mkdir(parents=True, exist_ok=True)

                master = BASE / "repos" / p["id"].split("__")[0].replace("-", "_")
                subprocess.run(
                    ["git", "clone", "-q", str(master), str(work_dir)],
                    check=True, capture_output=True, timeout=120,
                )
                subprocess.run(
                    ["git", "checkout", "-q", p["base_commit"]],
                    cwd=str(work_dir), check=True, capture_output=True, timeout=30,
                )
                if with_memory:
                    shutil.copy(str(OPENCODE_CONFIG), str(work_dir / "opencode.json"))

                hint = p.get("hint", "")
                problem_text = p["problem"]
                if hint:
                    problem_text += f"\n\nHint: {hint}"

                tasks.append({
                    "problem_id": p["id"],
                    "problem_text": problem_text,
                    "work_dir": str(work_dir),
                    "run_id": run_id,
                    "with_memory": with_memory,
                })

    print(f"  {len(tasks)} work directories ready")

    # Step 3: Run all tasks in parallel (max 10 concurrent to avoid API rate limits)
    print(f"\n=== Running {len(tasks)} OpenCode tasks (max 10 concurrent) ===")
    results = []
    with ProcessPoolExecutor(max_workers=10) as executor:
        futures = {}
        for t in tasks:
            f = executor.submit(
                run_opencode,
                t["problem_id"], t["problem_text"],
                t["work_dir"], t["run_id"], t["with_memory"],
            )
            futures[f] = t

        for f in as_completed(futures):
            t = futures[f]
            try:
                r = f.result()
                suffix = "mem" if t["with_memory"] else "nomem"
                print(f"  Done: {t['problem_id']} {suffix}_{t['run_id']} (rc={r['returncode']})")
                results.append(r)
            except Exception as e:
                print(f"  FAIL: {t['problem_id']} - {e}")
                results.append({
                    "problem_id": t["problem_id"],
                    "run_id": t["run_id"],
                    "with_memory": t["with_memory"],
                    "error": str(e),
                })

    # Save results index
    with open(BASE / "run_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n=== Complete: {len(results)} runs finished ===")


if __name__ == "__main__":
    main()
