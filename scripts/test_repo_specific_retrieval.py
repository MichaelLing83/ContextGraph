#!/usr/bin/env python3
"""Test script to compare retrieval between baseline and repo-specific graphs.

Queries problems 31-40 from the v3 experiment and checks whether
repo-specific strategies appear in the enhanced graph's results.
"""

import os

from neo4j import GraphDatabase

# Instance IDs for problems 31-40
TEST_PROBLEMS = [
    "pydata__xarray-3677",
    "pydata__xarray-4687",
    "pydata__xarray-6461",
    "pydata__xarray-6992",
    "pytest-dev__pytest-10051",
    "pytest-dev__pytest-5787",
    "pytest-dev__pytest-7571",
    "scikit-learn__scikit-learn-13328",
    "scikit-learn__scikit-learn-25102",
    "scikit-learn__scikit-learn-25973",
]

# Simulated query strings (what an agent might search for)
QUERY_STRINGS = {
    "pydata__xarray-3677": "Dataset.merge with DataArray argument fails TypeError",
    "pydata__xarray-4687": "xarray open_mfdataset concat combine_attrs override",
    "pydata__xarray-6461": "xarray encoding drop reset round trip netcdf",
    "pydata__xarray-6992": "xarray Dataset.roll create unintended coordinate",
    "pytest-dev__pytest-10051": "pytest fixture parametrize ordering inconsistent",
    "pytest-dev__pytest-5787": "pytest parametrize indirect fixture error message",
    "pytest-dev__pytest-7571": "caplog set_level handler level not restored between tests",
    "scikit-learn__scikit-learn-13328": "HuberRegressor fit fails on boolean input array",
    "scikit-learn__scikit-learn-25102": "sklearn Calibration display from_estimator sample_weight",
    "scikit-learn__scikit-learn-25973": "sklearn ColumnTransformer set_output pandas transform",
}


def query_graph(driver, query_text, embedder, top_k=10):
    """Query the graph for playbook entries using vector similarity."""
    embedding = embedder.embed(query_text)

    result = driver.execute_query(
        """
        CALL db.index.vector.queryNodes('playbook_embedding', $top_k, $embedding)
        YIELD node, score
        RETURN node.id AS id, node.text AS text, node.section AS section,
               node.prefix AS prefix, node.repo AS repo,
               node.instance_id AS instance_id, score
        ORDER BY score DESC
        """,
        {"top_k": top_k, "embedding": embedding},
    )
    return [
        {
            "id": r["id"],
            "text": r["text"],
            "section": r["section"],
            "prefix": r["prefix"],
            "repo": r.get("repo"),
            "instance_id": r.get("instance_id"),
            "score": r["score"],
        }
        for r in result.records
    ]


def main():
    from agent_memory.embeddings import get_embedding_client

    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "contextgraph123")
    baseline_uri = os.environ.get("NEO4J_BASELINE_URI", "bolt://localhost:7687")
    repospec_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7689")

    baseline_driver = GraphDatabase.driver(
        baseline_uri, auth=(neo4j_user, neo4j_password)
    )
    repospec_driver = GraphDatabase.driver(
        repospec_uri, auth=(neo4j_user, neo4j_password)
    )

    embedder = get_embedding_client(
        "openai",
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_API_BASE"),
        model="text-embedding-3-large",
    )

    print("=" * 80)
    print(f"COMPARISON: Baseline ({baseline_uri}) vs Repo-Specific ({repospec_uri})")
    print("=" * 80)

    # Verify repo-specific entries exist
    count_result = repospec_driver.execute_query(
        "MATCH (p:PlaybookEntry) WHERE p.prefix = 'repo' RETURN count(p) AS cnt"
    )
    repo_count = count_result.records[0]["cnt"]
    print(f"\nRepo-specific strategies in enhanced graph: {repo_count}")

    baseline_count = baseline_driver.execute_query(
        "MATCH (p:PlaybookEntry) WHERE p.prefix = 'repo' RETURN count(p) AS cnt"
    )
    print(f"Repo-specific strategies in baseline graph: {baseline_count.records[0]['cnt']}")
    print()

    repo_specific_found = 0
    repo_specific_in_top5 = 0
    total_tested = 0

    for instance_id in TEST_PROBLEMS:
        query = QUERY_STRINGS[instance_id]
        repo = instance_id.split("__")[0].replace("-dev", "").replace("__", "/")

        print(f"\n{'─' * 70}")
        print(f"Problem: {instance_id}")
        print(f"Query: {query}")
        print()

        # Query both graphs
        baseline_results = query_graph(baseline_driver, query, embedder, top_k=10)
        repospec_results = query_graph(repospec_driver, query, embedder, top_k=10)

        # Check if any repo-specific result appears in enhanced graph
        repo_results = [r for r in repospec_results if r["prefix"] == "repo"]
        any_repo_found = len(repo_results) > 0
        repo_in_top5 = any(
            r["prefix"] == "repo" for r in repospec_results[:5]
        )

        if any_repo_found:
            repo_specific_found += 1
        if repo_in_top5:
            repo_specific_in_top5 += 1
        total_tested += 1

        # Show top 5 from enhanced graph
        print("  Enhanced graph top-5:")
        for i, r in enumerate(repospec_results[:5], 1):
            marker = " *** REPO-SPECIFIC ***" if r["prefix"] == "repo" else ""
            print(
                f"    {i}. [{r['id']}] score={r['score']:.4f}{marker}"
            )
            print(f"       {r['text'][:100]}...")

        if repo_results:
            print(f"\n  Repo-specific results found (in top 10):")
            for r in repo_results:
                print(
                    f"    [{r['id']}] score={r['score']:.4f} "
                    f"instance={r['instance_id']}"
                )
                print(f"    {r['text'][:120]}...")

        # Show top result from baseline for comparison
        if baseline_results:
            print(f"\n  Baseline top-1:")
            b = baseline_results[0]
            print(f"    [{b['id']}] score={b['score']:.4f}: {b['text'][:100]}...")

    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}")
    print(f"Problems tested: {total_tested}")
    print(f"Problems with repo-specific result in top-10: {repo_specific_found}")
    print(f"Problems with repo-specific result in top-5: {repo_specific_in_top5}")
    print(
        f"Repo-specific hit rate (top-10): "
        f"{repo_specific_found}/{total_tested} = "
        f"{repo_specific_found/total_tested*100:.1f}%"
    )
    print(
        f"Repo-specific hit rate (top-5): "
        f"{repo_specific_in_top5}/{total_tested} = "
        f"{repo_specific_in_top5/total_tested*100:.1f}%"
    )

    baseline_driver.close()
    repospec_driver.close()


if __name__ == "__main__":
    main()
