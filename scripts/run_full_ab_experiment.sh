#!/bin/bash
# Full A/B experiment: treatment then control, with SWE-bench evaluation + email notification.
# Usage: GMAIL_APP_KEY='...' nohup bash scripts/run_full_ab_experiment.sh > /tmp/full_ab_experiment.log 2>&1 &
# Supports resume: re-run the same command to pick up where it left off.

set -uo pipefail  # no -e: don't abort on individual instance failures

PROJECT_ROOT="/home/jie/codes/ContextGraph"
cd "$PROJECT_ROOT"
source .venv/bin/activate
source .env

export CONTEXT_GRAPH_ROOT="$PROJECT_ROOT"

RESULTS_BASE="$PROJECT_ROOT/results/live_experiment"
TREATMENT_DIR="$RESULTS_BASE/swe_agent_treatment"
CONTROL_DIR="$RESULTS_BASE/swe_agent_control"

GMAIL_USER="wzh4464@gmail.com"
GMAIL_APP_KEY="${GMAIL_APP_KEY:?Set GMAIL_APP_KEY env var}"

send_email() {
    local subject="$1"
    local body="$2"
    python3 - "$subject" "$body" "$GMAIL_USER" "$GMAIL_APP_KEY" <<'PYEOF'
import smtplib, sys
from email.mime.text import MIMEText

subject, body, user, key = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
msg = MIMEText(body, "plain", "utf-8")
msg["Subject"] = subject
msg["From"] = user
msg["To"] = user

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
    s.login(user, key)
    s.send_message(msg)
print(f"Email sent: {subject}")
PYEOF
}

collect_stats() {
    local group_dir="$1"
    local group_name="$2"
    python3 - "$group_dir" "$group_name" <<'PYEOF'
import json, os, sys, glob

group_dir = sys.argv[1]
group_name = sys.argv[2]
dirs = sorted(glob.glob(f"{group_dir}/*/"))
total = 0; submitted = 0; cost = 0; qm_calls = 0; qm_useful = 0

for d in dirs:
    iid = os.path.basename(d.rstrip("/"))
    traj_path = os.path.join(d, f"{iid}.traj")
    if not os.path.exists(traj_path):
        continue
    try:
        t = json.load(open(traj_path))
        info = t.get("info", {})
        stats = info.get("model_stats", {})
        trajectory = t.get("trajectory", [])
        es = info.get("exit_status", "")
        total += 1
        if es and "submit" in str(es):
            submitted += 1
        cost += stats.get("instance_cost", 0)
        for s in trajectory:
            if "query_memory" in s.get("action", ""):
                qm_calls += 1
                obs = s.get("observation", "")
                if "STRATEGIES" in obs or "SIMILAR_PROBLEMS" in obs or "PAST_EXPERIENCES" in obs:
                    qm_useful += 1
    except:
        pass

print(f"{group_name}: {total} completed, {submitted} submitted, ${cost:.2f} total cost")
if qm_calls > 0:
    print(f"  query_memory: {qm_calls} calls, {qm_useful} useful ({qm_useful/qm_calls*100:.0f}%)")
PYEOF
}

evaluate_group() {
    local group_dir="$1"
    local group_name="$2"
    local run_id="contextgraph_${group_name}_$(date +%Y%m%d%H%M%S)"

    echo "=== Merging predictions for $group_name ==="
    python3 -c "
from sweagent.run.merge_predictions import merge_predictions
from pathlib import Path
merge_predictions([Path('$group_dir')], Path('$group_dir/preds.json'))
print('Merged predictions to $group_dir/preds.json')
"

    echo "=== Running SWE-bench evaluation for $group_name ==="
    python3 -m swebench.harness.run_evaluation \
        --dataset_name princeton-nlp/SWE-bench_Verified \
        --split test \
        --predictions_path "$group_dir/preds.json" \
        --max_workers 4 \
        --run_id "$run_id" \
        --report_dir "$group_dir/eval_reports" \
        --timeout 1800 \
        --cache_level env

    echo "=== Evaluation complete for $group_name ==="

    # Extract results
    local report_file
    report_file=$(ls "$group_dir/eval_reports/"*.json 2>/dev/null | head -1)
    if [ -n "$report_file" ]; then
        cp "$report_file" "$group_dir/eval_results.json"
        python3 -c "
import json
r = json.load(open('$group_dir/eval_results.json'))
resolved = r.get('resolved_ids', r.get('resolved', []))
total = r.get('total_instances', r.get('submitted_ids', []))
if isinstance(total, list):
    total = len(total)
if isinstance(resolved, list):
    n_resolved = len(resolved)
else:
    n_resolved = resolved
print(f'$group_name: {n_resolved}/{total} resolved')
"
    else
        echo "WARNING: No evaluation report found for $group_name"
    fi
}

