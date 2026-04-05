#!/bin/bash
# live-SWE-agent batch A/B experiment runner
# Usage:
#   ./scripts/run_liveswe_batch_ab.sh control   # run control group
#   ./scripts/run_liveswe_batch_ab.sh treatment  # run treatment (with memory)
#
# Prerequisites:
#   - mini-swe-agent: /tmp/mini-swe-venv/bin/mini-extra
#   - OPENROUTER_API_KEY set
#   - Neo4j running on port 7690 (for treatment)
#   - Docker running

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GROUP="${1:-control}"
WORKERS="${2:-10}"
OUTPUT="/tmp/liveswe-500-${GROUP}"
MINI="/tmp/mini-swe-venv/bin/mini-extra"
MODEL="google/gemini-3.1-pro-preview"
CONFIG="${REPO_ROOT}/configs/liveswe_gemini3_control.yaml"

export MSWEA_CONFIGURED=1
export MSWEA_DOCKER_EXECUTABLE=/usr/local/bin/docker

echo "=== live-SWE-agent batch: ${GROUP} ==="
echo "Output: ${OUTPUT}"
echo "Workers: ${WORKERS}"
echo "Model: ${MODEL}"

if [ "$GROUP" = "treatment" ]; then
    echo "Running treatment batch (with ContextGraph memory)..."
    echo "Neo4j port: ${NEO4J_PORT:-7690}"

    # Install agent_memory in mini-swe-venv if not present
    /tmp/mini-swe-venv/bin/python -c "import agent_memory" 2>/dev/null || \
        uv pip install --python /tmp/mini-swe-venv/bin/python -e "${REPO_ROOT}" 2>/dev/null

    # Monkey-patch ProgressTrackingAgent to inject memory, then run swebench
    export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"
    /tmp/mini-swe-venv/bin/python -c "
import sys; sys.argv = sys.argv[1:]
from scripts.patch_treatment import enable; enable()
from minisweagent.run.benchmarks.swebench import main; main()
" -- swebench \
        --subset princeton-nlp/SWE-bench_Verified \
        --split test \
        --config "${CONFIG}" \
        -c agent.mode=yolo \
        -c agent.cost_limit=5 \
        -m "${MODEL}" \
        --model-class openrouter_textbased \
        --environment-class docker \
        -w "${WORKERS}" \
        -o "${OUTPUT}" \
        2>&1 | tee "${OUTPUT}.log"
else
    # Control: standard run
    echo "Running control batch..."
    $MINI swebench \
        --subset princeton-nlp/SWE-bench_Verified \
        --split test \
        --config "${CONFIG}" \
        -c agent.mode=yolo \
        -c agent.cost_limit=5 \
        -m "${MODEL}" \
        --model-class openrouter_textbased \
        --environment-class docker \
        -w "${WORKERS}" \
        -o "${OUTPUT}" \
        2>&1 | tee "${OUTPUT}.log"
fi

echo "=== Done: ${GROUP} ==="
