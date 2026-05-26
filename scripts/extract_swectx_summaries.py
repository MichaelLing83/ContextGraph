"""Extract paper-style narrative summaries from SWE-ContextBench experience instances.

Mirrors the SWE-ContextBench paper's "Summary" memory format (§3.3, ~217
tokens per instance, narrative style). One summary per instance, not 3-5
bulleted strategies. Built from (problem_statement, gold_patch) of training
instances ONLY — strict held-out from the test set.

Output JSON shape matches scripts/build_pro_graph.py so the existing
embed-and-insert pipeline can ingest it:
  [
    {"repo": "...", "instance_id": "...", "strategies": [
        {"category": "summary", "rule_text": "<~250-token narrative>"}
    ]},
    ...
  ]

The narrative covers: what went wrong (1 sentence), what files / functions
were touched, the fix logic, and 1-2 verification tests. Format follows the
paper's intent ("final summary paragraph of corresponding trajectory") even
though we are working from gold patches rather than real agent trajectories
(see MANIFEST caveat).
"""
from __future__ import annotations
import argparse, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from huggingface_hub import hf_hub_download
import pandas as pd
from openai import OpenAI


PROMPT = """You are summarising how a coding agent could solve a GitHub bug-fix in {repo}.

PROBLEM STATEMENT (truncated):
{problem}

THE HUMAN FIX FROM THE MERGED PR (truncated):
{patch}

Write ONE narrative paragraph (~250 tokens) that a future agent could read to solve
a related issue in {repo}. Cover, in order:
  1. What was wrong (one sentence in plain English, no code).
  2. Which files / classes / functions were touched and why (use real names).
  3. The concrete logic of the fix (what condition was added, what was renamed,
     what assertion was relaxed, etc.).
  4. How the fix was verified (which test files / fixtures changed, what edge
     cases now pass).

Strict rules:
  - Plain narrative prose, NOT bullet points, NOT code blocks.
  - Keep it under 350 tokens — concise is better than complete.
  - Refer to specific symbols by name; the future agent needs them to grep with.
  - Do not say "this PR" or "the patch" — write as engineering notes a teammate
    would leave for someone hitting the same problem six months later.

Begin:
"""


def truncate(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[: n - 12] + "\n…[trunc]"


def summarise_one(client, model, instance, problem_chars, patch_chars):
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
                max_tokens=600,
                temperature=0.3,
            )
            text = (resp.choices[0].message.content or "").strip()
            # collapse any list-y formatting into one paragraph
            text = " ".join(line.strip().lstrip("-*•") for line in text.splitlines() if line.strip())
            if not text:
                continue
            return {
                "instance_id": instance["instance_id"],
                "repo": instance["repo"],
                "strategies": [{"category": "summary", "rule_text": text}],
            }
        except Exception as exc:
            if attempt == 2:
                print(f"[{instance['instance_id']}] giving up: {exc}", file=sys.stderr)
                return {"instance_id": instance["instance_id"],
                        "repo": instance["repo"], "strategies": []}
            time.sleep(1.5 * (attempt + 1))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="lite_train",
                    choices=("lite_train", "lite_experience", "full_experience"))
    ap.add_argument("--output", required=True)
    ap.add_argument("--exclude-file", default=None,
                    help="JSON list of instance_ids to exclude (e.g. test set)")
    ap.add_argument("--model", default=os.environ.get("EXTRACTOR_MODEL", "claude-sonnet-4-20250514"))
    ap.add_argument("--api-base", default=os.environ.get("OPENAI_API_BASE", "http://localhost:4000/v1"))
    ap.add_argument("--api-key", default=os.environ.get("LITELLM_MASTER_KEY", os.environ.get("OPENAI_API_KEY", "")))
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--problem-chars", type=int, default=2000)
    ap.add_argument("--patch-chars", type=int, default=4000)
    ap.add_argument("--hf-repo", default="jiayuanz3/SWEContextBench")
    args = ap.parse_args()

    if not args.api_key:
        sys.exit("no API key")

    fname = {
        "lite_train":      "data/SWEContextBench_Lite_Experience.parquet",
        "lite_experience": "data/SWEContextBench_Lite_Experience.parquet",
        "full_experience": "data/SWEContextBench_Experience.parquet",
    }[args.source]
    print(f"Loading {fname} from {args.hf_repo}")
    pq = hf_hub_download(args.hf_repo, fname, repo_type="dataset")
    df = pd.read_parquet(pq)
    print(f"  loaded {len(df)} instances")

    if args.exclude_file:
        excl = set(json.load(open(args.exclude_file)))
        before = len(df)
        df = df[~df["instance_id"].isin(excl)]
        print(f"  excluded {before - len(df)} (held-out test set), {len(df)} remain")
    if args.limit:
        df = df.head(args.limit)

    client = OpenAI(base_url=args.api_base, api_key=args.api_key)
    instances = df[["repo", "instance_id", "problem_statement", "patch"]].to_dict("records")

    results = []
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(summarise_one, client, args.model, inst,
                             args.problem_chars, args.patch_chars): inst["instance_id"]
                   for inst in instances}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)
            done += 1
            if done % 25 == 0 or done == len(instances):
                rate = done / (time.time() - t0)
                eta = (len(instances) - done) / rate if rate else 0
                print(f"  [{done}/{len(instances)}] {rate:.1f}/s eta {eta:.0f}s")

    results.sort(key=lambda r: r["instance_id"])
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(results, indent=2))
    total = sum(len(r["strategies"]) for r in results)
    print(f"\nWrote {args.output}")
    print(f"  {len(results)} instances, {total} summaries (avg {total/max(1,len(results)):.2f}/inst)")
    if results:
        sample = results[0]["strategies"][0]["rule_text"]
        print(f"  sample summary ({len(sample)} chars):\n    {sample[:400]}")


if __name__ == "__main__":
    main()
