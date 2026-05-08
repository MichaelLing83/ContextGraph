#!/usr/bin/env python3
"""Analyze rule frequency across contextgraph trajectories to find noise sources."""
import json
import os
import re
from collections import Counter

base = "/home/jie/codes/ContextGraph/results/baseline_comparison_full/contextgraph/output"

# Count rule appearances across all trajectories
rule_counter = Counter()
traj_count = 0
traj_with_memory = 0

for iid_dir in sorted(os.listdir(base)):
    traj_file = os.path.join(base, iid_dir, f"{iid_dir}.traj")
    if not os.path.exists(traj_file):
        continue

    traj_count += 1
    with open(traj_file) as f:
        content = f.read()

    # Find all rule IDs mentioned
    rules = re.findall(r'\[rule_([a-f0-9]+)\]', content)
    if rules:
        traj_with_memory += 1
    for r in set(rules):  # unique per trajectory
        rule_counter[f"rule_{r}"] += 1

print(f"Total trajectories: {traj_count}")
print(f"Trajectories with memory advice: {traj_with_memory} ({traj_with_memory/traj_count*100:.1f}%)")
print()

print("=== Top 20 most frequently appearing rules ===")
for rule_id, count in rule_counter.most_common(20):
    pct = count / traj_count * 100
    print(f"  {rule_id}: {count}/{traj_count} ({pct:.1f}%)")

print()

# How many unique rules appear?
print(f"Total unique rules appearing: {len(rule_counter)}")
print(f"Rules appearing in >50% trajectories: {sum(1 for _, c in rule_counter.items() if c > traj_count/2)}")
print(f"Rules appearing in >25% trajectories: {sum(1 for _, c in rule_counter.items() if c > traj_count/4)}")
print(f"Rules appearing in >10% trajectories: {sum(1 for _, c in rule_counter.items() if c > traj_count/10)}")
print(f"Rules appearing in <5% trajectories: {sum(1 for _, c in rule_counter.items() if c < traj_count/20)}")

# Entropy analysis: how diverse is the memory advice?
total_appearances = sum(rule_counter.values())
print(f"\nTotal rule appearances: {total_appearances}")
print(f"Average rules per trajectory: {total_appearances/traj_with_memory:.1f}")

# Top 5 rules account for what fraction?
top5_total = sum(c for _, c in rule_counter.most_common(5))
print(f"Top 5 rules account for: {top5_total}/{total_appearances} = {top5_total/total_appearances*100:.1f}% of all appearances")

top10_total = sum(c for _, c in rule_counter.most_common(10))
print(f"Top 10 rules account for: {top10_total}/{total_appearances} = {top10_total/total_appearances*100:.1f}% of all appearances")

# Now compare: for CG-HURT instances, what rules appeared?
print("\n=== Rule frequency in CG-HURT vs CG-WIN instances ===")
with open("/home/jie/codes/ContextGraph/gpt-5.4-no_memory.no_memory_v2.json") as f:
    nm_data = json.load(f)
with open("/home/jie/codes/ContextGraph/gpt-5.4-contextgraph.contextgraph_v2.json") as f:
    cg_data = json.load(f)

nm_resolved = set(nm_data["resolved_ids"])
cg_resolved = set(cg_data["resolved_ids"])
cg_completed = set(cg_data["completed_ids"])
nm_completed = set(nm_data["completed_ids"])
common = cg_completed & nm_completed

cg_hurt = sorted((nm_resolved - cg_resolved) & common)
cg_wins = sorted((cg_resolved - nm_resolved) & common)
both_won = sorted(cg_resolved & nm_resolved & common)

def get_rules_for_set(instance_set):
    """Get rule frequency for a set of instances."""
    counter = Counter()
    n = 0
    for iid in instance_set:
        traj_file = os.path.join(base, iid, f"{iid}.traj")
        if not os.path.exists(traj_file):
            continue
        n += 1
        with open(traj_file) as f:
            content = f.read()
        rules = set(re.findall(r'\[rule_([a-f0-9]+)\]', content))
        for r in rules:
            counter[f"rule_{r}"] += 1
    return counter, n

hurt_rules, hurt_n = get_rules_for_set(cg_hurt)
win_rules, win_n = get_rules_for_set(cg_wins)
both_rules, both_n = get_rules_for_set(both_won)

print(f"\nCG-HURT ({hurt_n} instances): top rules")
for rule_id, count in hurt_rules.most_common(10):
    print(f"  {rule_id}: {count}/{hurt_n} ({count/hurt_n*100:.1f}%)")

print(f"\nCG-WINS ({win_n} instances): top rules")
for rule_id, count in win_rules.most_common(10):
    print(f"  {rule_id}: {count}/{win_n} ({count/win_n*100:.1f}%)")

print(f"\nBoth won ({both_n} instances): top rules")
for rule_id, count in both_rules.most_common(5):
    print(f"  {rule_id}: {count}/{both_n} ({count/both_n*100:.1f}%)")

# Check if the same rule dominates all categories equally
print("\n=== rule_c866de1ede28 prevalence by outcome ===")
dom_rule = "rule_c866de1ede28"
print(f"  CG-HURT: {hurt_rules.get(dom_rule, 0)}/{hurt_n} = {hurt_rules.get(dom_rule, 0)/hurt_n*100:.1f}%")
print(f"  CG-WINS: {win_rules.get(dom_rule, 0)}/{win_n} = {win_rules.get(dom_rule, 0)/win_n*100:.1f}%")
print(f"  Both won: {both_rules.get(dom_rule, 0)}/{both_n} = {both_rules.get(dom_rule, 0)/both_n*100:.1f}%")
