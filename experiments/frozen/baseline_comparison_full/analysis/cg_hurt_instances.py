#!/usr/bin/env python3
"""Export CG-HURT instance IDs for targeted re-run."""
import json

data = {}
for m in ["no_memory", "contextgraph"]:
    with open(f"/home/jie/codes/ContextGraph/gpt-5.4-{m}.{m}_v2.json") as f:
        d = json.load(f)
    data[m] = {"resolved": set(d["resolved_ids"]), "completed": set(d["completed_ids"])}

common = data["no_memory"]["completed"] & data["contextgraph"]["completed"]
nm_r = data["no_memory"]["resolved"] & common
cg_r = data["contextgraph"]["resolved"] & common

# CG-HURT: no_memory solved but CG didn't
cg_hurt = sorted((nm_r - cg_r) & common)

# Also include faiss-only wins (faiss solved, CG didn't)
with open("/home/jie/codes/ContextGraph/gpt-5.4-faiss.faiss_v2.json") as f:
    faiss_data = json.load(f)
faiss_r = set(faiss_data["resolved_ids"]) & common

faiss_wins = sorted((faiss_r - cg_r) & common)

# Union of both sets
test_set = sorted(set(cg_hurt) | set(faiss_wins))

print(f"CG-HURT: {len(cg_hurt)}")
print(f"FAISS-WINS: {len(faiss_wins)}")
print(f"Union (test set): {len(test_set)}")

# Save to JSON
output = {"instance_ids": test_set, "count": len(test_set)}
with open("/home/jie/codes/ContextGraph/results/baseline_comparison_full/cg_retest_instances.json", "w") as f:
    json.dump(output, f, indent=2)

print(f"Saved to cg_retest_instances.json")
