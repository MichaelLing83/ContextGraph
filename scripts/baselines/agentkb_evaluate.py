"""Evaluate Agent-KB baseline vs ContextGraph retrieval.

Compares retrieval quality between Agent-KB's hybrid approach (TF-IDF + semantic)
and ContextGraph's graph-based 3-channel retrieval (cosine + BM25 + PPR + RRF + MMR).

Metrics:
- Recall@k: fraction of relevant items retrieved in top-k
- MRR (Mean Reciprocal Rank): 1/rank of first relevant result
- Diversity: number of unique error types covered in results
- Latency: query response time

Also generates a SWE-agent config that uses the Agent-KB server.

Usage:
    # Run retrieval comparison (requires both systems running)
    uv run python scripts/baselines/agentkb_evaluate.py compare \
        --index-dir data/baselines/agentkb_index \
        --queries-file data/baselines/eval_queries.json \
        --top-k 10

    # Generate SWE-agent config for Agent-KB baseline
    uv run python scripts/baselines/agentkb_evaluate.py gen-config \
        --output configs/swe_agent_agentkb_baseline.yaml

    # Generate evaluation queries from past experiment data
    uv run python scripts/baselines/agentkb_evaluate.py gen-queries \
        --output data/baselines/eval_queries.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

import numpy as np
import typer

# Ensure scripts.baselines is importable when running directly
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

app = typer.Typer(help="Evaluate Agent-KB baseline vs ContextGraph retrieval.")


# ---------------------------------------------------------------------------
# Evaluation metrics
# ---------------------------------------------------------------------------


def recall_at_k(retrieved_ids: List[str], relevant_ids: Set[str], k: int) -> float:
    """Compute Recall@k: fraction of relevant items in top-k results."""
    if not relevant_ids:
        return 0.0
    top_k_ids = set(retrieved_ids[:k])
    return len(top_k_ids & relevant_ids) / len(relevant_ids)


def mrr(retrieved_ids: List[str], relevant_ids: Set[str]) -> float:
    """Compute MRR: reciprocal rank of first relevant result."""
    for i, rid in enumerate(retrieved_ids):
        if rid in relevant_ids:
            return 1.0 / (i + 1)
    return 0.0


def diversity_score(results: List[dict]) -> int:
    """Count unique error types covered in results."""
    error_types = set()
    for r in results:
        for et in r.get("error_types", []):
            if et:
                error_types.add(et)
    return len(error_types)


def text_overlap(results_a: List[dict], results_b: List[dict]) -> float:
    """Compute Jaccard overlap of result sets by ID."""
    ids_a = set(r["id"] for r in results_a)
    ids_b = set(r["id"] for r in results_b)
    if not ids_a and not ids_b:
        return 1.0
    if not ids_a or not ids_b:
        return 0.0
    return len(ids_a & ids_b) / len(ids_a | ids_b)


# ---------------------------------------------------------------------------
# Retrieval comparison
# ---------------------------------------------------------------------------


def query_agentkb(
    retriever, query_text: str, top_k: int = 10
) -> tuple[List[dict], float]:
    """Query Agent-KB retriever, return results and latency."""
    t0 = time.time()
    results = retriever.query(query_text, top_k=top_k)
    latency = time.time() - t0
    return results, latency


def query_contextgraph(
    store, embedder, query_text: str, top_k: int = 10, error_type: Optional[str] = None
) -> tuple[List[dict], float]:
    """Query ContextGraph's PlaybookRetriever, return results and latency."""
    from agent_memory.playbook import PlaybookRetriever

    retriever = PlaybookRetriever(store=store, embedder=embedder)
    t0 = time.time()
    entries = retriever.retrieve(
        query_text=query_text,
        top_k=top_k,
        diversity=0.3,
        error_type=error_type,
    )
    latency = time.time() - t0

    results = []
    for entry in entries:
        results.append({
            "id": entry.id,
            "text": entry.text,
            "category": entry.section,
            "source_type": "CanonicalRule",
            "error_types": [],
            "score": getattr(entry, "_rrf_score", 0.0),
        })
    return results, latency


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@app.command()
def compare(
    index_dir: str = typer.Option(
        "data/baselines/agentkb_index",
        help="Agent-KB index directory",
    ),
    queries_file: str = typer.Option(
        "data/baselines/eval_queries.json",
        help="JSON file with evaluation queries",
    ),
    top_k: int = typer.Option(10, help="Number of results to compare"),
    neo4j_uri: str = typer.Option(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        help="Neo4j URI for ContextGraph",
    ),
    neo4j_password: str = typer.Option("contextgraph123", help="Neo4j password"),
    output: str = typer.Option(
        "data/baselines/eval_results.json",
        help="Output file for detailed results",
    ),
    skip_contextgraph: bool = typer.Option(
        False, help="Skip ContextGraph queries (if Neo4j unavailable)"
    ),
):
    """Compare retrieval quality: Agent-KB vs ContextGraph."""
    from scripts.baselines.agentkb_baseline import AgentKBRetriever

    # Load evaluation queries
    queries_path = Path(queries_file)
    if not queries_path.exists():
        typer.echo(f"Queries file not found: {queries_file}")
        typer.echo("Generate it first with: uv run python scripts/baselines/agentkb_evaluate.py gen-queries")
        raise typer.Exit(1)

    with open(queries_path) as f:
        queries = json.load(f)

    typer.echo(f"Loaded {len(queries)} evaluation queries")

    # Load Agent-KB index
    typer.echo(f"Loading Agent-KB index from {index_dir}...")
    akb_retriever = AgentKBRetriever()
    akb_retriever.load_index(index_dir)

    # Load ContextGraph retriever
    cg_store = None
    cg_embedder = None
    if not skip_contextgraph:
        try:
            from agent_memory.neo4j_store import Neo4jStore
            from agent_memory.embeddings import OpenAIEmbeddingClient

            cg_store = Neo4jStore(
                uri=neo4j_uri,
                auth=("neo4j", neo4j_password),
            )
            cg_store.verify_connectivity()

            api_key = os.environ.get("OPENAI_API_KEY", os.environ.get("LITELLM_MASTER_KEY", ""))
            api_base = os.environ.get("OPENAI_API_BASE", "http://localhost:4000/v1")
            cg_embedder = OpenAIEmbeddingClient(
                api_key=api_key,
                model="text-embedding-3-large",
                base_url=api_base,
            )
            typer.echo("ContextGraph connected.")
        except Exception as e:
            typer.echo(f"Warning: ContextGraph connection failed: {e}")
            typer.echo("Running Agent-KB only evaluation.")
            skip_contextgraph = True

    # Run evaluation
    akb_metrics = {"recall@5": [], "recall@10": [], "mrr": [], "diversity": [], "latency_ms": []}
    cg_metrics = {"recall@5": [], "recall@10": [], "mrr": [], "diversity": [], "latency_ms": []}
    overlap_scores = []
    detailed_results = []

    for i, q in enumerate(queries):
        query_text = q["query"]
        relevant_ids = set(q.get("relevant_ids", []))
        error_type = q.get("error_type")

        # Agent-KB retrieval
        akb_results, akb_latency = query_agentkb(akb_retriever, query_text, top_k)
        akb_ids = [r["id"] for r in akb_results]

        akb_metrics["recall@5"].append(recall_at_k(akb_ids, relevant_ids, 5))
        akb_metrics["recall@10"].append(recall_at_k(akb_ids, relevant_ids, 10))
        akb_metrics["mrr"].append(mrr(akb_ids, relevant_ids))
        akb_metrics["diversity"].append(diversity_score(akb_results))
        akb_metrics["latency_ms"].append(akb_latency * 1000)

        # ContextGraph retrieval
        cg_results = []
        cg_latency = 0.0
        if not skip_contextgraph:
            cg_results, cg_latency = query_contextgraph(
                cg_store, cg_embedder, query_text, top_k, error_type
            )
            cg_ids = [r["id"] for r in cg_results]

            cg_metrics["recall@5"].append(recall_at_k(cg_ids, relevant_ids, 5))
            cg_metrics["recall@10"].append(recall_at_k(cg_ids, relevant_ids, 10))
            cg_metrics["mrr"].append(mrr(cg_ids, relevant_ids))
            cg_metrics["diversity"].append(diversity_score(cg_results))
            cg_metrics["latency_ms"].append(cg_latency * 1000)

            overlap_scores.append(text_overlap(akb_results, cg_results))

        detailed_results.append({
            "query": query_text,
            "error_type": error_type,
            "akb_top3_ids": akb_ids[:3],
            "cg_top3_ids": [r["id"] for r in cg_results[:3]],
            "akb_latency_ms": akb_latency * 1000,
            "cg_latency_ms": cg_latency * 1000,
            "overlap": overlap_scores[-1] if overlap_scores else None,
        })

        if (i + 1) % 10 == 0:
            typer.echo(f"  Processed {i + 1}/{len(queries)} queries")

    # Close ContextGraph store
    if cg_store:
        cg_store.close()

    # Print summary
    typer.echo("\n" + "=" * 70)
    typer.echo("RETRIEVAL COMPARISON RESULTS")
    typer.echo("=" * 70)

    typer.echo(f"\n{'Metric':<20} {'Agent-KB':>12} {'ContextGraph':>14} {'Delta':>10}")
    typer.echo("-" * 60)

    for metric in ["recall@5", "recall@10", "mrr", "diversity", "latency_ms"]:
        akb_val = np.mean(akb_metrics[metric]) if akb_metrics[metric] else 0
        cg_val = np.mean(cg_metrics[metric]) if cg_metrics[metric] else 0
        delta = cg_val - akb_val

        if metric == "latency_ms":
            typer.echo(f"{metric:<20} {akb_val:>10.1f}ms {cg_val:>12.1f}ms {delta:>+10.1f}")
        else:
            typer.echo(f"{metric:<20} {akb_val:>12.4f} {cg_val:>14.4f} {delta:>+10.4f}")

    if overlap_scores:
        typer.echo(f"\nResult set overlap (Jaccard): {np.mean(overlap_scores):.4f}")

    typer.echo("\n" + "=" * 70)
    typer.echo("INTERPRETATION")
    typer.echo("=" * 70)
    typer.echo("- Higher Recall/MRR = better retrieval quality")
    typer.echo("- Higher Diversity = more error types covered (broader)")
    typer.echo("- Lower Latency = faster")
    typer.echo("- Low overlap = systems find different things (complementary)")
    typer.echo("- High overlap = systems agree (retrieval method less important)")

    # Save detailed results
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "summary": {
            "num_queries": len(queries),
            "top_k": top_k,
            "agent_kb": {k: float(np.mean(v)) for k, v in akb_metrics.items() if v},
            "context_graph": {k: float(np.mean(v)) for k, v in cg_metrics.items() if v},
            "mean_overlap": float(np.mean(overlap_scores)) if overlap_scores else None,
        },
        "per_query": detailed_results,
    }
    out_path.write_text(json.dumps(report, indent=2))
    typer.echo(f"\nDetailed results saved to {out_path}")