# ============================================================
# PHASE 1: TREATMENT GROUP
# ============================================================
echo "============================================================"
echo "PHASE 1: TREATMENT GROUP (200 problems)"
echo "Started at $(date)"
echo "============================================================"

# Resume-friendly: don't delete existing results
mkdir -p "$TREATMENT_DIR"

python scripts/run_real_swe_experiment.py \
    --group treatment \
    --max-problems 200 || true

echo "=== Treatment run complete at $(date) ==="
collect_stats "$TREATMENT_DIR" "treatment"

# Evaluate
evaluate_group "$TREATMENT_DIR" "treatment"

# Collect final stats for email
TREATMENT_STATS=$(collect_stats "$TREATMENT_DIR" "treatment" 2>&1)
TREATMENT_EVAL=""
if [ -f "$TREATMENT_DIR/eval_results.json" ]; then
    TREATMENT_EVAL=$(python3 -c "
import json
r = json.load(open('$TREATMENT_DIR/eval_results.json'))
resolved = r.get('resolved_ids', r.get('resolved', []))
total = r.get('total_instances', r.get('submitted_ids', []))
if isinstance(total, list): total = len(total)
if isinstance(resolved, list): n = len(resolved)
else: n = resolved
print(f'Resolved: {n}/{total} ({n/max(total,1)*100:.1f}%)')
" 2>&1)
fi

send_email \
    "[ContextGraph] Treatment group complete" \
    "Treatment group (200 problems) finished at $(date).

$TREATMENT_STATS
$TREATMENT_EVAL

Starting control group next."

# ============================================================
# PHASE 2: CONTROL GROUP
# ============================================================
echo "============================================================"
echo "PHASE 2: CONTROL GROUP (200 problems)"
echo "Started at $(date)"
echo "============================================================"

mkdir -p "$CONTROL_DIR"

python scripts/run_real_swe_experiment.py \
    --group control \
    --max-problems 200 || true

echo "=== Control run complete at $(date) ==="
collect_stats "$CONTROL_DIR" "control"

# Evaluate
evaluate_group "$CONTROL_DIR" "control"

# Final email
CONTROL_STATS=$(collect_stats "$CONTROL_DIR" "control" 2>&1)
CONTROL_EVAL=""
if [ -f "$CONTROL_DIR/eval_results.json" ]; then
    CONTROL_EVAL=$(python3 -c "
import json
r = json.load(open('$CONTROL_DIR/eval_results.json'))
resolved = r.get('resolved_ids', r.get('resolved', []))
total = r.get('total_instances', r.get('submitted_ids', []))
if isinstance(total, list): total = len(total)
if isinstance(resolved, list): n = len(resolved)
else: n = resolved
print(f'Resolved: {n}/{total} ({n/max(total,1)*100:.1f}%)')
" 2>&1)
fi

send_email \
    "[ContextGraph] Full A/B experiment complete" \
    "Both groups finished at $(date).

=== TREATMENT ===
$TREATMENT_STATS
$TREATMENT_EVAL

=== CONTROL ===
$CONTROL_STATS
$CONTROL_EVAL

Results at: $RESULTS_BASE/"

echo "============================================================"
echo "FULL EXPERIMENT COMPLETE at $(date)"
echo "============================================================"
