"""Sequential SWE-ContextBench runner with online memory ingestion.

This is the "real" SWE-ContextBench context-learning protocol: solve
instances one at a time in deterministic order, and after each instance
finishes, distill its trajectory into a handful of memory entries and
POST them to the memory server. Subsequent instances retrieve from the
augmented graph.

Differs from ``run_swectx.py run contextgraph`` in three ways:
  1. ``--num_workers 1`` is forced (sequential).
  2. Each instance is run in a separate ``sweagent run-batch`` invocation
     against a single-instance JSON, so the new memory is visible to the
     NEXT instance.
  3. After each instance, we (a) parse the .traj for problem_statement +
     submitted_patch, (b) call an LLM to extract 3-5 reusable strategies,
     and (c) POST each strategy to the server's /ingest_strategy endpoint
     with the instance's repo.

Usage:
    uv run python scripts/run_swectx_online.py \\
        --method contextgraph_online \\
        --server-url http://127.0.0.1:8021 \\
        --instances results/swectx/instances.json

Prerequisites:
    - run_swectx.py select --n 30   (or equivalent — instances.json present)
    - contextgraph_pro_server with /ingest_strategy endpoint running
    - LITELLM_MASTER_KEY + OPENAI_API_BASE env set
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from openai import OpenAI


PROJECT_ROOT = Path(__file__).parent.parent
RESULTS_DIR = PROJECT_ROOT / "results" / "swectx"
CONFIGS_DIR = PROJECT_ROOT / "configs"
# Linux Docker bridge gateway — overridable for non-default Docker setups.
DOCKER_HOST_IP = os.environ.get("DOCKER_HOST_IP", "172.17.0.1")
COST_LIMIT = 3.0

EXTRACT_PROMPT = """You just watched a coding agent attempt the following GitHub bug fix in {repo}.

PROBLEM STATEMENT (truncated):
{problem}

THE AGENT'S SUBMITTED PATCH (may or may not be the correct fix):
{patch}

Extract 3-5 REUSABLE strategies a future agent could apply on a *related* issue
in the same repository. Rules:
  - One sentence per strategy, imperative voice ("When X, do Y so that Z").
  - Mention file/module/API names when relevant.
  - Generic enough to apply beyond this exact bug.
  - If the patch looks empty / wrong / off-target, you may still emit
    diagnostic strategies framed as "When you see error E, check ... in {repo}".

Output one strategy per line in this format:
[category] rule text

