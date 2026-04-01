#!/usr/bin/env python3
"""Pipeline runner: OpenCode + SWE-bench verify in parallel.

For each problem pair (treatment + control), as soon as both finish:
1. Extract git diffs
2. Submit to SWE-bench verify queue (background thread)

This overlaps OpenCode execution with Docker-based verification.
"""

import json, os, subprocess, shutil, time, sys, threading, queue
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE = Path(os.environ.get("PIPELINE_DIR", "/tmp/swe-bench-test/pipeline"))
LOG_DIR = BASE / "logs"
STATUS_FILE = BASE / "status.json"
VERIFY_DIR = BASE / "verify"

REPO_URLS = {
    "astropy/astropy": "https://github.com/astropy/astropy.git",
    "django/django": "https://github.com/django/django.git",
    "matplotlib/matplotlib": "https://github.com/matplotlib/matplotlib.git",
    "psf/requests": "https://github.com/psf/requests.git",
    "pydata/xarray": "https://github.com/pydata/xarray.git",
    "pytest-dev/pytest": "https://github.com/pytest-dev/pytest.git",
    "scikit-learn/scikit-learn": "https://github.com/scikit-learn/scikit-learn.git",
    "sphinx-doc/sphinx": "https://github.com/sphinx-doc/sphinx.git",
    "sympy/sympy": "https://github.com/sympy/sympy.git",
}

# Configurable
TREATMENT_CONFIG = Path(os.environ.get(
    "TREATMENT_CONFIG", str(REPO_ROOT / "configs" / "opencode_gpt54_da_combo.json")))
NOMEM_CONFIG = Path(os.environ.get(
    "NOMEM_CONFIG", str(REPO_ROOT / "configs" / "opencode_gpt54_nomem.json")))
PROBLEMS_FILE = Path(os.environ.get(
    "PROBLEMS_FILE", str(REPO_ROOT / "results" / "case_studies" / "fifty_gpt54" / "problems.json")))
OPENCODE_WORKERS = int(os.environ.get("OPENCODE_WORKERS", "1"))  # sequential by default
VERIFY_WORKERS = int(os.environ.get("VERIFY_WORKERS", "4"))
TIMEOUT = int(os.environ.get("OPENCODE_TIMEOUT", "900"))
CONSECUTIVE_FAIL_LIMIT = int(os.environ.get("FAIL_LIMIT", "5"))


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(BASE / "run.log", "a") as f:
        f.write(line + "\n")


def update_status(status):
    with open(STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)


# ── Verify Queue ──────────────────────────────────────────────

verify_queue = queue.Queue()
verify_results = []
verify_lock = threading.Lock()


def verify_worker():
    """Background thread: consume (pid, group, predictions) from queue, run swebench."""
    while True:
        item = verify_queue.get()
        if item is None:
            break
        pid, pred_file, run_id = item
        try:
            log(f"[VERIFY] Starting: {run_id}")
            report_dir = VERIFY_DIR / run_id
            report_dir.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["uv", "run", "python", "-m", "swebench.harness.run_evaluation",
                 "--dataset_name", "princeton-nlp/SWE-bench_Verified",
                 "--split", "test",
                 "--predictions_path", str(pred_file),
                 "--max_workers", str(VERIFY_WORKERS),
                 "--run_id", run_id,
                 "--report_dir", str(report_dir),
                 "--cache_level", "instance"],
                capture_output=True, text=True, timeout=1800,
                cwd=str(REPO_ROOT),
            )
            # Find report file
            reports = list(Path(".").glob(f"*.{run_id}.json")) + list(report_dir.glob("*.json"))
            resolved = []
            for rf in reports:
                try:
                    d = json.load(open(rf))
                    resolved = d.get("resolved_ids", [])
                except:
                    pass
            with verify_lock:
                verify_results.append({"run_id": run_id, "resolved": resolved})
            log(f"[VERIFY] Done: {run_id} — resolved {len(resolved)}: {resolved}")
        except Exception as e:
            log(f"[VERIFY] Error: {run_id} — {e}")
            with verify_lock:
                verify_results.append({"run_id": run_id, "error": str(e)})
        verify_queue.task_done()


# ── OpenCode Runner ───────────────────────────────────────────

def setup_work_dir(p, group, config_path):
    work_dir = BASE / p["id"] / group
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.parent.mkdir(parents=True, exist_ok=True)

    repo_key = p["repo"].replace("/", "_")
    master = BASE / "repos" / repo_key
    subprocess.run(["git", "clone", "-q", str(master), str(work_dir)],
                   check=True, capture_output=True, timeout=120)
    subprocess.run(["git", "checkout", "-q", p["base_commit"]],
                   cwd=str(work_dir), check=True, capture_output=True, timeout=30)
    shutil.copy(str(config_path), str(work_dir / "opencode.json"))
    (work_dir / "CLAUDE.md").write_text("# Project\nFix the bug described in the task prompt.\n")
    exclude = work_dir / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with open(exclude, "a") as ef:
        ef.write("\nCLAUDE.md\nopencode.json\n")
    return work_dir


