"""Orchestrate SWE-ContextBench evaluation: no_memory vs contextgraph.

SWE-ContextBench (Zhu et al. 2026, arXiv:2602.08316) provides 99 related-task
instances (Lite split) on Python repositories. Each related task shares context
with one or more "experience" tasks via real GitHub issue/PR cross-references.
This script runs SWE-agent on the 99 related instances under up to four memory
conditions and writes predictions in SWE-bench harness format for evaluation
with `swebench.harness.run_evaluation` on the standard SWE-bench Lite dataset.

Methods:
    no_memory       — vanilla SWE-agent, no query_memory tool.
    contextgraph    — Neo4j graph + 3-channel retrieval over strategies extracted
                      from the 300 experience tasks (the test set is held out).
    faiss           — flat FAISS over the same 300-instance strategy corpus.
    agentkb         — Agent-KB-style TF-IDF+semantic over the same corpus.

Usage:
    uv run python scripts/run_swectx.py select          # cache 99-related subset
    uv run python scripts/run_swectx.py select --n 20   # dev signal: first 20
    uv run python scripts/run_swectx.py run no_memory
    uv run python scripts/run_swectx.py run contextgraph
    uv run python scripts/run_swectx.py verify

Prerequisites:
    - docker compose up -d (LiteLLM proxy)
    - For contextgraph: a Neo4j instance populated by
        scripts/extract_swectx_strategies.py + scripts/build_pro_graph.py,
      and `scripts/baselines/contextgraph_pro_server.py` serving on the port
      indicated by --port (default 8011).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(help="SWE-ContextBench (Lite Related) runner")

PROJECT_ROOT = Path(__file__).parent.parent
CONFIGS_DIR = PROJECT_ROOT / "configs"
RESULTS_DIR = PROJECT_ROOT / "results" / "swectx"
SUBSET_FILE = RESULTS_DIR / "selected_instances.json"
INSTANCES_FILE = RESULTS_DIR / "instances.json"

# Linux Docker bridge gateway — overridable for non-default Docker setups.
DOCKER_HOST_IP = os.environ.get("DOCKER_HOST_IP", "172.17.0.1")

# Per-instance cost cap. SWE-bench Lite is Python-only / smaller codebases than
# Pro, so a $3 cap is plenty for the baseline to converge. Memory methods use
# the same cap; budgets are symmetric here (unlike Pro).
COST_LIMIT = 3.0

# Memory server ports (8021+ range — avoids clash with existing Verified/Pro
# servers on 8001-8013 on the eval host).
CONTEXTGRAPH_SWECTX_PORT = 8021         # graph from 200-Lite-train (held-out)
FAISS_SWECTX_PORT = 8022                # FAISS over the same 1,000 strategies
AGENTKB_SWECTX_PORT = 8023              # Agent-KB over the same 1,000 strategies
CONTEXTGRAPH_VERIFIED_PORT = 8024       # graph from the Verified 1,795-trajectory corpus
                                        # (transfer test — out-of-domain memory)

METHODS = ("no_memory", "contextgraph", "contextgraph_verified", "contextgraph_merged", "faiss", "agentkb")


# ---------------------------------------------------------------------------
# Subset selection
# ---------------------------------------------------------------------------


@app.command()
def select(
    n: Optional[int] = typer.Option(None, help="Take first N instances (default: all 99)"),
    hf_repo: str = typer.Option("jiayuanz3/SWEContextBench"),
    split_file: str = typer.Option(
        "data/SWEContextBench_Related_Lite.parquet",
        help="Parquet file in the HF dataset to load",
    ),
):
    """Cache the related-task subset and write a SWE-agent batch instances file."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import hf_hub_download
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        typer.echo(f"ERROR: {exc}. uv pip install huggingface_hub pandas pyarrow")
        raise typer.Exit(1)

    pq = hf_hub_download(hf_repo, split_file, repo_type="dataset")
    df = pd.read_parquet(pq)
    typer.echo(f"Loaded {len(df)} related-task instances from {split_file}")

    if n is not None:
        df = df.head(n)
        typer.echo(f"Limited to first {len(df)}")

    instance_ids = df["instance_id"].tolist()

    # SWE-agent batch-instance format. SWE-bench publishes per-instance Docker
    # images under `swebench/sweb.eval.x86_64.<slug>:latest`, where the slug
    # replaces every `__` in the instance_id with `_1776_` (Docker tag rules
    # disallow consecutive underscores in some contexts). Without this the
    # image pull fails on every Lite instance.
    #
    # `repo_name` is interpreted by SimpleBatchInstance (sweagent/run/batch_instances.py
    # lines 114-121) — if it contains `/` it triggers LocalRepoConfig and SWE-agent
    # tries to find the repo on the HOST filesystem. We want the image's preexisting
    # /testbed repo, so we pass the literal string "testbed" (no slash).
    instances = []
    for _, row in df.iterrows():
        slug = row["instance_id"].replace("__", "_1776_")
        instances.append({
            "instance_id": row["instance_id"],
            "image_name": f"swebench/sweb.eval.x86_64.{slug}:latest",
            "problem_statement": row["problem_statement"],
            "repo_name": "testbed",
            "base_commit": row["base_commit"],
        })

    SUBSET_FILE.write_text(json.dumps({
        "dataset": hf_repo,
        "split": split_file,
        "n": len(instance_ids),
        "selected_at": datetime.now().isoformat(),
        "instance_ids": instance_ids,
    }, indent=2))
    INSTANCES_FILE.write_text(json.dumps(instances, indent=2))

    by_repo: dict[str, int] = {}
    for inst in instances:
        by_repo[inst["repo_name"]] = by_repo.get(inst["repo_name"], 0) + 1

    typer.echo(f"\nSubset cached: {SUBSET_FILE}")
    typer.echo(f"Instances:     {INSTANCES_FILE}")
    typer.echo("\nRepo distribution:")
    for repo, ct in sorted(by_repo.items(), key=lambda kv: -kv[1]):
        typer.echo(f"  {repo:35s} {ct}")


