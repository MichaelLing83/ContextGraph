#!/usr/bin/env python3
"""GPT-5.4 original-memory vs no-memory on 50 random problems."""

import json, os, subprocess, shutil
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE = Path(os.environ.get("FIFTY_TEST_DIR", "/tmp/swe-bench-test/fifty"))

# OpenCode configs generated at runtime to avoid hardcoded paths.
def _make_treatment_config():
    """Generate treatment config with REPO_ROOT resolved."""
    import json as _json
    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "model": "openrouter/openai/gpt-5.4",
        "provider": {"openrouter": {}},
        "mcp": {"contextgraph-memory": {
            "type": "local",
            "command": ["uv", "run", "--directory", str(REPO_ROOT),
                        "python", "tools/mcp_server/server.py"],
            "environment": {
                "NEO4J_URI": "bolt://localhost:7687",
                "NEO4J_USER": "neo4j",
                "NEO4J_PASSWORD": "{env:NEO4J_PASSWORD}",
                "OPENAI_API_KEY": "{env:OPENAI_API_KEY}",
                "OPENAI_API_BASE": "{env:OPENAI_API_BASE}",
                "EMBEDDING_MODEL": "text-embedding-3-large",
            },
            "enabled": True, "timeout": 30000,
        }},
    }
    out = BASE / "_generated_treatment_config.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    _json.dump(cfg, open(out, "w"), indent=2)
    return out

CONFIGS = {
    "original": None,  # set in main()
    "nomem": REPO_ROOT / "configs" / "opencode_gpt54_nomem.json",
}

# Problem list (50 random from verified_200, seed=2026, excluding 11 already tested)
PROBLEMS_FILE = REPO_ROOT / "results" / "case_studies" / "fifty_gpt54" / "problems.json"
with open(PROBLEMS_FILE) as f:
    PROBLEMS = json.load(f)

# Map repo to git url
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


def run_opencode(problem_id, problem_text, work_dir, group):
    out_file = BASE / problem_id / f"result_{group}.jsonl"
    prompt = f"""You are a coding agent tasked with fixing a bug in a Python repository.

## Bug Report ({problem_id})

{problem_text[:3000]}

## Instructions
1. Reproduce the bug to confirm it.
2. Find and fix the root cause in the source code.
3. Verify your fix works.
"""
    if group == "original":
        prompt += "\nIMPORTANT: You MUST call the query_memory MCP tool at least once before making any code changes."

    cmd = ["opencode", "run", "--dir", str(work_dir), "--format", "json",
           "--title", f"{problem_id}-gpt54-{group}"]
    if group == "nomem":
        cmd.append("--pure")
    cmd.append(prompt)

    with open(out_file, "w") as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT,
                                env={**os.environ}, timeout=900)
    return {"problem_id": problem_id, "group": group,
            "returncode": result.returncode}


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    CONFIGS["original"] = _make_treatment_config()

    # Clone repos
    print("=== Cloning ===")
    repos_done = set()
    for p in PROBLEMS:
        repo = p["repo"]
        if repo not in repos_done:
            repo_key = repo.replace("/", "_")
            master = BASE / "repos" / repo_key
            if not master.exists():
                url = REPO_URLS[repo]
                print(f"  Cloning {url}...")
                master.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["git", "clone", "--bare", url, str(master)],
                               check=True, timeout=600)
            repos_done.add(repo)
    print(f"  {len(repos_done)} repos ready")

    # Prepare 100 work dirs
    print("\n=== Preparing ===")
    tasks = []
    for p in PROBLEMS:
        for group in ["original", "nomem"]:
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
            shutil.copy(str(CONFIGS[group]), str(work_dir / "opencode.json"))
            # Place a minimal CLAUDE.md to prevent OpenCode early exit bug
            # (GPT-5.4 tries to read CLAUDE.md on startup; missing file causes
            # the agent to terminate after a single step)
            (work_dir / "CLAUDE.md").write_text(
                "# Project\nFix the bug described in the task prompt.\n"
            )
            # Protect CLAUDE.md and opencode.json from git clean/checkout
            exclude = work_dir / ".git" / "info" / "exclude"
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with open(exclude, "a") as ef:
                ef.write("\nCLAUDE.md\nopencode.json\n")
            tasks.append({"problem_id": p["id"], "problem_text": p["problem"],
                          "work_dir": str(work_dir), "group": group})
    print(f"  {len(tasks)} dirs ready")

    # Run (max 10 concurrent)
    print(f"\n=== Running {len(tasks)} tasks ===")
    results = []
    with ProcessPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(run_opencode, t["problem_id"], t["problem_text"],
                   t["work_dir"], t["group"]): t for t in tasks}
        for f in as_completed(futures):
            t = futures[f]
            try:
                r = f.result()
                print(f"  Done: {t['problem_id']} {t['group']} (rc={r['returncode']})")
                results.append(r)
            except Exception as e:
                print(f"  FAIL: {t['problem_id']} {t['group']} - {e}")
                results.append({"problem_id": t["problem_id"], "group": t["group"],
                                "error": str(e)})
    with open(BASE / "run_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n=== Done: {len(results)} ===")


if __name__ == "__main__":
    main()