def run_opencode(p, group, is_treatment):
    pid = p["id"]
    work_dir = BASE / pid / group
    out_file = BASE / pid / f"result_{group}.jsonl"

    prompt = (f"You are a coding agent tasked with fixing a bug.\n\n"
              f"## Bug Report ({pid})\n\n{p['problem'][:3000]}\n\n"
              f"## Instructions\n1. Find and fix the root cause.\n2. Verify your fix works.\n")
    if is_treatment:
        prompt += "\nIMPORTANT: Call the query_memory MCP tool before making code changes."

    cmd = ["opencode", "run", "--dir", str(work_dir), "--format", "json",
           "--title", f"{pid}-{group}"]
    if not is_treatment:
        cmd.append("--pure")
    cmd.append(prompt)

    start = time.time()
    with open(out_file, "w") as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE,
                                env={**os.environ}, timeout=TIMEOUT)
    elapsed = time.time() - start

    # Health check
    events = mcp = edits = 0
    try:
        for line in open(out_file):
            if line.strip():
                events += 1
                if 'query_memory' in line: mcp += 1
                if '"tool":"edit"' in line: edits += 1
    except: pass

    diff = subprocess.run(["git", "diff", "HEAD", "--stat"],
                          cwd=str(work_dir), capture_output=True, text=True).stdout.strip()

    return {
        "problem_id": pid, "group": group, "returncode": result.returncode,
        "elapsed": round(elapsed, 1), "events": events, "mcp_called": mcp > 0,
        "has_patch": bool(diff), "early_exit": elapsed < 10,
    }


def extract_diff(p, group):
    """Extract git diff as a SWE-bench prediction."""
    work_dir = BASE / p["id"] / group
    diff = subprocess.run(["git", "diff", "HEAD"], cwd=str(work_dir),
                          capture_output=True, text=True).stdout.strip()
    return {
        "instance_id": p["id"],
        "model_name_or_path": f"pipeline_{group}",
        "model_patch": diff + "\n" if diff else "",
    }


# ── Main ──────────────────────────────────────────────────────

def main():
    BASE.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    VERIFY_DIR.mkdir(parents=True, exist_ok=True)

    with open(PROBLEMS_FILE) as f:
        problems = json.load(f)

    # Clone repos
    log("=== Cloning repos ===")
    for p in problems:
        repo_key = p["repo"].replace("/", "_")
        master = BASE / "repos" / repo_key
        if not master.exists():
            master.parent.mkdir(parents=True, exist_ok=True)
            url = REPO_URLS[p["repo"]]
            log(f"  Cloning {url}...")
            subprocess.run(["git", "clone", "--bare", url, str(master)],
                           check=True, timeout=600)

    # Start verify worker thread
    verify_thread = threading.Thread(target=verify_worker, daemon=True)
    verify_thread.start()

    # Run problems
    log(f"=== Running {len(problems)} problems ===")
    results = []
    status = {"total": len(problems) * 2, "completed": 0, "early_exits": 0,
              "errors": [], "verified": 0}
    consecutive_failures = 0
    batch_preds = {"treatment": [], "nomem": []}

    for i, p in enumerate(problems):
        pid = p["id"]
        pair_results = {}

        for group, config, is_treatment in [
            ("treatment", TREATMENT_CONFIG, True),
            ("nomem", NOMEM_CONFIG, False),
        ]:
            log(f"[{i+1}/{len(problems)}] {pid} {group} — starting")
            try:
                setup_work_dir(p, group, config)
                r = run_opencode(p, group, is_treatment)
                results.append(r)
                pair_results[group] = r

                flags = ""
                if r["early_exit"]: flags += " EARLY_EXIT"; status["early_exits"] += 1; consecutive_failures += 1
                if is_treatment and not r["mcp_called"]: flags += " NO_MCP"
                if r["has_patch"]: flags += " PATCH"
                log(f"[{i+1}/{len(problems)}] {pid} {group} — {r['elapsed']}s, {r['events']} events{flags}")
                if not r["early_exit"]: consecutive_failures = 0

            except Exception as e:
                log(f"[{i+1}/{len(problems)}] {pid} {group} — ERROR: {e}")
                results.append({"problem_id": pid, "group": group, "error": str(e)})
                status["errors"].append(f"{pid}_{group}: {str(e)[:100]}")
                consecutive_failures += 1

            status["completed"] += 1
            update_status(status)

            if consecutive_failures >= CONSECUTIVE_FAIL_LIMIT:
                log(f"ABORT: {consecutive_failures} consecutive failures")
                break

        if consecutive_failures >= CONSECUTIVE_FAIL_LIMIT:
            break

        # After both groups finish for this problem, submit to verify
        for group in ["treatment", "nomem"]:
            pred = extract_diff(p, group)
            batch_preds[group].append(pred)

        # Submit verify every 5 problems (batch for efficiency)
        if (i + 1) % 5 == 0 or i == len(problems) - 1:
            batch_num = (i + 1) // 5
            for group in ["treatment", "nomem"]:
                if batch_preds[group]:
                    pred_file = VERIFY_DIR / f"preds_{group}_batch{batch_num}.json"
                    with open(pred_file, "w") as f:
                        json.dump(batch_preds[group], f, indent=2)
                    verify_queue.put((None, pred_file, f"v5_{group}_b{batch_num}"))
                    batch_preds[group] = []
            log(f"[VERIFY] Submitted batch {batch_num} to verify queue")

    # Wait for verify to finish
    log("=== Waiting for verify queue to drain ===")
    verify_queue.put(None)  # poison pill
    verify_thread.join(timeout=3600)

    # Save all results
    with open(BASE / "run_results.json", "w") as f:
        json.dump(results, f, indent=2)
    with open(BASE / "verify_results.json", "w") as f:
        json.dump(verify_results, f, indent=2)
    update_status(status)

    # Summary
    ok = [r for r in results if not r.get("error") and not r.get("early_exit")]
    log(f"\n=== DONE: {len(results)} runs, {len(ok)} normal, "
        f"{status['early_exits']} early, {len(status['errors'])} errors, "
        f"{len(verify_results)} verified ===")


if __name__ == "__main__":
    main()
