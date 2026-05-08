#!/usr/bin/env python3
"""Extract predictions from contextgraph_clean output and run SWE-bench eval."""
import json
import os
import subprocess
from pathlib import Path

base = Path("/home/jie/codes/ContextGraph/results/baseline_comparison_full/contextgraph_clean/output")

# Extract predictions
preds = {}
for iid_dir in sorted(base.iterdir()):
    if not iid_dir.is_dir():
        continue
    iid = iid_dir.name
    traj_file = iid_dir / f"{iid}.traj"
    if not traj_file.exists():
        continue

    with open(traj_file) as f:
        d = json.load(f)

    patch = d.get("info", {}).get("submission", "")
    preds[iid] = {
        "instance_id": iid,
        "model_patch": patch or "",
        "model_name_or_path": "gpt-5.4-contextgraph-clean",
    }

preds_file = base / "preds.json"
with open(preds_file, "w") as f:
    json.dump(preds, f, indent=2)

print(f"Extracted {len(preds)} predictions")
non_empty = sum(1 for p in preds.values() if p["model_patch"].strip())
print(f"Non-empty patches: {non_empty}/{len(preds)}")

# Run SWE-bench evaluation
print("\n=== Running SWE-bench evaluation ===")
cmd = [
    "/home/jie/codes/ContextGraph/.venv/bin/python", "-m",
    "swebench.harness.run_evaluation",
    "--predictions_path", str(preds_file),
    "--swe_bench_tasks", "princeton-nlp/SWE-bench_Verified",
    "--run_id", "contextgraph_clean",
    "--max_workers", "8",
    "--timeout", "300",
]
print(f"Command: {' '.join(cmd)}")

result = subprocess.run(cmd, capture_output=True, text=True, cwd="/home/jie/codes/ContextGraph")
print("STDOUT:", result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
if result.returncode != 0:
    print("STDERR:", result.stderr[-1000:])
print(f"Exit code: {result.returncode}")
