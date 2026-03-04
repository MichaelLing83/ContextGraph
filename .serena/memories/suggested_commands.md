# Suggested Commands

## Environment Setup
```bash
# Create virtual environment (user prefers uv)
uv venv .venv --python 3.12
source .venv/bin/activate

# Install project in editable mode with dev deps
uv pip install -e '.[dev]'

# Install extra dependencies
uv pip install datasets python-dotenv openhands-ai

# Install SWE-agent (editable, from local clone)
cd ~/codes/SWE-agent && uv pip install -e .
```

## Testing
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_models.py

# Run specific test class/method
pytest tests/evaluation/test_metrics.py::TestProblemResult::test_pass_at_1_success

# Run tests with verbose output
pytest -v

# Run tests matching pattern
pytest -k "test_pass"
```

## Infrastructure
```bash
# Start Neo4j container
docker run -d --name neo4j-contextgraph -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/contextgraph123 neo4j:5

# Check Neo4j status
docker ps | grep neo4j

# Build context graph from training trajectories (~12 min)
python scripts/build_context_graph.py
```

## Running Experiments
```bash
# Run SWE-agent control (3 attempts in background, 2 workers each)
nohup .venv/bin/python scripts/run_real_swe_experiment.py --group control --num-workers 2 --attempt 1 > results/live_experiment/control_stdout.log 2>&1 &
nohup .venv/bin/python scripts/run_real_swe_experiment.py --group control --num-workers 2 --attempt 2 > results/live_experiment/control_attempt2.log 2>&1 &
nohup .venv/bin/python scripts/run_real_swe_experiment.py --group control --num-workers 2 --attempt 3 > results/live_experiment/control_attempt3.log 2>&1 &

# Run SWE-agent treatment (3 attempts, 1 worker each to avoid rate limits)
# IMPORTANT: Clean stale output dirs first!
rm -rf results/live_experiment/swe_agent_treatment results/live_experiment/swe_agent_treatment_attempt2 results/live_experiment/swe_agent_treatment_attempt3
nohup .venv/bin/python scripts/run_real_swe_experiment.py --group treatment --num-workers 1 --attempt 1 > results/live_experiment/treatment_stdout.log 2>&1 &
nohup .venv/bin/python scripts/run_real_swe_experiment.py --group treatment --num-workers 1 --attempt 2 > results/live_experiment/treatment_attempt2.log 2>&1 &
nohup .venv/bin/python scripts/run_real_swe_experiment.py --group treatment --num-workers 1 --attempt 3 > results/live_experiment/treatment_attempt3.log 2>&1 &

# Monitor progress
tail -f results/live_experiment/treatment_stdout.log
ls results/live_experiment/swe_agent_treatment/ | wc -l  # count completed

# Run OpenHands A/B experiment
python scripts/run_real_openhands_experiment.py --n 200

# Analyze results
python results/live_experiment/run_live_analysis.py

# Prepare train/test split
python scripts/prepare_split.py

# Collect results
python scripts/collect_swe_agent_results.py
python scripts/collect_openhands_results.py
```

## SWE-agent CLI
```bash
# Run SWE-agent batch (NOT python -m sweagent.run.run)
python -m sweagent run-batch --config configs/swe_agent_control.yaml
python -m sweagent run-batch --config configs/swe_agent_treatment.yaml
```

## Git
```bash
git status
git diff
git log --oneline -10
git add <file>
git commit -m "message"
git push origin <branch>
```

## System Utilities (Linux)
```bash
ls, cd, pwd, mkdir, rm, cp, mv
grep, find, cat, head, tail
docker ps, docker logs, docker exec
```

## Rewriter Ablation Experiment
```bash
# Pilot: 10 problems, 1 attempt (quick validation)
nohup .venv/bin/python scripts/run_rewriter_experiment.py \
  --group both --attempts 1 --n 10 \
  > results/rewriter_ablation/pilot.log 2>&1 &

# Full experiment: 200 problems, 3 attempts (pass@k / pass^k)
nohup .venv/bin/python scripts/run_rewriter_experiment.py \
  --group control --attempts 3 --n 200 \
  > results/rewriter_ablation/control.log 2>&1 &
nohup .venv/bin/python scripts/run_rewriter_experiment.py \
  --group treatment --attempts 3 --n 200 \
  > results/rewriter_ablation/treatment.log 2>&1 &

# Analyze results (same format as online learning)
python scripts/analyze_online_learning.py \
  --results results/rewriter_ablation/results.json

# Dry run (verify setup)
python scripts/run_rewriter_experiment.py --dry-run --n 2 --attempts 1
```

## Notes
- Always use `uv` instead of `pip` for package management
- `.env` file must exist with `ANTHROPIC_API_KEY` and `ANTHROPIC_API_BASE`
- Neo4j graph must be rebuilt after container recreation
- On Linux, Docker needs `--add-host=host.docker.internal:host-gateway` for Neo4j access from containers
