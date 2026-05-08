#!/usr/bin/env python3
"""McNemar paired statistical test on v2 SWE-bench results."""
import json
import math
from itertools import combinations

methods = ["no_memory", "expel", "faiss", "agentkb", "contextgraph"]
data = {}
for m in methods:
    with open(f"/home/jie/codes/ContextGraph/gpt-5.4-{m}.{m}_v2.json") as f:
        d = json.load(f)
    data[m] = {
        "resolved": set(d["resolved_ids"]),
        "completed": set(d["completed_ids"]),
        "submitted": set(d["submitted_ids"]),
    }

# Find common evaluated instances
common = None
for m in methods:
    if common is None:
        common = data[m]["completed"]
    else:
        common = common & data[m]["completed"]
print(f"Common completed instances: {len(common)}")
print()

# Results over common set
print("=== Resolution Rate (common set) ===")
for m in methods:
    resolved = data[m]["resolved"] & common
    print(f"  {m:20s}: {len(resolved)}/{len(common)} = {len(resolved)/len(common)*100:.1f}%")
print()

# Results over all 500
print("=== Resolution Rate (all 500) ===")
for m in methods:
    resolved = data[m]["resolved"]
    print(f"  {m:20s}: {len(resolved)}/500 = {len(resolved)/500*100:.1f}%")
print()

# McNemar test
print("=== McNemar Paired Test (all pairs) ===")
header = f"  {'Pair':35s} {'b(A+B-)':>8s} {'c(A-B+)':>8s} {'chi2':>8s} {'p-value':>8s} {'sig':>4s}"
print(header)
print("  " + "-" * 70)

for m1, m2 in combinations(methods, 2):
    r1 = data[m1]["resolved"] & common
    r2 = data[m2]["resolved"] & common
    b = len(r1 - r2)  # m1 solved, m2 not
    c = len(r2 - r1)  # m2 solved, m1 not
    if b + c == 0:
        chi2 = 0.0
        p = 1.0
    else:
        chi2 = (abs(b - c) - 1) ** 2 / (b + c)
        z = math.sqrt(chi2)
        p = math.erfc(z / math.sqrt(2))
    sig = "**" if p < 0.01 else ("*" if p < 0.05 else ("~" if p < 0.1 else ""))
    label = f"{m1} vs {m2}"
    print(f"  {label:35s} {b:>8d} {c:>8d} {chi2:>8.3f} {p:>8.4f} {sig:>4s}")

print()
print("=== Unique Wins/Losses vs no_memory ===")
baseline = data["no_memory"]["resolved"] & common
for m in methods[1:]:
    r = data[m]["resolved"] & common
    gained = r - baseline
    lost = baseline - r
    net = len(gained) - len(lost)
    print(f"  {m:20s}: +{len(gained)} gained, -{len(lost)} lost (net {net:+d})")

print()
print("=== Uniquely Solved (only this method solved it) ===")
for m in methods:
    r = data[m]["resolved"] & common
    unique = r.copy()
    for m2 in methods:
        if m2 != m:
            unique -= data[m2]["resolved"]
    print(f"  {m:20s}: {len(unique)} uniquely solved")

any_solved = set()
for m in methods:
    any_solved |= (data[m]["resolved"] & common)
all_solved = common.copy()
for m in methods:
    all_solved &= data[m]["resolved"]

print()
print(f"  Solved by at least one: {len(any_solved)}/{len(common)} = {len(any_solved)/len(common)*100:.1f}%")
print(f"  Solved by ALL methods:  {len(all_solved)}/{len(common)} = {len(all_solved)/len(common)*100:.1f}%")

# Overlap matrix
print()
print("=== Pairwise Overlap (both solved) ===")
print(f"  {'':20s}", end="")
for m in methods:
    print(f" {m[:8]:>8s}", end="")
print()
for m1 in methods:
    r1 = data[m1]["resolved"] & common
    print(f"  {m1:20s}", end="")
    for m2 in methods:
        r2 = data[m2]["resolved"] & common
        overlap = len(r1 & r2)
        print(f" {overlap:>8d}", end="")
    print()