@app.command()
def gen_queries(
    output: str = typer.Option(
        "data/baselines/eval_queries.json",
        help="Output file for evaluation queries",
    ),
    neo4j_uri: str = typer.Option(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        help="Neo4j URI",
    ),
    neo4j_password: str = typer.Option("contextgraph123", help="Neo4j password"),
    num_queries: int = typer.Option(50, help="Number of queries to generate"),
    seed: int = typer.Option(42, help="Random seed"),
):
    """Generate evaluation queries from Neo4j error patterns and trajectories.

    Creates queries that simulate real agent usage:
    - Error-based queries (from ErrorPattern nodes)
    - Problem-description queries (from ProblemSummary nodes)
    - Mixed queries combining error + context

    For each query, relevant_ids are set to CanonicalRule nodes that
    ADDRESSES_ERROR the matching ErrorPattern (ground truth).
    """
    from neo4j import GraphDatabase

    rng = np.random.default_rng(seed)
    driver = GraphDatabase.driver(neo4j_uri, auth=("neo4j", neo4j_password))

    queries = []

    with driver.session() as session:
        # Type 1: Error-based queries with ground truth from ADDRESSES_ERROR
        typer.echo("Generating error-based queries...")
        error_results = session.run("""
            MATCH (ep:ErrorPattern)<-[:ADDRESSES_ERROR]-(cr:CanonicalRule)
            WITH ep, collect(cr.id) AS rule_ids
            WHERE size(rule_ids) >= 2
            RETURN ep.error_type AS error_type,
                   ep.error_keywords AS keywords,
                   ep.error_keywords_text AS keywords_text,
                   rule_ids
            ORDER BY size(rule_ids) DESC
            LIMIT $limit
        """, {"limit": num_queries})

        error_queries = []
        for rec in error_results:
            error_type = rec["error_type"]
            keywords_text = rec["keywords_text"] or ""
            rule_ids = rec["rule_ids"]

            # Build a natural query from error type and keywords
            query_text = f"{error_type}: {keywords_text}" if keywords_text else error_type
            error_queries.append({
                "query": query_text,
                "error_type": error_type,
                "relevant_ids": rule_ids,
                "query_type": "error_based",
            })

        # Type 2: Problem-description queries
        typer.echo("Generating problem-description queries...")
        problem_results = session.run("""
            MATCH (ps:ProblemSummary)-[:SUMMARIZES]->(t:Trajectory)
            WHERE t.success = true
            MATCH (t)-[:HAS_FRAGMENT]->(f:Fragment)-[:CAUSED_ERROR]->(ep:ErrorPattern)
            MATCH (cr:CanonicalRule)-[:ADDRESSES_ERROR]->(ep)
            WITH ps, collect(DISTINCT cr.id) AS rule_ids, collect(DISTINCT ep.error_type) AS error_types
            WHERE size(rule_ids) >= 1
            RETURN ps.summary_text AS summary, rule_ids, error_types
            LIMIT $limit
        """, {"limit": num_queries})

        problem_queries = []
        for rec in problem_results:
            summary = rec["summary"] or ""
            if len(summary) > 300:
                summary = summary[:300]
            problem_queries.append({
                "query": summary,
                "error_type": rec["error_types"][0] if rec["error_types"] else None,
                "relevant_ids": rec["rule_ids"],
                "query_type": "problem_description",
            })

    driver.close()

    # Combine and sample
    all_queries = error_queries + problem_queries
    if len(all_queries) > num_queries:
        indices = rng.choice(len(all_queries), size=num_queries, replace=False)
        queries = [all_queries[i] for i in sorted(indices)]
    else:
        queries = all_queries

    # Save
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(queries, indent=2, ensure_ascii=False))
    typer.echo(f"Generated {len(queries)} evaluation queries -> {out_path}")
    typer.echo(f"  Error-based: {sum(1 for q in queries if q['query_type'] == 'error_based')}")
    typer.echo(f"  Problem-desc: {sum(1 for q in queries if q['query_type'] == 'problem_description')}")


