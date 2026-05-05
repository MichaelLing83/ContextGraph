#!/bin/bash
# Run 5-way baseline comparison in parallel using tmux on remote server.
# Usage: ssh jie 'cd ~/codes/ContextGraph && bash scripts/run_baseline_remote.sh'
#
# This script creates a tmux session "baselines" with 5 windows,
# each running one baseline method. Survives disconnection.

set -e

SESSION="baselines"
PROJECT_DIR="$HOME/codes/ContextGraph"
cd "$PROJECT_DIR"

# Kill existing session if any
tmux kill-session -t "$SESSION" 2>/dev/null || true

# Ensure infrastructure is running
echo "=== Starting infrastructure ==="
docker compose up -d
sleep 5

# Verify Neo4j is up
echo "Waiting for Neo4j..."
for i in $(seq 1 30); do
  if python3 -c "from neo4j import GraphDatabase; d=GraphDatabase.driver('bolt://localhost:7687', auth=('neo4j','contextgraph123')); d.verify_connectivity(); d.close()" 2>/dev/null; then
    echo "Neo4j ready."
    break
  fi
  sleep 2
done

# Verify LiteLLM proxy
echo "Waiting for LiteLLM..."
for i in $(seq 1 20); do
  if curl -sf http://localhost:4000/health >/dev/null 2>&1; then
    echo "LiteLLM ready."
    break
  fi
  sleep 3
done

echo "=== Infrastructure ready ==="

# Create tmux session with first window (no_memory)
tmux new-session -d -s "$SESSION" -n "no_memory" \
  "cd $PROJECT_DIR && uv run python scripts/run_baseline_comparison.py run no_memory --n-workers 2 2>&1 | tee results/baseline_comparison/no_memory.log; echo 'DONE: no_memory'; read"

# Add windows for each method
tmux new-window -t "$SESSION" -n "expel" \
  "cd $PROJECT_DIR && uv run python scripts/run_baseline_comparison.py run expel --n-workers 2 2>&1 | tee results/baseline_comparison/expel.log; echo 'DONE: expel'; read"

tmux new-window -t "$SESSION" -n "faiss" \
  "cd $PROJECT_DIR && uv run python scripts/run_baseline_comparison.py run faiss --n-workers 2 2>&1 | tee results/baseline_comparison/faiss.log; echo 'DONE: faiss'; read"

tmux new-window -t "$SESSION" -n "agentkb" \
  "cd $PROJECT_DIR && uv run python scripts/run_baseline_comparison.py run agentkb --n-workers 2 2>&1 | tee results/baseline_comparison/agentkb.log; echo 'DONE: agentkb'; read"

tmux new-window -t "$SESSION" -n "contextgraph" \
  "cd $PROJECT_DIR && uv run python scripts/run_baseline_comparison.py run contextgraph --n-workers 2 2>&1 | tee results/baseline_comparison/contextgraph.log; echo 'DONE: contextgraph'; read"

# Add a monitor window
tmux new-window -t "$SESSION" -n "monitor" \
  "cd $PROJECT_DIR && watch -n 30 'echo \"=== Baseline Comparison Progress ===\"; for m in no_memory expel faiss agentkb contextgraph; do echo -n \"\$m: \"; if [ -f results/baseline_comparison/\$m/output/preds.jsonl ]; then wc -l < results/baseline_comparison/\$m/output/preds.jsonl; else echo \"not started\"; fi; done'"

echo ""
echo "=== tmux session '$SESSION' created with 5 parallel experiments ==="
echo ""
echo "To attach:  tmux attach -t $SESSION"
echo "To monitor: tmux select-window -t $SESSION:monitor"
echo "To check:   tmux list-windows -t $SESSION"
echo ""
echo "All 5 methods are running in parallel (2 workers each)."
echo "Session survives SSH disconnection."