# ---------------------------------------------------------------------------
# Config generation (templated after run_swebench_pro.py)
# ---------------------------------------------------------------------------


def _patch_cost(content: str) -> str:
    return content.replace(
        "per_instance_cost_limit: 0",
        f"per_instance_cost_limit: {COST_LIMIT}",
    )


def _generate_control_config(output: Path) -> None:
    template = (CONFIGS_DIR / "swe_agent_control.yaml").read_text()
    output.write_text(_patch_cost(template))


def _generate_memory_config(output: Path, port: int) -> None:
    treatment = (CONFIGS_DIR / "swe_agent_treatment.yaml").read_text()
    # Swap the Neo4j env block for an HTTP MEMORY_SERVER_URL so the wrapper
    # talks to the server instead of bolt://.
    treatment = treatment.replace(
        "      NEO4J_URI: bolt://neo4j-contextgraph:7687\n"
        "      NEO4J_USER: neo4j\n"
        "      NEO4J_PASSWORD: INJECTED_AT_RUNTIME",
        f"      MEMORY_SERVER_URL: http://{DOCKER_HOST_IP}:{port}",
    )
    output.write_text(_patch_cost(treatment))


def generate_config(method: str) -> Path:
    method_dir = RESULTS_DIR / method
    method_dir.mkdir(parents=True, exist_ok=True)
    output = method_dir / "config.yaml"
    if method == "no_memory":
        _generate_control_config(output)
    elif method == "contextgraph":
        _generate_memory_config(output, CONTEXTGRAPH_SWECTX_PORT)
    elif method == "contextgraph_verified":
        _generate_memory_config(output, CONTEXTGRAPH_VERIFIED_PORT)
    elif method == "contextgraph_merged":
        # Same port as contextgraph (8021) — the underlying Neo4j 7694 has
        # been augmented in-place with the Verified PlaybookEntries, so the
        # existing server serves the merged 9,910-entry corpus. Distinct
        # method name keeps the output dir + analysis separate.
        _generate_memory_config(output, CONTEXTGRAPH_SWECTX_PORT)
    elif method == "faiss":
        _generate_memory_config(output, FAISS_SWECTX_PORT)
    elif method == "agentkb":
        _generate_memory_config(output, AGENTKB_SWECTX_PORT)
    else:
        raise typer.Exit(f"Unknown method: {method}")
    return output


# ---------------------------------------------------------------------------
# Run a method
# ---------------------------------------------------------------------------


