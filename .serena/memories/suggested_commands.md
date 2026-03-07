# Suggested Commands

## Environment Setup
```bash
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e '.[dev]'
uv pip install datasets python-dotenv openhands-ai
cd ~/codes/SWE-agent && uv pip install -e .
```

## Testing
```bash
pytest                                    # All tests
pytest tests/test_models.py               # Specific file
pytest tests/test_playbook.py -v          # Playbook tests
pytest -k "test_pass"                     # Pattern match
```

## Neo4j Graph Build Pipeline
```bash
# 1. Start Neo4j (persistent volume)
docker run -d --name neo4j-contextgraph -v neo4j-contextgraph-data:/data \
  -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/contextgraph123 neo4j:5

# 2. Build base graph (~12 min)
python scripts/build_context_graph.py

# 3. Strategy extraction + dedup + playbook
python scripts/extract_strategies.py
python scripts/deduplicate_strategies.py
python scripts/ingest_playbook.py

# 4. Re-embed all nodes (text-embedding-3-large, 3072 dim)
python scripts/reembed_all_nodes.py --batch-size 50
# For specific labels only:
python scripts/reembed_all_nodes.py --batch-size 50 --labels Fragment Strategy
# Dry run:
python scripts/reembed_all_nodes.py --dry-run
```

## Neo4j Verification
```bash
# Quick check via cypher-shell or Python
python3 -c "
from neo4j import GraphDatabase
d = GraphDatabase.driver('bolt://localhost:7687', auth=('neo4j', 'contextgraph123'))
with d.session() as s:
    for r in s.run('MATCH (n) RETURN labels(n)[0] AS l, count(n) AS c ORDER BY c DESC').data():
        print(r)
d.close()
"
```

## Running Experiments

### SWE-agent (Claude via ChatAnywhere)
```bash
# Control (2 workers, 3 attempts)
nohup .venv/bin/python scripts/run_real_swe_experiment.py --group control --num-workers 2 --attempt 1 > control_a1.log 2>&1 &
# Treatment (1 worker to avoid rate limits)
nohup .venv/bin/python scripts/run_real_swe_experiment.py --group treatment --num-workers 1 --attempt 1 > treatment_a1.log 2>&1 &
```

### SWE-agent (GLM-4.7)
```bash
python -m sweagent run-batch --config configs/glm47_control.yaml
python -m sweagent run-batch --config configs/glm47_treatment.yaml
```

### Rewriter Ablation
```bash
nohup .venv/bin/python scripts/run_rewriter_experiment.py --group both --attempts 1 --n 10 > pilot.log 2>&1 &
```

### Analysis
```bash
python results/live_experiment/run_live_analysis.py
python scripts/analyze_online_learning.py --results results/rewriter_ablation/results.json
```

## Sync Tool Bundle (after modifying agent_memory/)
```bash
cp agent_memory/*.py tools/query_memory/lib/agent_memory/
```

## Git
```bash
git status && git diff
git log --oneline -10
git push origin <branch>
```
