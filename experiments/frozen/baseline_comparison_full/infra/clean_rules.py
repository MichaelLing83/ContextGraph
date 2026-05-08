#!/usr/bin/env python3
"""Identify and remove garbage rules from the clean Neo4j instance.

Criteria for removal:
1. member_count = 1 (single source, not a pattern)
2. Appears in >10% of trajectories (overly generic via embedding similarity)
3. References specific repos/files not relevant to SWE-bench

Also remove rules that are near-duplicates of high-member-count rules
(e.g., "create a minimal reproduction script" appearing 5+ times).
"""
import json
import os
import re
from collections import Counter
from neo4j import GraphDatabase

# Count rule frequencies from trajectories
base = "/home/jie/codes/ContextGraph/results/baseline_comparison_full/contextgraph/output"
rule_counter = Counter()
traj_count = 0

for iid_dir in sorted(os.listdir(base)):
    traj_file = os.path.join(base, iid_dir, f"{iid_dir}.traj")
    if not os.path.exists(traj_file):
        continue
    traj_count += 1
    with open(traj_file) as f:
        content = f.read()
    rules = set(re.findall(r'\[rule_([a-f0-9]+)\]', content))
    for r in rules:
        rule_counter[f"rule_{r}"] += 1

print(f"Scanned {traj_count} trajectories")
print()

# Connect to clean Neo4j (port 7691)
driver = GraphDatabase.driver("bolt://localhost:7691", auth=("neo4j", "contextgraph123"))

# Find rules to remove
rules_to_remove = []
with driver.session() as s:
    # Get all CanonicalRules with member_count
    result = s.run("""
        MATCH (n:CanonicalRule)
        RETURN n.id AS id, n.rule_text AS text, n.member_count AS mc, n.category AS cat
    """)

    for rec in result:
        rule_id = rec["id"]
        mc = rec["mc"] or 0
        text = rec["text"] or ""
        hit_rate = rule_counter.get(rule_id, 0) / traj_count if traj_count > 0 else 0

        remove = False
        reason = ""

        # Criterion 1: member_count=1 AND hit_rate > 10%
        if mc <= 1 and hit_rate > 0.10:
            remove = True
            reason = f"mc={mc}, hit_rate={hit_rate:.1%} (low-quality but over-retrieved)"

        # Criterion 2: references specific repo files irrelevant to SWE-bench
        if any(kw in text.lower() for kw in ["dvc/repo", "dvc/", "airflow/", "ansible/"]):
            remove = True
            reason = f"references repo-specific path not in SWE-bench: {text[:80]}"

        if remove:
            rules_to_remove.append((rule_id, reason, text[:120], hit_rate, mc))

print(f"Rules to remove: {len(rules_to_remove)}")
for rid, reason, text, hr, mc in rules_to_remove:
    print(f"  {rid}: mc={mc}, hit_rate={hr:.1%}")
    print(f"    Text: {text}")
    print(f"    Reason: {reason}")
print()

# Remove the rules and their relationships
with driver.session() as s:
    for rid, reason, _, _, _ in rules_to_remove:
        result = s.run("""
            MATCH (n:CanonicalRule {id: $rid})
            OPTIONAL MATCH (n)-[r]-()
            DELETE r, n
            RETURN count(r) AS rels_deleted
        """, rid=rid)
        rec = result.single()
        print(f"  Deleted {rid}: {rec['rels_deleted']} relationships removed")

    # Also delete PlaybookEntry nodes that reference removed rules
    for rid, _, _, _, _ in rules_to_remove:
        result = s.run("""
            MATCH (p:PlaybookEntry)
            WHERE p.text CONTAINS $rid
            OPTIONAL MATCH (p)-[r]-()
            DELETE r, p
            RETURN count(p) AS deleted
        """, rid=rid)
        rec = result.single()
        if rec["deleted"] > 0:
            print(f"  Deleted {rec['deleted']} PlaybookEntry nodes referencing {rid}")

    # Count remaining nodes
    result = s.run("MATCH (n:CanonicalRule) RETURN count(n) AS cnt")
    print(f"\nRemaining CanonicalRules: {result.single()['cnt']}")

    result = s.run("MATCH (n:PlaybookEntry) RETURN count(n) AS cnt")
    print(f"Remaining PlaybookEntries: {result.single()['cnt']}")

    result = s.run("MATCH (n) RETURN count(n) AS cnt")
    print(f"Total nodes: {result.single()['cnt']}")

driver.close()