@app.command()
def gen_config(
    output: str = typer.Option(
        "configs/swe_agent_agentkb_baseline.yaml",
        help="Output YAML config path",
    ),
    server_host: str = typer.Option(
        "host.docker.internal",
        help="Agent-KB server hostname (from Docker container perspective)",
    ),
    server_port: int = typer.Option(8001, help="Agent-KB server port"),
):
    """Generate SWE-agent YAML config that uses Agent-KB server instead of Neo4j.

    Creates a treatment config where the QueryMemoryTool calls the Agent-KB
    baseline server instead of the ContextGraph Neo4j backend.
    """
    config_content = f"""\
# SWE-agent config: Agent-KB baseline (hybrid TF-IDF + semantic retrieval)
# This uses the Agent-KB server at port {server_port} instead of ContextGraph's Neo4j.
# Start the server first:
#   uv run python scripts/baselines/agentkb_server.py --port {server_port}

agent:
  model:
    name: claude-sonnet-4-20250514
    api_base: http://host.docker.internal:4000/v1
    api_key: ${{LITELLM_MASTER_KEY}}
    per_instance_cost_limit: 3.00
    total_cost_limit: 500.00

  tools:
    bundles:
      - path: /Users/zihanwu/Public/codes/ContextGraph/tools/query_memory_agentkb
    tool_call_timeout: 60

  templates:
    system_message: |
      You are a skilled software engineer. You have access to a memory tool
      that retrieves relevant experiences from past problem-solving sessions.
      Use it when you encounter errors or need strategies for specific tasks.

      The memory uses Agent-KB hybrid retrieval (TF-IDF + semantic similarity).

environment:
  repo_path: "{{{{repo_path}}}}"
  python: python3
  install_command: "pip install -e ."
  additional_docker_args:
    - "--add-host=host.docker.internal:host-gateway"
    - "--network=host"

run:
  batch_size: 5
  max_retries: 2

# Experiment metadata
metadata:
  experiment: agentkb_baseline
  retrieval_method: hybrid_tfidf_semantic
  text_weight: 0.5
  semantic_weight: 0.5
  server: http://{server_host}:{server_port}
"""

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(config_content)
    typer.echo(f"Generated SWE-agent config: {out_path}")
    typer.echo(f"\nTo use:")
    typer.echo(f"  1. Start Agent-KB server: uv run python scripts/baselines/agentkb_server.py --port {server_port}")
    typer.echo(f"  2. Create tool bundle at tools/query_memory_agentkb/ (HTTP-based)")
    typer.echo(f"  3. Run: python -m sweagent run-batch --config {output}")


