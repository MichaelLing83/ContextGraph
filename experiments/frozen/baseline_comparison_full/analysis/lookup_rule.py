#!/usr/bin/env python3
from neo4j import GraphDatabase
d = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "contextgraph123"))
with d.session() as s:
    # Look up the dominant rule
    r = s.run('MATCH (n:CanonicalRule {id: "rule_c866de1ede28"}) RETURN n.rule_text AS text, n.category AS cat, n.member_count AS mc')
    for rec in r:
        print(f"Category: {rec['cat']}")
        print(f"Member count: {rec['mc']}")
        print(f"Rule text: {rec['text']}")

    print("\n--- Top 10 most retrieved rules across ALL trajectories ---")
    # Count which rules appear most often in CG trajectories
    # Let's also look at the top rules by member_count (generality)
    r = s.run('MATCH (n:CanonicalRule) RETURN n.id AS id, n.rule_text AS text, n.member_count AS mc, n.category AS cat ORDER BY n.member_count DESC LIMIT 10')
    for rec in r:
        print(f"  [{rec['id']}] mc={rec['mc']}, cat={rec['cat']}")
        print(f"    {rec['text'][:150]}")
d.close()