@app.command()
def run(
    method: str = typer.Argument(..., help=f"One of {METHODS}"),
    n_workers: int = typer.Option(2, help="Parallel SWE-agent workers"),
):
    """Run SWE-agent on the cached subset under the given method."""
    if method not in METHODS:
        typer.echo(f"ERROR: method must be one of {METHODS}")
        raise typer.Exit(1)
    if not INSTANCES_FILE.exists():
        typer.echo(f"ERROR: no subset cached. Run `select` first.")
        raise typer.Exit(1)

    config_path = generate_config(method)
    output_dir = RESULTS_DIR / method / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    typer.echo(f"\n{'='*70}")
    typer.echo(f"Method: {method}")
    typer.echo(f"Config: {config_path}")
    typer.echo(f"Output: {output_dir}")
    typer.echo(f"{'='*70}\n")

    cmd = [
        sys.executable, "-m", "sweagent", "run-batch",
        "--config", str(config_path),
        "--instances.type", "file",
        "--instances.path", str(INSTANCES_FILE),
        "--output_dir", str(output_dir),
        "--num_workers", str(n_workers),
        "--instances.deployment.type", "docker",
        "--instances.deployment.python_standalone_dir", "",
        '--instances.deployment.docker_args=["--add-host=host.docker.internal:host-gateway"]',
    ]
    typer.echo(" ".join(cmd))
    # List-form sweagent invocation; no shell interpretation.
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT))  # nosec B603  # nosem
    raise typer.Exit(proc.returncode)


# ---------------------------------------------------------------------------
# Verification (SWE-bench Lite harness)
# ---------------------------------------------------------------------------


@app.command()
def verify(
    methods: Optional[str] = typer.Option(None, help="Comma-separated; default = all"),
    max_workers: int = typer.Option(4),
):
    """Run swebench.harness.run_evaluation for each method's preds.json."""
    chosen = methods.split(",") if methods else list(METHODS)
    typer.echo(f"Verifying methods: {chosen}\n")

    summary = {}
    for method in chosen:
        preds = RESULTS_DIR / method / "output" / "preds.json"
        if not preds.exists():
            preds_jsonl = RESULTS_DIR / method / "output" / "preds.jsonl"
            if preds_jsonl.exists():
                preds = preds_jsonl
        if not preds.exists():
            typer.echo(f"  {method:15s} SKIP (no predictions)")
            continue
        with open(preds) as f:
            data = json.load(f)
        n = len(data) if isinstance(data, dict) else len(data)
        eval_dir = RESULTS_DIR / method / "eval"
        eval_dir.mkdir(parents=True, exist_ok=True)
        run_id = f"swectx_{method}"
        typer.echo(f"\n--- {method} ({n} predictions) ---")
        cmd = [
            sys.executable, "-m", "swebench.harness.run_evaluation",
            "--dataset_name", "SWE-bench/SWE-bench_Lite",
            "--predictions_path", str(preds),
            "--max_workers", str(max_workers),
            "--run_id", run_id,
            "--report_dir", str(eval_dir),
        ]
        typer.echo(" ".join(cmd))
        # List-form swebench harness invocation with controlled args.
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)  # nosec B603  # nosem
        if proc.returncode != 0:
            typer.echo(f"  ERROR exit {proc.returncode}: {proc.stderr[-400:]}")
        log_dir = PROJECT_ROOT / "logs" / "run_evaluation" / run_id / "output"
        if log_dir.exists():
            resolved = 0
            evaluated = 0
            for report in log_dir.glob("*/report.json"):
                with open(report) as f:
                    rep = json.load(f)
                evaluated += 1
                for v in rep.values():
                    if v.get("resolved"):
                        resolved += 1
            summary[method] = {"preds": n, "evaluated": evaluated, "resolved": resolved}

    typer.echo("\n\n" + "=" * 70)
    typer.echo("VERIFICATION SUMMARY")
    typer.echo("=" * 70)
    typer.echo(f"{'Method':<15} {'Preds':>6} {'Evaluated':>10} {'Resolved':>9} {'Rate':>8}")
    typer.echo("-" * 60)
    for method in chosen:
        if method in summary:
            r = summary[method]
            rate = r["resolved"] / r["evaluated"] if r["evaluated"] else 0
            typer.echo(f"{method:<15} {r['preds']:>6} {r['evaluated']:>10} {r['resolved']:>9} {rate:>7.1%}")

    out = RESULTS_DIR / "verify_summary.json"
    out.write_text(json.dumps(summary, indent=2))
    typer.echo(f"\nSaved {out}")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@app.command()
def status():
    """Print quick status for each method."""
    typer.echo(f"\n{'Method':<15} {'Trajs':>8}  {'Config':>15}")
    typer.echo("-" * 60)
    for method in METHODS:
        d = RESULTS_DIR / method / "output"
        n = len(list(d.glob("*.traj"))) if d.exists() else 0
        cfg = (RESULTS_DIR / method / "config.yaml").exists()
        typer.echo(f"{method:<15} {n:>8}  {'yes' if cfg else '-':>15}")


if __name__ == "__main__":
    app()
