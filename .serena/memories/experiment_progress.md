# A/B Experiment Execution Progress

## Current Status (2026-02-27)

### Context Graph (Neo4j)
- **Status**: Rebuilt and verified (2026-02-27)
- **Container**: `neo4j-contextgraph` with named volume `neo4j-contextgraph-data`
- **Nodes**: 16,194 total (1,795 Trajectory, 13,813 Fragment, 586 ErrorPattern)
- **Docker Root**: Migrated to `/home/jie/ext0/docker` (1.5T available)

### Control Group (SWE-agent, no memory)
- **Status**: Running (3 concurrent processes, attempts 1-3)
- **Progress**: Attempt 1 had 71/200, now resuming 129 remaining
- **Progress**: Attempts 2,3 had 61/200 each, resuming 139 remaining
- **PIDs**: 71848, 72173, 72176
- **Logs**: `control_resume_attempt{1,2,3}.log`

### Treatment Group (SWE-agent + QueryMemoryTool)
- **Status**: Running (3 concurrent processes, fresh start after fixes)
- **Progress**: All 3 attempts started fresh (200 instances each)
- **PIDs**: 98814, 98817, 98820
- **Logs**: `treatment_v3_attempt{1,2,3}.log`
- **query_memory**: VERIFIED WORKING (returns 5 fragments, Neo4j connected)

## Critical Fixes Applied (2026-02-27)

### 5. install.sh Bash Quoting Fix
- **Problem**: `pip install --quiet neo4j>=5.0.0` — bash interprets `>=` as redirect
- **Fix**: `pip install --quiet "neo4j>=5.0.0"` (added quotes)
- **Synced to**: `tools/query_memory/install.sh` AND `SWE-agent/tools/query_memory/install.sh`

### 6. retriever.py Out-of-Sync in SWE-agent Bundle
- **Problem**: `/home/jie/codes/ContextGraph/SWE-agent/tools/query_memory/lib/agent_memory/retriever.py` was the OLD version querying non-existent RESOLVED_BY/Methodology
- **Fix**: Synced all files from `tools/query_memory/lib/agent_memory/` to SWE-agent bundle
- **Key**: Always sync after modifying `agent_memory/*.py`

### 7. Docker Disk Space (Root Cause of Exit Code 102)
- **Problem**: `/` partition was full → `docker build` failed with exit code 102
- **Fix**: User migrated Docker Root Dir to `/home/jie/ext0/docker` (1.5T NVMe)
- **Verified**: Docker build now succeeds

### 8. neo4j Module Location (SWE-bench Containers)
- **Finding**: SWE-bench containers have `testbed` conda env (Python 3.9) active via PATH
- **pip install** inside SWE-agent installs to testbed env, not base miniconda (3.11)
- **query_memory** shebang `#!/usr/bin/env python3` resolves to testbed's python3
- **docker exec** uses base miniconda PATH (different from SWE-agent's runtime)
- **Implication**: Testing with `docker exec` may show different results than actual runtime

## Next Steps (TODO)
1. **Monitor runs**: Check progress every few hours, watch for rate limiting or failures
2. **After all 200 complete (both groups, all 3 attempts)**: Collect results
3. **Run swebench evaluation**: Evaluate patches for correctness (if not done by SWE-agent)
4. **Statistical analysis**: pass@k, McNemar paired test via `run_live_analysis.py`
5. **After SWE-agent done**: Run OpenHands A/B experiment

## Key Files Modified (not yet committed)
- `agent_memory/retriever.py` — Complete rewrite for actual Neo4j schema
- `scripts/run_real_swe_experiment.py` — docker_args fix in `run_swe_agent_single()`
- `tools/query_memory/lib/agent_memory/retriever.py` — Synced copy
- `~/codes/SWE-agent/tools/query_memory/` — Copied bundle (external to this repo)
