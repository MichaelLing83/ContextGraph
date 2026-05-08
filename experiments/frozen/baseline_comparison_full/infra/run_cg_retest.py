#!/usr/bin/env python3
"""Re-run contextgraph on CG-HURT+FAISS-WINS instances using clean Neo4j (port 8004)."""
import json
import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path("/home/jie/codes/ContextGraph")
CONFIGS_DIR = PROJECT_ROOT / "configs"
RESULTS_DIR = PROJECT_ROOT / "results" / "baseline_comparison_full" / "contextgraph_clean"
SUBSET_FILE = PROJECT_ROOT / "results" / "baseline_comparison_full" / "cg_retest_instances.json"

RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Generate config pointing to port 8004 (clean server)
treatment = (CONFIGS_DIR / "swe_agent_treatment.yaml").read_text()
content = treatment.replace(
    "      NEO4J_URI: bolt://neo4j-contextgraph:7687\n"
    "      NEO4J_USER: neo4j\n"
    "      NEO4J_PASSWORD: INJECTED_AT_RUNTIME",
    "      MEMORY_SERVER_URL: http://172.17.0.1:8004",
)
content = content.replace(
    "per_instance_cost_limit: 0",
    "per_instance_cost_limit: 0.0",
)

config_path = RESULTS_DIR / "config.yaml"
config_path.write_text(content)

# Load instance IDs
with open(SUBSET_FILE) as f:
    data = json.load(f)
instance_ids = data["instance_ids"]

print(f"Instances to re-test: {len(instance_ids)}")
print(f"Config: {config_path}")
print(f"Output: {RESULTS_DIR / 'output'}")

# Build filter string
filter_str = "|".join(instance_ids)

cmd = [
    sys.executable, "-m", "sweagent", "run-batch",
    "--config", str(config_path),
    "--instances.type", "swe_bench",
    "--instances.subset", "verified",
    "--instances.split", "test",
    "--instances.filter", filter_str,
    "--output_dir", str(RESULTS_DIR / "output"),
    "--num_workers", "1",
]

print(f"\nCommand: {' '.join(cmd)}")
print(f"Starting at {datetime.now().isoformat()}")

log_file = RESULTS_DIR / "run.log"
with open(log_file, "w") as log:
    proc = subprocess.Popen(
        cmd,
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
    )
    print(f"PID: {proc.pid}")
    print(f"Log: {log_file}")
    proc.wait()
    print(f"Exit code: {proc.returncode}")
    print(f"Finished at {datetime.now().isoformat()}")
