"""
Run 5-way baseline comparison on 50-problem SWE-bench Verified subset.

Methods:
  1. no_memory    — vanilla SWE-agent (control)
  2. expel        — flat rule list injected into system prompt (~20 rules)
  3. faiss        — FAISS vector retrieval (SWE-Bench-CL style)
  4. agentkb      — TF-IDF + semantic hybrid (Agent-KB style)
  5. contextgraph — Neo4j graph + 3-channel PPR retrieval (ours)

Usage:
  # Run all methods sequentially
  uv run python scripts/run_baseline_comparison.py run-all

  # Run a single method
  uv run python scripts/run_baseline_comparison.py run --method contextgraph

  # Analyze results
  uv run python scripts/run_baseline_comparison.py analyze

Prerequisites:
  - docker compose up -d (Neo4j + LiteLLM)
  - ExpeL rules extracted: data/baselines/expel_rules.json
  - Agent-KB server running: uv run python scripts/baselines/agentkb_server.py
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(help="5-way baseline comparison on SWE-bench Verified (50 problems)")

PROJECT_ROOT = Path(__file__).parent.parent
CONFIGS_DIR = PROJECT_ROOT / "configs"
RESULTS_DIR = PROJECT_ROOT / "results" / "baseline_comparison"
SUBSET_FILE = PROJECT_ROOT / "results" / "live_experiment" / "verified_50_subset.json"
SWE_AGENT_DIR = PROJECT_ROOT / "SWE-agent"

# cost_limit = 10x default (default=5 in live-swe-agent)
COST_LIMIT = 50.0


def load_subset_ids() -> list[str]:
    with open(SUBSET_FILE) as f:
        return json.load(f)["instance_ids"]


def generate_config(method: str, output_path: Path) -> Path:
    """Generate SWE-agent YAML config for the given method."""
    base_config = {
        "model_name": "openai/gpt-5.4",
        "api_base": "http://localhost:4000/v1",
        "cost_limit": COST_LIMIT,
        "max_output_tokens": 8192,
    }

    if method == "no_memory":
        template = CONFIGS_DIR / "swe_agent_control.yaml"
        _patch_cost_limit(template, output_path, COST_LIMIT)

    elif method == "expel":
        rules_file = PROJECT_ROOT / "data" / "baselines" / "expel_rules.json"
        if not rules_file.exists():
            typer.echo(f"ERROR: ExpeL rules not found at {rules_file}")
            typer.echo("Run: uv run python scripts/baselines/expel_baseline.py extract")
            raise typer.Exit(1)
        # Generate config with rules injected
        _generate_expel_config(rules_file, output_path, base_config)

    elif method == "faiss":
        # FAISS baseline uses same agent config as control but with a different tool
        # that queries the FAISS server instead of Neo4j
        _generate_faiss_config(output_path, base_config)

    elif method == "agentkb":
        # Agent-KB uses FastAPI server on port 8001
        _generate_agentkb_config(output_path, base_config)

    elif method == "contextgraph":
        template = CONFIGS_DIR / "swe_agent_treatment.yaml"
        _patch_cost_limit(template, output_path, COST_LIMIT)

    return output_path


def _patch_cost_limit(template: Path, output: Path, cost_limit: float):
    """Copy config and set per_instance_cost_limit."""
    content = template.read_text()
    content = content.replace(
        "per_instance_cost_limit: 0",
        f"per_instance_cost_limit: {cost_limit}",
    )
    output.write_text(content)


def _generate_expel_config(rules_file: Path, output: Path, base_config: dict):
    """Generate config with ExpeL rules in system prompt."""
    with open(rules_file) as f:
        rules_data = json.load(f)

    rules_list = rules_data if isinstance(rules_data, list) else rules_data.get("rules", [])
    rules_text = "\n".join(
        f"  {i+1}. {r['text'] if isinstance(r, dict) else r}"
        for i, r in enumerate(rules_list[:20])
    )

    system_prompt = (
        "You are a helpful assistant that can interact with a computer to solve tasks.\n"
        "\n"
        "Based on past debugging experiences, here are important rules to follow:\n"
        f"{rules_text}\n"
        "\n"
        "Apply these rules when diagnosing and fixing code issues."
    )

    # Indent each line of the system prompt for YAML block scalar
    indented_prompt = "\n".join(
        f"      {line}" if line.strip() else ""
        for line in system_prompt.split("\n")
    )

    control = (CONFIGS_DIR / "swe_agent_control.yaml").read_text()
    old_system = "    system_template: |-\n      You are a helpful assistant that can interact with a computer to solve tasks."
    new_system = f"    system_template: |-\n{indented_prompt}"
    content = control.replace(old_system, new_system)
    content = content.replace(
        "per_instance_cost_limit: 0",
        f"per_instance_cost_limit: {base_config['cost_limit']}",
    )
    output.write_text(content)


def _generate_faiss_config(output: Path, base_config: dict):
    """Generate config that uses FAISS memory via HTTP tool."""
    # FAISS baseline: same as treatment but tool calls go to FAISS server (port 8002)
    treatment = (CONFIGS_DIR / "swe_agent_treatment.yaml").read_text()
    # Replace Neo4j host with FAISS server
    content = treatment.replace(
        "NEO4J_URI: bolt://host.docker.internal:7687",
        "MEMORY_SERVER_URL: http://host.docker.internal:8002",
    )
    content = content.replace(
        "per_instance_cost_limit: 0",
        f"per_instance_cost_limit: {base_config['cost_limit']}",
    )
    output.write_text(content)


def _generate_agentkb_config(output: Path, base_config: dict):
    """Generate config that uses Agent-KB server (port 8001)."""
    treatment = (CONFIGS_DIR / "swe_agent_treatment.yaml").read_text()
    content = treatment.replace(
        "NEO4J_URI: bolt://host.docker.internal:7687",
        "MEMORY_SERVER_URL: http://host.docker.internal:8001",
    )
    content = content.replace(
        "per_instance_cost_limit: 0",
        f"per_instance_cost_limit: {base_config['cost_limit']}",
    )
    output.write_text(content)


@app.command()
def run(
    method: str = typer.Argument(help="Method: no_memory|expel|faiss|agentkb|contextgraph"),
    n_workers: int = typer.Option(1, help="Parallel workers for SWE-agent"),
    resume: bool = typer.Option(False, help="Resume from checkpoint"),
):
    """Run a single baseline method on 50 problems."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    run_dir = RESULTS_DIR / method
    run_dir.mkdir(exist_ok=True)

    config_path = run_dir / "config.yaml"
    generate_config(method, config_path)

    instance_ids = load_subset_ids()
    typer.echo(f"\n{'='*60}")
    typer.echo(f"Method: {method}")
    typer.echo(f"Problems: {len(instance_ids)}")
    typer.echo(f"Cost limit: ${COST_LIMIT}/problem")
    typer.echo(f"Config: {config_path}")
    typer.echo(f"{'='*60}\n")

    # Build instance filter string for SWE-agent v1.1.0
    filter_str = "|".join(instance_ids)

    # Build SWE-agent command (v1.1.0 CLI format)
    cmd = [
        sys.executable, "-m", "sweagent", "run-batch",
        "--config", str(config_path),
        "--instances.type", "swe_bench",
        "--instances.subset", "verified",
        "--instances.split", "test",
        "--instances.filter", filter_str,
        "--output_dir", str(run_dir / "output"),
        "--num_workers", str(n_workers),
    ]

    typer.echo(f"Command: {' '.join(cmd)}")
    typer.echo(f"\nStarting at {datetime.now().isoformat()}")

    log_file = run_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    typer.echo(f"Log: {log_file}\n")

    with open(log_file, "w") as log:
        proc = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
        )
        typer.echo(f"PID: {proc.pid}")
        typer.echo("Running... (use `tail -f {log_file}` to monitor)")
        proc.wait()

    typer.echo(f"\nFinished with exit code {proc.returncode}")


