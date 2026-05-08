#!/usr/bin/env python3
"""Deep analysis of contextgraph losses vs faiss — find memory influence."""
import json
import os
import re

# Load v2 results
data = {}
for m in ["no_memory", "expel", "faiss", "agentkb", "contextgraph"]:
    with open(f"/home/jie/codes/ContextGraph/gpt-5.4-{m}.{m}_v2.json") as f:
        d = json.load(f)
    data[m] = {"resolved": set(d["resolved_ids"]), "completed": set(d["completed_ids"])}

common = None
for m in data:
    common = data[m]["completed"] if common is None else common & data[m]["completed"]

faiss_r = data["faiss"]["resolved"] & common
cg_r = data["contextgraph"]["resolved"] & common
nm_r = data["no_memory"]["resolved"] & common

# CG HURT = no_memory solved but CG didn't
cg_hurt = sorted((nm_r - cg_r) & common)
# CG MISSED = faiss solved, no_memory didn't, CG didn't
cg_missed = sorted((faiss_r - cg_r - nm_r) & common)
# CG UNIQUE WINS = CG solved, no_memory didn't
cg_unique_wins = sorted((cg_r - nm_r) & common)

base = "/home/jie/codes/ContextGraph/results/baseline_comparison_full"

def analyze_traj(method, iid):
    """Extract key info from a trajectory."""
    traj_file = f"{base}/{method}/output/{iid}/{iid}.traj"
    if not os.path.exists(traj_file):
        return None
    with open(traj_file) as f:
        d = json.load(f)

    info = d.get("info", {})
    history = d.get("history", [])
    trajectory = d.get("trajectory", [])

    # Find query_memory calls and responses
    memory_queries = []
    memory_responses = []
    for i, msg in enumerate(history):
        content = msg.get("content", "")
        role = msg.get("role", "")
        if isinstance(content, str):
            if "query_memory" in content and role == "assistant":
                memory_queries.append((i, content[:300]))
            if role == "tool" and ("playbook" in content.lower() or "strategy" in content.lower() or "canonical" in content.lower()):
                memory_responses.append((i, content))

    # Count actions
    n_steps = len(trajectory)
    edit_count = sum(1 for t in trajectory if "edit" in str(t.get("action", "")).lower())

    # Model stats
    stats = info.get("model_stats", {})
    total_cost = stats.get("instance_cost", 0)

    return {
        "steps": n_steps,
        "edits": edit_count,
        "exit": info.get("exit_status", "?"),
        "cost": total_cost,
        "memory_queries": memory_queries,
        "memory_responses": memory_responses,
        "history_len": len(history),
    }


print("=" * 80)
print(f"=== CG-HURT: no_memory solved, CG didn't ({len(cg_hurt)} instances) ===")
print("=" * 80)

hurt_categories = {"memory_noise": [], "longer_traj": [], "same_effort": [], "unknown": []}

for iid in cg_hurt:
    cg_info = analyze_traj("contextgraph", iid)
    nm_info = analyze_traj("no_memory", iid)

    if not cg_info or not nm_info:
        continue

    print(f"\n{'='*60}")
    print(f"  {iid}")
    print(f"  CG: {cg_info['steps']} steps, {cg_info['edits']} edits, cost=${cg_info['cost']:.2f}, exit={cg_info['exit']}")
    print(f"  NM: {nm_info['steps']} steps, {nm_info['edits']} edits, cost=${nm_info['cost']:.2f}, exit={nm_info['exit']}")
    print(f"  CG memory queries: {len(cg_info['memory_queries'])}")

    # Analyze memory responses
    for idx, resp in cg_info["memory_responses"][:2]:
        # Extract the key advice
        lines = resp.split('\n')
        advice_lines = []
        for line in lines:
            ll = line.lower().strip()
            if any(kw in ll for kw in ['##', 'strategy', 'rule', 'pattern', 'fix', 'approach',
                                        'error_type', 'canonical', 'playbook', 'section']):
                advice_lines.append(line.strip())
        if advice_lines:
            print(f"  Memory advice (msg {idx}):")
            for al in advice_lines[:6]:
                print(f"    {al[:120]}")
        else:
            # Show first bit
            preview = resp[:400].replace('\n', ' | ')
            print(f"  Memory response preview: {preview[:200]}")

    # Categorize
    if cg_info["steps"] > nm_info["steps"] * 1.5:
        hurt_categories["longer_traj"].append(iid)
        print(f"  >> CATEGORY: longer_traj (CG took {cg_info['steps']-nm_info['steps']} more steps)")
    elif len(cg_info["memory_responses"]) > 0:
        hurt_categories["memory_noise"].append(iid)
        print(f"  >> CATEGORY: memory_noise (got memory advice, still failed)")
    else:
        hurt_categories["unknown"].append(iid)
        print(f"  >> CATEGORY: unknown")


print("\n")
print("=" * 80)
print("=== CATEGORY SUMMARY (CG-HURT) ===")
print("=" * 80)
for cat, ids in hurt_categories.items():
    print(f"  {cat}: {len(ids)}")
    for iid in ids:
        print(f"    - {iid}")

print("\n")
print("=" * 80)
print(f"=== CG-MISSED: faiss solved, neither NM nor CG ({len(cg_missed)} instances) ===")
print("=" * 80)
for iid in cg_missed:
    cg_info = analyze_traj("contextgraph", iid)
    faiss_info = analyze_traj("faiss", iid)
    if not cg_info or not faiss_info:
        continue
    print(f"\n  {iid}")
    print(f"    CG: {cg_info['steps']} steps, exit={cg_info['exit']}, memory_queries={len(cg_info['memory_queries'])}")
    print(f"    FAISS: {faiss_info['steps']} steps, exit={faiss_info['exit']}")

print("\n")
print("=" * 80)
print(f"=== CG UNIQUE WINS: CG solved, NM didn't ({len(cg_unique_wins)} instances) ===")
print("=" * 80)
for iid in cg_unique_wins:
    cg_info = analyze_traj("contextgraph", iid)
    nm_info = analyze_traj("no_memory", iid)
    if not cg_info or not nm_info:
        continue
    print(f"\n  {iid}")
    print(f"    CG: {cg_info['steps']} steps, exit={cg_info['exit']}, memory_queries={len(cg_info['memory_queries'])}")
    print(f"    NM: {nm_info['steps']} steps, exit={nm_info['exit']}")

    for idx, resp in cg_info["memory_responses"][:1]:
        lines = resp.split('\n')
        advice_lines = [l.strip() for l in lines if any(kw in l.lower() for kw in ['##', 'strategy', 'rule', 'fix', 'approach', 'canonical', 'playbook'])]
        if advice_lines:
            print(f"    Memory advice helped:")
            for al in advice_lines[:4]:
                print(f"      {al[:120]}")

# Final summary
print("\n")
print("=" * 80)
print("=== OVERALL ROOT CAUSE SUMMARY ===")
print("=" * 80)
total_cg_hurt = len(cg_hurt)
total_faiss_wins = 27
total_cg_missed = len(cg_missed)
print(f"Total faiss-wins over CG: {total_faiss_wins}")
print(f"  CG-HURT (NM solved, CG didn't): {total_cg_hurt}")
print(f"  CG-MISSED (only faiss solved): {total_cg_missed}")
print(f"  Other (some methods solved): {total_faiss_wins - total_cg_hurt - total_cg_missed}")