Allowed categories: error_handling, debugging, testing, code_navigation,
dependency, configuration, anti_pattern.
"""


def truncate(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[: n - 12] + "\n…[trunc]"


def parse_strategies(raw: str):
    out = []
    for line in raw.strip().splitlines():
        line = line.strip().lstrip("-*•").strip()
        if not line.startswith("["):
            continue
        end = line.find("]")
        if end < 0:
            continue
        cat = line[1:end].strip().lower()
        text = line[end + 1:].strip()
        if text:
            out.append({"category": cat, "rule_text": text})
    return out


def extract_strategies(client, model, repo, problem, patch):
    prompt = EXTRACT_PROMPT.format(
        repo=repo,
        problem=truncate(problem, 2000),
        patch=truncate(patch, 4000),
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=900,
            temperature=0.3,
        )
        return parse_strategies(resp.choices[0].message.content or "")
    except Exception as exc:
        print(f"[WARN] extract failed: {exc}", file=sys.stderr)
        return []


def post_strategy(server_url, repo, rule_text, prefix):
    body = json.dumps({
        "text": rule_text,
        "repo": repo,
        "prefix": prefix,
        "section": "REPO_SPECIFIC",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{server_url.rstrip('/')}/ingest_strategy",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def generate_memory_config(port: int, output: Path) -> Path:
    """Same logic as run_swectx.generate_config('contextgraph')."""
    treatment = (CONFIGS_DIR / "swe_agent_treatment.yaml").read_text()
    content = treatment.replace(
        "      NEO4J_URI: bolt://neo4j-contextgraph:7687\n"
        "      NEO4J_USER: neo4j\n"
        "      NEO4J_PASSWORD: INJECTED_AT_RUNTIME",
        f"      MEMORY_SERVER_URL: http://{DOCKER_HOST_IP}:{port}",
    )
    content = content.replace(
        "per_instance_cost_limit: 0",
        f"per_instance_cost_limit: {COST_LIMIT}",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content)
    return output


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--method", default="contextgraph_online",
                    help="Output dir name under results/swectx/")
    ap.add_argument("--server-url", default="http://127.0.0.1:8021",
                    help="Memory server (must expose /ingest_strategy)")
    ap.add_argument("--server-port", type=int, default=8021,
                    help="Memory server port the SWE-agent Docker container talks to")
    ap.add_argument("--instances", default=str(RESULTS_DIR / "instances.json"))
    ap.add_argument("--model", default=os.environ.get("EXTRACTOR_MODEL", "claude-sonnet-4-20250514"))
    ap.add_argument("--api-base", default=os.environ.get("OPENAI_API_BASE", "http://localhost:4000/v1"))
    ap.add_argument("--api-key", default=os.environ.get("LITELLM_MASTER_KEY", os.environ.get("OPENAI_API_KEY", "")))
    ap.add_argument("--skip-ingest", action="store_true",
                    help="Run sequentially but do NOT ingest after each instance (control)")
    args = ap.parse_args()

    if not args.api_key:
        sys.exit("Missing LITELLM_MASTER_KEY / OPENAI_API_KEY")

    instances = json.load(open(args.instances))
    method_dir = RESULTS_DIR / args.method
    output_dir = method_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Running {len(instances)} instances sequentially. method={args.method} skip_ingest={args.skip_ingest}")

    config_path = generate_memory_config(args.server_port, method_dir / "config.yaml")
    print(f"Config: {config_path}")

    client = OpenAI(base_url=args.api_base, api_key=args.api_key)

    ingest_log = []
    for i, inst in enumerate(instances, 1):
        iid = inst["instance_id"]
        repo = inst.get("repo_name") or inst.get("repo") or "testbed"
        # The instances.json carries repo_name="testbed" (PreExistingRepoConfig);
        # the real org/name repo is parseable from the instance_id.
        if "__" in iid:
            org, rest = iid.split("__", 1)
            repo_for_memory = f"{org}/{rest.split('-', 1)[0]}"
        else:
            repo_for_memory = None
        print(f"\n{'='*72}\n[{i}/{len(instances)}] {iid}   repo_for_memory={repo_for_memory}\n{'='*72}")

        # Write single-instance JSON
        single_path = method_dir / f"single_{iid}.json"
        single_path.write_text(json.dumps([inst]))

        t0 = time.time()
        cmd = [
            sys.executable, "-m", "sweagent", "run-batch",
            "--config", str(config_path),
            "--instances.type", "file",
            "--instances.path", str(single_path),
            "--output_dir", str(output_dir),
            "--num_workers", "1",
            "--instances.deployment.type", "docker",
            "--instances.deployment.python_standalone_dir", "",
            '--instances.deployment.docker_args=["--add-host=host.docker.internal:host-gateway"]',
        ]
        # List-form sweagent invocation; iid + paths originate from
        # instances.json under the project's results/ tree.
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT))  # nosec B603  # nosem
        print(f"[{iid}] sweagent exit={proc.returncode} took {time.time()-t0:.1f}s")

        # Parse traj for problem statement + patch
        traj_path = output_dir / iid / f"{iid}.traj"
        if not traj_path.exists():
            print(f"[{iid}] WARN: no traj at {traj_path}; nothing to ingest")
            continue
        try:
            traj = json.load(open(traj_path))
        except Exception as e:
            print(f"[{iid}] WARN: bad traj: {e}")
            continue

        patch = traj.get("info", {}).get("submission", "") or ""
        problem = inst.get("problem_statement", "") or ""

        if args.skip_ingest:
            print(f"[{iid}] (skip ingest)")
            ingest_log.append({"instance_id": iid, "patch_len": len(patch), "ingested": 0})
            continue
        if not patch.strip():
            print(f"[{iid}] empty patch — skip ingest")
            ingest_log.append({"instance_id": iid, "patch_len": 0, "ingested": 0})
            continue

        strategies = extract_strategies(client, args.model, repo_for_memory, problem, patch)
        n_ok = 0
        for s in strategies:
            try:
                r = post_strategy(args.server_url, repo_for_memory, s["rule_text"],
                                  s.get("category", "psw"))
                if r.get("ok"):
                    n_ok += 1
            except Exception as e:
                print(f"  ingest err: {e}")
        print(f"[{iid}] ingested {n_ok}/{len(strategies)} strategies for repo={repo_for_memory}")
        ingest_log.append({"instance_id": iid, "patch_len": len(patch),
                            "ingested": n_ok, "extracted": len(strategies)})

    out = method_dir / "ingest_log.json"
    out.write_text(json.dumps(ingest_log, indent=2))
    print(f"\nWrote {out}")
    print(f"Total ingested: {sum(e['ingested'] for e in ingest_log)}")


if __name__ == "__main__":
    main()