@app.command()
def run_all(
    n_workers: int = typer.Option(1, help="Parallel workers"),
    methods: Optional[str] = typer.Option(None, help="Comma-separated methods to run"),
):
    """Run all 5 methods sequentially."""
    all_methods = ["no_memory", "expel", "faiss", "agentkb", "contextgraph"]
    if methods:
        all_methods = [m.strip() for m in methods.split(",")]

    for method in all_methods:
        typer.echo(f"\n{'#'*60}")
        typer.echo(f"# Running: {method}")
        typer.echo(f"{'#'*60}")
        run(method=method, n_workers=n_workers, resume=False)


@app.command()
def analyze():
    """Analyze and compare results across all methods."""
    methods = ["no_memory", "expel", "faiss", "agentkb", "contextgraph"]
    results = {}

    for method in methods:
        preds_file = RESULTS_DIR / method / "output" / "preds.jsonl"
        eval_dir = RESULTS_DIR / method / "eval"

        if not preds_file.exists():
            typer.echo(f"  {method}: no predictions found")
            continue

        # Count predictions
        with open(preds_file) as f:
            preds = [json.loads(line) for line in f]

        n_total = len(preds)
        n_with_patch = sum(1 for p in preds if p.get("model_patch", "").strip())
        n_empty = n_total - n_with_patch

        # Check eval results
        resolved = 0
        eval_report = eval_dir / "report.json"
        if eval_report.exists():
            with open(eval_report) as f:
                report = json.load(f)
            resolved = report.get("resolved", 0)

        results[method] = {
            "total": n_total,
            "with_patch": n_with_patch,
            "empty_patch": n_empty,
            "resolved": resolved,
            "resolve_rate": resolved / n_total if n_total > 0 else 0,
            "patch_rate": n_with_patch / n_total if n_total > 0 else 0,
        }

    if not results:
        typer.echo("No results found. Run experiments first.")
        raise typer.Exit(1)

    # Print comparison table
    typer.echo(f"\n{'='*70}")
    typer.echo("BASELINE COMPARISON — SWE-bench Verified (50 problems, cost_limit=$50)")
    typer.echo(f"{'='*70}")
    typer.echo(f"{'Method':<15} {'Total':>6} {'Patch':>6} {'Empty':>6} {'Resolved':>9} {'Rate':>8}")
    typer.echo("-" * 70)

    for method, r in results.items():
        typer.echo(
            f"{method:<15} {r['total']:>6} {r['with_patch']:>6} "
            f"{r['empty_patch']:>6} {r['resolved']:>9} "
            f"{r['resolve_rate']*100:>7.1f}%"
        )

    typer.echo("-" * 70)

    # Save results
    output_file = RESULTS_DIR / "comparison_results.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    typer.echo(f"\nResults saved to {output_file}")


