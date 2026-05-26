#!/usr/bin/env python3
"""Extract reusable strategies from SWE-ContextBench experience-task gold patches.

Input: HuggingFace dataset jiayuanz3/SWEContextBench, split SWEContextBench_Lite_Experience
       (300 SWE-bench Lite instances, Python only, 12 repos).

For each experience task, prompt an LLM with (repo, problem_statement, gold patch) and
ask it to emit 3-5 reusable strategies. Output JSON in the format that
scripts/build_pro_graph.py consumes: a list of {repo, instance_id, strategies: [...]}.

Strategies are LLM abstractions of "what to do when you see a similar issue", not
patch fragments. The 99 related-task test set is NEVER touched in this stage.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from huggingface_hub import hf_hub_download
import pandas as pd
from openai import OpenAI


PROMPT = """You are reviewing a real GitHub bug-fix in {repo}.

PROBLEM STATEMENT (truncated):
{problem}

GOLD-PATCH DIFF (truncated, the engineering team's actual fix):
{patch}

Extract 3-5 REUSABLE strategies a future agent could apply on a *related* issue
in the same repository. Each strategy must be:
  - One sentence, imperative voice ("When X, do Y so that Z").
  - Specific enough to be actionable (mention file/module/API names when relevant).
  - Generic enough to apply beyond this exact bug (so future agents on a different
    but related issue in {repo} can use it).

Do NOT copy patch fragments. Output one strategy per line in this format:
[category] rule text

Allowed categories: error_handling, debugging, testing, code_navigation,
dependency, configuration, anti_pattern.

Begin:
"""

CATEGORIES = {
    "error_handling", "debugging", "testing", "code_navigation",
    "dependency", "configuration", "anti_pattern",
}


def truncate(text: str, max_chars: int) -> str:
    if text is None:
        return ""
    text = str(text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 12] + "\n…[trunc]"


def parse_response(content: str) -> list[dict[str, str]]:
    out = []
    for line in content.strip().splitlines():
        line = line.strip().lstrip("-*•").strip()
        if not line or not line.startswith("["):
            continue
        end = line.find("]")
        if end < 0:
            continue
        cat = line[1:end].strip().lower()
        rule = line[end + 1 :].strip()
        if not rule:
            continue
        if cat not in CATEGORIES:
            # Fall back to debugging for unknown categories
            cat = "debugging"
        out.append({"category": cat, "rule_text": rule})
    return out


def extract_one(
    client: OpenAI,
    model: str,
    instance: dict[str, Any],
    problem_chars: int,
    patch_chars: int,
) -> dict[str, Any]:
    prompt = PROMPT.format(
        repo=instance["repo"],
        problem=truncate(instance.get("problem_statement", ""), problem_chars),
        patch=truncate(instance.get("patch", ""), patch_chars),
    )
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=900,
                temperature=0.3,
            )
            content = resp.choices[0].message.content or ""
            strategies = parse_response(content)
            return {
                "instance_id": instance["instance_id"],
                "repo": instance["repo"],
                "strategies": strategies,
            }
        except Exception as exc:  # pragma: no cover (network)
            if attempt == 2:
                print(f"[{instance['instance_id']}] giving up after 3 attempts: {exc}", file=sys.stderr)
                return {
                    "instance_id": instance["instance_id"],
                    "repo": instance["repo"],
                    "strategies": [],
                    "error": str(exc),
                }
            time.sleep(1.5 * (attempt + 1))
    return {"instance_id": instance["instance_id"], "repo": instance["repo"], "strategies": []}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--split",
        default="lite",
        choices=("lite", "full"),
        help="lite = 300 experience tasks (SWE-bench Lite only); full = all 1,100 experience tasks",
    )
    ap.add_argument("--output", required=True, help="Output JSON path")
    ap.add_argument("--model", default=os.environ.get("EXTRACTOR_MODEL", "claude-sonnet-4-20250514"))
    ap.add_argument("--api-base", default=os.environ.get("OPENAI_API_BASE", "http://localhost:4000/v1"))
    ap.add_argument("--api-key", default=os.environ.get("LITELLM_MASTER_KEY", os.environ.get("OPENAI_API_KEY", "")))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None, help="Process only first N instances (smoke test)")
    ap.add_argument("--problem-chars", type=int, default=2000)
    ap.add_argument("--patch-chars", type=int, default=4000)
    ap.add_argument("--hf-repo", default="jiayuanz3/SWEContextBench")
    args = ap.parse_args()

    if not args.api_key:
        sys.exit("API key not set (LITELLM_MASTER_KEY or OPENAI_API_KEY).")

    fname = {
        "lite": "data/SWEContextBench_Lite_Experience.parquet",
        "full": "data/SWEContextBench_Experience.parquet",
    }[args.split]
    print(f"Downloading {fname} from {args.hf_repo}")
    pq = hf_hub_download(args.hf_repo, fname, repo_type="dataset")
    df = pd.read_parquet(pq)
    print(f"  loaded {len(df)} experience instances")

    if args.limit:
        df = df.head(args.limit)
        print(f"  limited to first {len(df)} (--limit)")

    client = OpenAI(base_url=args.api_base, api_key=args.api_key)
    instances = df[["repo", "instance_id", "problem_statement", "patch"]].to_dict("records")

    results: list[dict[str, Any]] = []
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {
            ex.submit(extract_one, client, args.model, inst, args.problem_chars, args.patch_chars): inst["instance_id"]
            for inst in instances
        }
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            done += 1
            if done % 10 == 0 or done == len(instances):
                rate = done / (time.time() - t0)
                print(f"  [{done}/{len(instances)}] {rate:.1f} inst/s")

    # Sort for determinism (output not strictly required to be sorted, but nice)
    results.sort(key=lambda r: r["instance_id"])

    total_strategies = sum(len(r["strategies"]) for r in results)
    by_repo: dict[str, int] = {}
    for r in results:
        by_repo[r["repo"]] = by_repo.get(r["repo"], 0) + len(r["strategies"])

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))

    print()
    print(f"Wrote {out_path}")
    print(f"  {len(results)} instances · {total_strategies} strategies "
          f"(avg {total_strategies / max(1, len(results)):.1f}/inst)")
    print("  by repo:")
    for repo, n in sorted(by_repo.items(), key=lambda kv: -kv[1]):
        print(f"    {repo}: {n}")


if __name__ == "__main__":
    main()
