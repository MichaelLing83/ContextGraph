#!/usr/bin/env python3
"""Analyze instances where faiss solved but contextgraph didn't."""
import json
import os
import sys

# Load v2 results
methods = ["no_memory", "expel", "faiss", "agentkb", "contextgraph"]
data = {}
for m in methods:
    with open(f"/home/jie/codes/ContextGraph/gpt-5.4-{m}.{m}_v2.json") as f:
        d = json.load(f)
    data[m] = {
        "resolved": set(d["resolved_ids"]),
        "completed": set(d["completed_ids"]),
    }

common = None
for m in methods:
    common = data[m]["completed"] if common is None else common & data[m]["completed"]

faiss_r = data["faiss"]["resolved"] & common
cg_r = data["contextgraph"]["resolved"] & common
nm_r = data["no_memory"]["resolved"] & common
expel_r = data["expel"]["resolved"] & common
akb_r = data["agentkb"]["resolved"] & common

faiss_wins = sorted(faiss_r - cg_r)
cg_wins = sorted(cg_r - faiss_r)

print(f"=== faiss vs contextgraph (common={len(common)}) ===")
print(f"faiss only:  {len(faiss_wins)}")
print(f"cg only:     {len(cg_wins)}")
print()

# Cross-method analysis of faiss_wins
print("=== Who else solved the faiss-wins? ===")
cats = {"all_memory": [], "faiss_unique_mem": [], "nm_and_faiss": [], "only_faiss": []}
for iid in faiss_wins:
    solved_by = [m for m in methods if iid in data[m]["resolved"]]
    nm = iid in nm_r
    print(f"  {iid}: solved by {solved_by}")

print()
print("=== Classification ===")
nm_also = [iid for iid in faiss_wins if iid in nm_r]
nm_not = [iid for iid in faiss_wins if iid not in nm_r]
print(f"  no_memory also solved (CG HURT): {len(nm_also)}")
print(f"  no_memory didn't solve (CG missed opportunity): {len(nm_not)}")
print()

# Load predictions to check empty patches
base = "/home/jie/codes/ContextGraph/results/baseline_comparison_full"
with open(f"{base}/contextgraph/output/preds.json") as f:
    cg_preds = json.load(f)

cg_empty = []
cg_wrong = []
for iid in faiss_wins:
    patch = cg_preds.get(iid, {})
    if isinstance(patch, dict):
        p = patch.get("model_patch", "")
    else:
        p = str(patch)
    if not p or p.strip() == "":
        cg_empty.append(iid)
    else:
        cg_wrong.append(iid)

print(f"  CG empty patch: {len(cg_empty)}")
print(f"  CG wrong patch: {len(cg_wrong)}")
print()

# Repo distribution
print("=== Repo distribution: faiss-wins ===")
repos = {}
for iid in faiss_wins:
    repo = "__".join(iid.split("__")[:-1])
    repos[repo] = repos.get(repo, 0) + 1
for repo, count in sorted(repos.items(), key=lambda x: -x[1]):
    print(f"  {repo}: {count}")

print()
print("=== Repo distribution: cg-wins ===")
repos2 = {}
for iid in cg_wins:
    repo = "__".join(iid.split("__")[:-1])
    repos2[repo] = repos2.get(repo, 0) + 1
for repo, count in sorted(repos2.items(), key=lambda x: -x[1]):
    print(f"  {repo}: {count}")

print()
print("=" * 60)
print("=== Deep dive: CG-HURT cases (no_memory solved, CG didn't) ===")
print("=" * 60)

# These are the most interesting: no_memory solved them fine, but CG's memory hurt
for iid in sorted(nm_also):
    print(f"\n--- {iid} ---")

    # Read CG trajectory
    traj_file = f"{base}/contextgraph/output/{iid}/trajectory.json"
    if not os.path.exists(traj_file):
        print("  [no trajectory]")
        continue

    with open(traj_file) as f:
        traj = json.load(f)

    info = traj.get("info", {})
    steps = traj.get("trajectory", [])
    print(f"  Steps: {len(steps)}, Exit: {info.get('exit_status', '?')}")

    # Find memory query results in the trajectory
    for i, step in enumerate(steps):
        # Look at observation/output
        obs = step.get("observation", "") or step.get("output", "")
        action = step.get("action", "")

        # Check for query_memory in action
        if isinstance(action, str) and "query_memory" in action.lower():
            print(f"  Step {i}: MEMORY QUERY")
            # The response is typically in observation
            if isinstance(obs, str) and len(obs) > 50:
                # Extract key lines
                lines = obs.split('\n')
                # Find strategy/rule/pattern lines
                key_lines = []
                for line in lines:
                    ll = line.lower()
                    if any(kw in ll for kw in ['strategy:', 'rule:', 'pattern:', 'fix:', 'approach:', 'error:', '##', 'repository:', 'playbook']):
                        key_lines.append(line.strip())
                if key_lines:
                    print(f"    Memory advice:")
                    for kl in key_lines[:8]:
                        print(f"      {kl[:120]}")
                else:
                    # Just show first 300 chars
                    print(f"    Response preview: {obs[:300]}")

        # Check for edit/patch actions
        if isinstance(action, str) and ("edit" in action.lower() or "patch" in action.lower() or "sed" in action.lower()):
            if i >= len(steps) - 5:  # Last few steps
                print(f"  Step {i}: EDIT ACTION (near end)")
                print(f"    {action[:200]}")

    # Compare with no_memory trajectory
    nm_traj_file = f"{base}/no_memory/output/{iid}/trajectory.json"
    if os.path.exists(nm_traj_file):
        with open(nm_traj_file) as f:
            nm_traj = json.load(f)
        nm_steps = nm_traj.get("trajectory", [])
        nm_info = nm_traj.get("info", {})
        print(f"  no_memory: Steps={len(nm_steps)}, Exit={nm_info.get('exit_status', '?')}")