@app.command()
def verify(
    methods: Optional[str] = typer.Option(None, help="Comma-separated methods to verify"),
    max_workers: int = typer.Option(4, help="Parallel workers for evaluation"),
):
    """Run SWE-bench verification on all completed experiments."""
    from swebench.harness.run_evaluation import main as run_evaluation

    all_methods = ["no_memory", "expel", "faiss", "agentkb", "contextgraph"]
    if methods:
        all_methods = [m.strip() for m in methods.split(",")]

    typer.echo(f"\n{'='*70}")
    typer.echo("SWE-bench Verification — Running evaluation harness")
    typer.echo(f"{'='*70}\n")

    for method in all_methods:
        preds_file = RESULTS_DIR / method / "output" / "preds.jsonl"
        if not preds_file.exists():
            typer.echo(f"  {method:<15} SKIP (no predictions)")
            continue

        n_preds = sum(1 for _ in open(preds_file))
        eval_dir = RESULTS_DIR / method / "eval"
        eval_dir.mkdir(parents=True, exist_ok=True)

        typer.echo(f"\n{'─'*60}")
        typer.echo(f"  Verifying: {method} ({n_preds} predictions)")
        typer.echo(f"  Output:    {eval_dir}")
        typer.echo(f"{'─'*60}")

        cmd = [
            sys.executable, "-m", "swebench.harness.run_evaluation",
            "--dataset_name", "princeton-nlp/SWE-bench_Verified",
            "--predictions_path", str(preds_file),
            "--max_workers", str(max_workers),
            "--run_id", f"baseline_{method}",
            "--output_dir", str(eval_dir),
        ]

        typer.echo(f"  Command: {' '.join(cmd)}")
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

        if proc.returncode != 0:
            typer.echo(f"  ERROR (exit {proc.returncode}):")
            typer.echo(proc.stderr[-500:] if proc.stderr else "no stderr")
        else:
            typer.echo(f"  Completed successfully")

        # Parse results
        report_file = eval_dir / f"baseline_{method}.json"
        if not report_file.exists():
            # Try alternate location
            for f in eval_dir.glob("*.json"):
                report_file = f
                break

        if report_file.exists():
            with open(report_file) as f:
                report = json.load(f)
            resolved = report.get("resolved", report.get("resolved_ids", []))
            n_resolved = len(resolved) if isinstance(resolved, list) else resolved
            typer.echo(f"  Result: {n_resolved}/{n_preds} resolved ({n_resolved/n_preds*100:.1f}%)")

    # Print summary table
    typer.echo(f"\n\n{'='*70}")
    typer.echo("VERIFICATION RESULTS SUMMARY")
    typer.echo(f"{'='*70}")
    typer.echo(f"{'Method':<15} {'Preds':>6} {'Resolved':>9} {'Rate':>8}")
    typer.echo("-" * 70)

    summary = {}
    for method in all_methods:
        preds_file = RESULTS_DIR / method / "output" / "preds.jsonl"
        eval_dir = RESULTS_DIR / method / "eval"
        if not preds_file.exists():
            continue

        n_preds = sum(1 for _ in open(preds_file))
        n_resolved = 0

        for f in eval_dir.glob("*.json"):
            try:
                with open(f) as fh:
                    report = json.load(fh)
                resolved = report.get("resolved", report.get("resolved_ids", []))
                n_resolved = len(resolved) if isinstance(resolved, list) else resolved
                break
            except Exception:
                continue

        rate = n_resolved / n_preds if n_preds > 0 else 0
        summary[method] = {"preds": n_preds, "resolved": n_resolved, "rate": rate}
        typer.echo(f"{method:<15} {n_preds:>6} {n_resolved:>9} {rate*100:>7.1f}%")

    typer.echo("-" * 70)

    # Save
    output_file = RESULTS_DIR / "verification_results.json"
    with open(output_file, "w") as f:
        json.dump(summary, f, indent=2)
    typer.echo(f"\nResults saved to {output_file}")


