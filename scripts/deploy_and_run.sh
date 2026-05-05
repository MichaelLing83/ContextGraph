#!/bin/bash
# One-shot deployment script for remote server.
# Handles: preflight (ExpeL rules + Agent-KB index) then parallel experiments.
# Usage: ssh jie 'cd ~/codes/ContextGraph && bash scripts/deploy_and_run.sh'

export PATH="$HOME/.local/bin:$PATH"
set -e

PROJECT_DIR="$HOME/codes/ContextGraph"
cd "$PROJECT_DIR"

SESSION="baselines"
LOG_DIR="$PROJECT_DIR/results/baseline_comparison"
mkdir -p "$LOG_DIR" data/baselines

echo "============================================"
echo "  ContextGraph Baseline Comparison"
echo "  50 problems × 5 methods × cost_limit=$50"
echo "============================================"
echo ""

# --- Step 1: Infrastructure check ---
echo "[1/4] Checking infrastructure..."
docker compose up -d 2>&1 | tail -3

# Wait for Neo4j
for i in $(seq 1 30); do
  if uv run python -c "from neo4j import GraphDatabase; d=GraphDatabase.driver('bolt://localhost:7687', auth=('neo4j','contextgraph123')); d.verify_connectivity(); d.close()" 2>/dev/null; then
    echo "  ✓ Neo4j ready"
    break
  fi
  sleep 2
done

# Wait for LiteLLM
source .env
for i in $(seq 1 20); do
  if curl -sf -H "Authorization: Bearer $LITELLM_MASTER_KEY" http://localhost:4000/health >/dev/null 2>&1; then
    echo "  ✓ LiteLLM ready"
    break
  fi
  # health endpoint doesn't need auth, but the proxy needs time
  if curl -sf http://localhost:4000/health 2>/dev/null | grep -q "error"; then
    echo "  ✓ LiteLLM ready (auth required but proxy up)"
    break
  fi
  sleep 3
done

# Quick API test
echo "  Testing yunwu API..."
RESP=$(curl -s http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.4","messages":[{"role":"user","content":"say ok"}],"max_tokens":3}' 2>&1)
if echo "$RESP" | grep -q "choices"; then
  echo "  ✓ Yunwu API working"
else
  echo "  ✗ API failed: $RESP"
  exit 1
fi

# --- Step 2: Prepare ExpeL rules (if not exists) ---
echo ""
echo "[2/4] Preparing ExpeL rules..."
if [ -f data/baselines/expel_rules.json ]; then
  N_RULES=$(uv run python -c "import json; print(len(json.load(open('data/baselines/expel_rules.json')).get('rules', json.load(open('data/baselines/expel_rules.json')))))")
  echo "  ✓ Already exists ($N_RULES rules)"
else
  echo "  Extracting rules (this calls LLM, ~5-10 min)..."
  uv run python scripts/baselines/expel_baseline.py extract \
    --max-pairs 15 --max-rules 20 \
    2>&1 | tee "$LOG_DIR/expel_extraction.log" | tail -5
  echo "  ✓ Rules extracted"
fi

# --- Step 3: Build Agent-KB index ---
echo ""
echo "[3/4] Building Agent-KB index..."
if [ -f data/baselines/agentkb_index.json ]; then
  echo "  ✓ Already exists"
else
  uv run python scripts/baselines/agentkb_baseline.py build-from-neo4j \
    2>&1 | tee "$LOG_DIR/agentkb_build.log" | tail -3
  echo "  ✓ Index built"
fi

# --- Step 4: Launch parallel experiments in tmux ---
echo ""
echo "[4/4] Launching parallel experiments in tmux..."

# Kill existing session
tmux kill-session -t "$SESSION" 2>/dev/null || true

# Start Agent-KB server in background (needed for agentkb method)
tmux new-session -d -s "$SESSION" -n "agentkb_srv" \
  "cd $PROJECT_DIR && export PATH=$HOME/.local/bin:\$PATH && uv run python scripts/baselines/agentkb_server.py 2>&1 | tee $LOG_DIR/agentkb_server.log"

sleep 3  # Let server start

# Launch 5 experiment windows
METHODS="no_memory expel faiss agentkb contextgraph"
for method in $METHODS; do
  tmux new-window -t "$SESSION" -n "$method" \
    "cd $PROJECT_DIR && export PATH=$HOME/.local/bin:\$PATH && echo 'Starting $method at $(date)' && uv run python scripts/run_baseline_comparison.py run $method --n-workers 2 2>&1 | tee $LOG_DIR/${method}.log; echo 'DONE: $method at $(date)'; sleep 86400"
done

# Monitor window
tmux new-window -t "$SESSION" -n "monitor" \
  "cd $PROJECT_DIR && watch -n 60 'echo \"=== Progress ($(date)) ===\"; for m in no_memory expel faiss agentkb contextgraph; do printf \"  %-15s\" \$m; f=results/baseline_comparison/\$m/output/preds.jsonl; if [ -f \$f ]; then echo \"\$(wc -l < \$f)/50\"; else echo \"waiting...\"; fi; done; echo; echo \"=== Docker ===\"; docker ps --format \"{{.Names}}: {{.Status}}\"'"

echo ""
echo "============================================"
echo "  ✓ All 5 experiments launched in tmux!"
echo "============================================"
echo ""
echo "  tmux attach -t baselines        # attach to session"
echo "  tmux select-window -t baselines:monitor  # view progress"
echo ""
echo "  Methods running in parallel:"
for m in $METHODS; do
  echo "    - $m (2 workers)"
done
echo ""
echo "  Results will be in: results/baseline_comparison/"
echo "  Logs: results/baseline_comparison/*.log"
echo ""
echo "  To analyze after completion:"
echo "    uv run python scripts/run_baseline_comparison.py analyze"
echo ""