@app.command()
def gen_tool_bundle(
    output_dir: str = typer.Option(
        "tools/query_memory_agentkb",
        help="Output directory for the tool bundle",
    ),
    server_host: str = typer.Option(
        "host.docker.internal",
        help="Agent-KB server hostname (from Docker container perspective)",
    ),
    server_port: int = typer.Option(8001, help="Agent-KB server port"),
):
    """Generate SWE-agent tool bundle that queries Agent-KB server via HTTP."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "bin").mkdir(exist_ok=True)

    # config.yaml - tool definition
    config_yaml = """\
name: query_memory_agentkb
description: >
  Query the Agent-KB baseline memory system for relevant past experiences.
  Returns strategies and rules learned from solving similar coding problems.
parameters:
  type: object
  properties:
    query:
      type: string
      description: Description of the problem or error you are facing.
    error_type:
      type: string
      description: The type of error (e.g. ImportError, TypeError).
  required:
    - query
"""
    (out_path / "config.yaml").write_text(config_yaml)

    # bin/query_memory_agentkb - bash wrapper
    server_url = f"http://{server_host}:{server_port}"
    bin_script = f"""\
#!/bin/bash
# Query the Agent-KB baseline server via HTTP.
# This script is called by SWE-agent inside Docker containers.

QUERY="$1"
ERROR_TYPE="${{2:-}}"

