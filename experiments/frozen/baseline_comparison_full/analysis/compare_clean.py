#!/usr/bin/env python3
"""Compare contextgraph_clean vs original contextgraph on the 42 test instances."""
import json

# Load clean results
with open("/home/jie/codes/ContextGraph/output.contextgraph_clean.json") as f:
    clean = json.load(f)
clean_resolved = set(clean["resolved_ids"])
clean_completed = set(clean["completed_ids"])

# Load original contextgraph results
with open("/home/jie/codes/ContextGraph/gpt-5.4-contextgraph.contextgraph_v2.json") as f:
    orig = json.load(f)
orig_resolved = set(orig["resolved_ids"])

# Load other method results for context
methods_data = {}
for m in ["no_memory", "faiss", "expel", "agentkb"]:
    with open(f"/home/jie/codes/ContextGraph/gpt-5.4-{m}.{m}_v2.json") as f:
        d = json.load(f)
    methods_data[m] = set(d["resolved_ids"])

# The 42 test instances
with open("/home/jie/codes/ContextGraph/results/baseline_comparison_full/cg_retest_instances.json") as f:
    test_instances = set(json.load(f)["instance_ids"])

print(f"Test set: {len(test_instances)} instances")
print(f"Clean completed: {len(clean_completed)}")
print()

# Results on the 42 test instances
orig_on_test = orig_resolved & test_instances
clean_on_test = clean_resolved & test_instances
nm_on_test = methods_data["no_memory"] & test_instances
faiss_on_test = methods_data["faiss"] & test_instances

print("=== Results on 42 test instances ===")
print(f"  contextgraph (original): {len(orig_on_test)}/42 = {len(orig_on_test)/42*100:.1f}%")
print(f"  contextgraph (clean):    {len(clean_on_test)}/42 = {len(clean_on_test)/42*100:.1f}%")
print(f"  no_memory:               {len(nm_on_test)}/42 = {len(nm_on_test)/42*100:.1f}%")
print(f"  faiss:                   {len(faiss_on_test)}/42 = {len(faiss_on_test)/42*100:.1f}%")
print()

# Delta
gained = clean_on_test - orig_on_test
lost = orig_on_test - clean_on_test
print(f"  Clean GAINED (orig failed, clean solved): {len(gained)}")
for iid in sorted(gained):
    also_by = [m for m in ["no_memory", "faiss", "expel", "agentkb"] if iid in methods_data[m]]
    print(f"    + {iid} (also solved by: {also_by})")

print(f"\n  Clean LOST (orig solved, clean failed): {len(lost)}")
for iid in sorted(lost):
    also_by = [m for m in ["no_memory", "faiss", "expel", "agentkb"] if iid in methods_data[m]]
    print(f"    - {iid} (also solved by: {also_by})")

print(f"\n  Net change: {len(gained) - len(lost):+d}")

# Project to full 500
# Original CG: 318/500. The 42 test instances had X resolved.
# Clean: same as original on the other 458, but different on the 42.
orig_other = len(orig_resolved - test_instances)
clean_projected = orig_other + len(clean_on_test)
print(f"\n=== Projected full-500 results ===")
print(f"  Original CG:    {len(orig_resolved)}/500 = {len(orig_resolved)/500*100:.1f}%")
print(f"  Clean CG:       {clean_projected}/500 = {clean_projected/500*100:.1f}%")
print(f"  faiss:           {len(methods_data['faiss'] & set(orig['completed_ids']))}/500 = {len(methods_data['faiss'])}/500 = {len(methods_data['faiss'])/500*100:.1f}%")
print(f"  no_memory:       {len(methods_data['no_memory'])}/500 = {len(methods_data['no_memory'])/500*100:.1f}%")

# Per-instance comparison
print("\n=== Per-instance detail (42 test instances) ===")
print(f"{'Instance':<45} {'Orig':>5} {'Clean':>6} {'NM':>5} {'FAISS':>6} {'Delta':>6}")
print("-" * 80)
for iid in sorted(test_instances):
    o = "Y" if iid in orig_on_test else "N"
    c = "Y" if iid in clean_on_test else "N"
    n = "Y" if iid in nm_on_test else "N"
    f_val = "Y" if iid in faiss_on_test else "N"
    if o != c:
        delta = "+1" if c == "Y" else "-1"
    else:
        delta = ""
    print(f"  {iid:<43} {o:>5} {c:>6} {n:>5} {f_val:>6} {delta:>6}")
