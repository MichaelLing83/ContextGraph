#!/usr/bin/env python3
"""Inject strategies learned from OpenCode A/B case study failures into the graph.

These strategies were derived from analyzing 3 problems where the agent
produced patches that didn't pass SWE-bench verification:
- django__django-13513: Fixed symptom not root cause
- sympy__sympy-14531: Fixed only one method, polluted diff with unrelated changes
- matplotlib__matplotlib-24870: Used instance flag instead of function signature
"""

import hashlib
import os

from agent_memory.neo4j_store import Neo4jStore
from agent_memory.embeddings import get_embedding_client

STRATEGIES = [
    # From sympy-14531 failure: only fixed 1 of N similar methods
    {
        "section": "STRATEGIES AND HARD RULES",
        "prefix": "shr",
        "text": (
            "When fixing a bug in a class method that follows a pattern "
            "(e.g., _print_Relational not calling self._print()), search "
            "for ALL other methods in the same class with the identical "
            "anti-pattern using grep. Fix every occurrence, not just the "
            "one mentioned in the bug report."
        ),
        "category": "pattern_completeness",
    },
    # From sympy-14531 failure: polluted diff with Python compat fixes
    {
        "section": "COMMON MISTAKES AND CORRECT STRATEGIES",
        "prefix": "cms",
        "text": (
            "Do NOT modify files unrelated to the reported bug. If the "
            "codebase has Python version compatibility issues (e.g., "
            "collections.Mapping moved to collections.abc), ignore them. "
            "Your patch must only touch files and lines directly needed "
            "to fix the bug. Unrelated changes cause test failures."
        ),
        "category": "patch_hygiene",
    },
    # From django-13513 failure: fixed display logic not traversal logic
    {
        "section": "STRATEGIES AND HARD RULES",
        "prefix": "shr",
        "text": (
            "When fixing exception handling in web framework debug views, "
            "trace the exception chain traversal function (e.g., "
            "explicit_or_implicit_cause) rather than the template display "
            "variables. The root cause is usually in how exceptions are "
            "collected, not how they are rendered."
        ),
        "category": "root_cause_analysis",
    },
    # From django-13513 failure: general root cause principle
    {
        "section": "STRATEGIES AND HARD RULES",
        "prefix": "shr",
        "text": (
            "Fix the ROOT CAUSE, not the symptom. When a value displays "
            "incorrectly, the bug is often in the function that computes "
            "the value, not in the code that reads it. Trace backwards "
            "from the symptom to the origin."
        ),
        "category": "root_cause_analysis",
    },
    # From matplotlib-24870 failure: used flag instead of parameter
    {
        "section": "COMMON MISTAKES AND CORRECT STRATEGIES",
        "prefix": "cms",
        "text": (
            "When passing state between methods during processing, prefer "
            "adding a parameter to the function signature over storing "
            "state in an instance variable (self._flag). Instance flags "
            "create ordering dependencies and are harder to maintain."
        ),
        "category": "code_design",
    },
    # From matplotlib-24870 failure: missed second file
    {
        "section": "PROBLEM-SOLVING HEURISTICS AND WORKFLOWS",
        "prefix": "psw",
        "text": (
            "After fixing a function, search for all callers of that "
            "function using grep. If you changed the function signature "
            "(added a parameter), every call site must be updated. "
            "Missing a call site causes test failures."
        ),
        "category": "completeness_check",
    },
    # General: verification without compilation
    {
        "section": "VERIFICATION CHECKLIST",
        "prefix": "verify",
        "text": (
            "When you cannot compile or install the project (e.g., C "
            "extensions fail to build), verify your fix using source "
            "code analysis: AST parsing, grep for pattern completeness, "
            "and standalone reproduction scripts. Do not waste time "
            "trying to fix build environments."
        ),
        "category": "verification",
    },
]


def main():
    # Default to enhanced graph (port 7688) to avoid modifying the original
    store = Neo4jStore(
        uri=os.environ.get("NEO4J_URI", "bolt://localhost:7688"),
        auth=(
            os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", "contextgraph123"),
        ),
    )
    embedder = get_embedding_client(
        "openai",
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_API_BASE", "https://api.chatanywhere.org"),
        model="text-embedding-3-large",
    )

    # Get next ID for each prefix
    result = store.driver.execute_query(
        "MATCH (p:PlaybookEntry) RETURN p.prefix AS prefix, max(p.id) AS max_id"
    )
    max_ids = {}
    for r in result.records:
        prefix = r["prefix"]
        max_id = r["max_id"]
        if max_id and "-" in max_id:
            num = int(max_id.split("-")[1])
            max_ids[prefix] = num

    injected = 0
    for s in STRATEGIES:
        prefix = s["prefix"]
        next_num = max_ids.get(prefix, 0) + 1
        entry_id = f"{prefix}-{next_num:05d}"
        max_ids[prefix] = next_num

        # Generate embedding
        embedding = embedder.embed(s["text"])

        # Check for duplicates (cosine similarity > 0.95)
        dup_result = store.driver.execute_query(
            """
            CALL db.index.vector.queryNodes('playbook_embedding', 3, $embedding)
            YIELD node, score
            WHERE score > 0.95
            RETURN node.id AS id, node.text AS text, score
            """,
            {"embedding": embedding},
        )
        if dup_result.records:
            existing = dup_result.records[0]
            print(f"SKIP (duplicate, score={existing['score']:.3f}): {s['text'][:80]}...")
            print(f"  Existing: {existing['text'][:80]}...")
            continue

        # Insert
        store.driver.execute_query(
            """
            CREATE (p:PlaybookEntry {
                id: $id,
                text: $text,
                section: $section,
                prefix: $prefix,
                embedding: $embedding
            })
            """,
            {
                "id": entry_id,
                "text": s["text"],
                "section": s["section"],
                "prefix": s["prefix"],
                "embedding": embedding,
            },
        )
        print(f"INJECTED [{entry_id}] ({s['section']}): {s['text'][:80]}...")
        injected += 1

    store.driver.close()
    print(f"\nDone: {injected} strategies injected, {len(STRATEGIES) - injected} skipped (duplicates)")


if __name__ == "__main__":
    main()
