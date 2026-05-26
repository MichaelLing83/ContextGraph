"""Build enriched summaries for SWE-ContextBench Lite Experience instances.

Replaces the LLM-extracted summaries (median 66 chars — too short, weak
retrieval signal) with concatenated raw text:
    [REPO] {repo}
    PROBLEM:
    {problem_statement[:500]}
    GOLD PATCH:
    {patch[:1500]}

These ~2000-char summaries preserve all the keywords (file names, symbol
names, error strings) that the 3-channel retriever (cosine + BM25 + PPR)
needs to fire on related test instances. No LLM call, takes seconds.

Output matches scripts/build_pro_graph.py JSON shape so the existing embed
pipeline can ingest it:
  [
    {"repo": "...", "instance_id": "...", "strategies": [
        {"category": "summary", "rule_text": "<enriched ~2000-char text>"}
    ]},
    ...
  ]
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from huggingface_hub import hf_hub_download
import pandas as pd


def build_summary(repo: str, problem: str, patch: str,
                  problem_chars: int = 500, patch_chars: int = 1500) -> str:
    problem = (problem or "").strip()
    patch = (patch or "").strip()
    if len(problem) > problem_chars:
        problem = problem[:problem_chars] + "…"
    if len(patch) > patch_chars:
        patch = patch[:patch_chars] + "…"
    return (
        f"[REPO] {repo}\n\n"
        f"PROBLEM:\n{problem}\n\n"
        f"GOLD PATCH:\n{patch}"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="lite_experience",
                    choices=("lite_experience", "full_experience"))
    ap.add_argument("--output", required=True)
    ap.add_argument("--exclude-file", default=None,
                    help="JSON list of instance_ids to exclude (e.g. test set)")
    ap.add_argument("--problem-chars", type=int, default=500)
    ap.add_argument("--patch-chars", type=int, default=1500)
    ap.add_argument("--hf-repo", default="jiayuanz3/SWEContextBench")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    fname = {
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
        print(f"  excluded {before - len(df)} held-out, {len(df)} remain")
    if args.limit:
        df = df.head(args.limit)

    results = []
    char_lens = []
    for _, row in df.iterrows():
        text = build_summary(
            row["repo"], row.get("problem_statement", ""), row.get("patch", ""),
            args.problem_chars, args.patch_chars,
        )
        results.append({
            "instance_id": row["instance_id"],
            "repo": row["repo"],
            "strategies": [{"category": "summary", "rule_text": text}],
        })
        char_lens.append(len(text))

    results.sort(key=lambda r: r["instance_id"])
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(results, indent=2))

    char_lens.sort()
    n = len(char_lens)
    median = char_lens[n // 2] if n else 0
    print(f"\nWrote {args.output}")
    print(f"  {n} summaries, median {median} chars, "
          f"p10 {char_lens[n//10] if n else 0}, "
          f"p90 {char_lens[(n*9)//10] if n else 0}")
    if results:
        sample = results[0]["strategies"][0]["rule_text"]
        print(f"  sample ({len(sample)} chars):\n----\n{sample[:600]}\n----")


if __name__ == "__main__":
    main()