SERVER_URL="{server_url}"

# Build JSON payload
if [ -n "$ERROR_TYPE" ]; then
    PAYLOAD=$(python3 -c "import json; print(json.dumps({{'query': '''$QUERY''', 'error_type': '''$ERROR_TYPE''', 'top_k': 10}}))")
else
    PAYLOAD=$(python3 -c "import json; print(json.dumps({{'query': '''$QUERY''', 'top_k': 10}}))")
fi

# Make HTTP request
RESPONSE=$(curl -s -X POST "$SERVER_URL/search" \\
    -H "Content-Type: application/json" \\
    -d "$PAYLOAD" 2>/dev/null)

if [ $? -ne 0 ] || [ -z "$RESPONSE" ]; then
    echo "Error: Could not reach Agent-KB server at $SERVER_URL"
    exit 1
fi

# Format output
python3 -c "
import json, sys
try:
    data = json.loads('''$RESPONSE''')
    results = data.get('results', [])
    if not results:
        print('No relevant experience found.')
    else:
        print('<memory_playbook>')
        print('The following rules were learned from solving similar coding problems.')
        print('Apply relevant rules to your current task.')
        print()
        for r in results:
            print(f\\"[{{r['id']}}] {{r['text']}}\\")
        print('</memory_playbook>')
except Exception as e:
    print(f'Error parsing response: {{e}}')
"
"""
    bin_path = out_path / "bin" / "query_memory_agentkb"
    bin_path.write_text(bin_script)
    bin_path.chmod(0o755)

    # install.sh
    install_script = """\
#!/bin/bash
# Install dependencies for Agent-KB tool (just needs curl, which is usually available)
echo "Agent-KB tool ready (uses HTTP, no additional dependencies needed)"
"""
    install_path = out_path / "install.sh"
    install_path.write_text(install_script)
    install_path.chmod(0o755)

    typer.echo(f"Generated SWE-agent tool bundle at {out_path}/")
    typer.echo(f"  config.yaml - tool definition")
    typer.echo(f"  bin/query_memory_agentkb - HTTP-based query script")
    typer.echo(f"  install.sh - installation script")
    typer.echo(f"\nServer URL: {server_url}")


if __name__ == "__main__":
    app()
