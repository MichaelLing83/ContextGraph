# Debugging Lessons & Known Issues

## SWE-agent Tool Bundle Resolution
- SWE-agent resolves bundle paths via `_convert_path_to_abspath()` using `REPO_ROOT` (= SWE-agent install dir, e.g., `~/codes/SWE-agent/`)
- A config like `path: tools/query_memory` resolves to `~/codes/SWE-agent/tools/query_memory/`, NOT the project's `tools/query_memory/`
- **Fix**: Copy bundles to SWE-agent's tools dir: `cp -r tools/query_memory ~/codes/SWE-agent/tools/query_memory`

## SWE-agent `redo_existing=False` Trap
- When `redo_existing=False` (default), SWE-agent skips instances with existing output directories
- If old batch runs left empty/incomplete output dirs, sequential runs will skip them all
- **Fix**: Always `rm -rf` stale output directories before re-running experiments
- **Symptom**: "No trajectory output" for most instances, only first few new ones processed

## SWE-agent Sequential vs Batch Mode
- `--num-workers 1` → `run_group_sequential()` → calls `run_swe_agent_single()` per instance
- `--num-workers >1` → `run_group_batch()` → calls `run_swe_agent_batch()` with all instances
- Fixes applied to one function may be missing from the other (e.g., docker_args)
- **Always check both code paths** when making changes to the runner

## Neo4j Schema Reality vs Code Assumptions
- **Actual graph schema** (built by `writer.py` / `build_context_graph.py`):
  - Nodes: `Trajectory`, `Fragment`, `ErrorPattern` (NO `Methodology`)
  - Relationships: `HAS_FRAGMENT`, `CAUSED_ERROR` (NO `RESOLVED_BY`)
  - Properties: `success` on Trajectory, `error_type`/`message` on ErrorPattern
- **Original retriever assumed**: `Methodology` nodes, `RESOLVED_BY` relationships, `confidence` property — all nonexistent
- **Lesson**: Always verify Neo4j schema with Cypher queries before writing retriever code:
  ```cypher
  CALL db.labels() YIELD label RETURN label;
  CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType;
  MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count;
  ```

## ChatAnywhere Rate Limits
- Free tier supports ~6-9 concurrent API callers
- With 15+ callers, get 429 errors with 500+ second backoff suggestions
- Control (6 callers: 2 workers × 3 attempts) + Treatment (3 callers: 1 worker × 3 attempts) = 9 total → safe
- **Never run treatment with >1 worker per attempt while control is running**

## Docker on Linux: host.docker.internal
- On Linux, `host.docker.internal` is NOT automatically available (unlike macOS/Windows Docker Desktop)
- Must pass `--add-host=host.docker.internal:host-gateway` via docker_args
- In SWE-agent config/script: `--instances.deployment.docker_args '["--add-host=host.docker.internal:host-gateway"]'`

## install.sh Behavior in Treatment Docker Containers
- `tools/query_memory/install.sh` copies from `$CONTEXT_GRAPH_ROOT/agent_memory` at Docker container startup
- This means fixes to `agent_memory/` on host are automatically picked up by new containers
- BUT the `lib/` bundled copy is used as fallback — keep both in sync