@app.command()
def status():
    """Check infrastructure and experiment status."""
    typer.echo("=== Infrastructure Status ===\n")

    # Docker
    docker_ok = subprocess.run(["docker", "ps"], capture_output=True).returncode == 0
    typer.echo(f"  Docker:       {'✓' if docker_ok else '✗ — run: docker compose up -d'}")

    # Neo4j
    neo4j_ok = False
    try:
        from neo4j import GraphDatabase
        d = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "contextgraph123"))
        d.verify_connectivity()
        d.close()
        neo4j_ok = True
    except Exception:
        pass
    typer.echo(f"  Neo4j (7687): {'✓' if neo4j_ok else '✗ — run: docker compose up -d'}")

    # LiteLLM
    import urllib.request
    litellm_ok = False
    try:
        urllib.request.urlopen("http://localhost:4000/health", timeout=3)
        litellm_ok = True
    except Exception:
        pass
    typer.echo(f"  LiteLLM:      {'✓' if litellm_ok else '✗ — run: docker compose up -d'}")

    # Agent-KB server
    agentkb_ok = False
    try:
        urllib.request.urlopen("http://localhost:8001/health", timeout=3)
        agentkb_ok = True
    except Exception:
        pass
    typer.echo(f"  Agent-KB srv: {'✓' if agentkb_ok else '✗ — run: uv run python scripts/baselines/agentkb_server.py'}")

    # ExpeL rules
    expel_ok = (PROJECT_ROOT / "data" / "baselines" / "expel_rules.json").exists()
    typer.echo(f"  ExpeL rules:  {'✓' if expel_ok else '✗ — run: uv run python scripts/baselines/expel_baseline.py extract'}")

    typer.echo("\n=== Experiment Status ===\n")
    methods = ["no_memory", "expel", "faiss", "agentkb", "contextgraph"]
    for method in methods:
        run_dir = RESULTS_DIR / method
        preds = run_dir / "output" / "preds.jsonl"
        if preds.exists():
            n = sum(1 for _ in open(preds))
            typer.echo(f"  {method:<15} {n}/50 predictions")
        else:
            typer.echo(f"  {method:<15} not started")


@app.command()
def preflight():
    """Run preflight checks and setup steps before experiment."""
    typer.echo("=== Preflight Checks ===\n")

    # 1. Start Docker
    typer.echo("1. Starting Docker containers...")
    subprocess.run(["docker", "compose", "up", "-d"], cwd=str(PROJECT_ROOT))

    # 2. Wait for Neo4j
    typer.echo("\n2. Waiting for Neo4j...")
    for i in range(30):
        try:
            from neo4j import GraphDatabase
            d = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "contextgraph123"))
            d.verify_connectivity()
            d.close()
            typer.echo("   Neo4j ready.")
            break
        except Exception:
            time.sleep(2)
    else:
        typer.echo("   ERROR: Neo4j did not start in 60s")
        raise typer.Exit(1)

    # 3. Sync OAuth token
    typer.echo("\n3. Syncing OAuth token...")
    subprocess.run(["bash", "scripts/sync_oauth_token.sh"], cwd=str(PROJECT_ROOT))

    # 4. Extract ExpeL rules if not present
    rules_file = PROJECT_ROOT / "data" / "baselines" / "expel_rules.json"
    if not rules_file.exists():
        typer.echo("\n4. Extracting ExpeL rules...")
        subprocess.run(
            [sys.executable, "scripts/baselines/expel_baseline.py", "extract"],
            cwd=str(PROJECT_ROOT),
        )
    else:
        typer.echo(f"\n4. ExpeL rules already exist ({rules_file})")

    # 5. Build Agent-KB index
    typer.echo("\n5. Building Agent-KB index...")
    subprocess.run(
        [sys.executable, "scripts/baselines/agentkb_baseline.py", "build-from-neo4j"],
        cwd=str(PROJECT_ROOT),
    )

    typer.echo("\n=== Preflight complete. Run: uv run python scripts/run_baseline_comparison.py run-all ===")


if __name__ == "__main__":
    app()
