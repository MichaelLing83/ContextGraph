# Debugging Lessons & Known Issues

## SWE-agent Tool Bundle Resolution (CRITICAL)
- SWE-agent resolves bundle paths via `_convert_path_to_abspath()` using `REPO_ROOT` (= SWE-agent install dir, e.g., `~/codes/SWE-agent/`)
- A config like `path: tools/query_memory` resolves to `~/codes/SWE-agent/tools/query_memory/`, NOT the project's `tools/query_memory/`
- **Fix**: Use absolute path in YAML config: `path: /home/jie/codes/ContextGraph/tools/query_memory`

## SWE-bench Testbed Python Version
- SWE-bench Docker images have a `testbed` conda env (can be Python 3.6) activated via `.bashrc`
- `#!/usr/bin/env python3` resolves to testbed's Python (3.6), causing SyntaxError on `from __future__ import annotations` in neo4j package
- **Fix**: Bash wrapper that explicitly uses `/opt/miniconda3/bin/python3` (Python 3.11+)
- **Important**: Do NOT use `exec` in the wrapper — it replaces the pexpect bash session, causing EOF when Python exits. Use normal subprocess call with `exit $?`
- **Important**: Do NOT export PYTHONPATH from `install.sh` — it gets sourced into the bash session and pollutes testbed Python 3.6

## SWE-agent `redo_existing=False` Trap
- When `redo_existing=False` (default), SWE-agent skips instances with existing output directories
- If old batch runs left empty/incomplete output dirs, sequential runs will skip them all
- **Fix**: Always `rm -rf` stale output directories before re-running experiments

## SWE-agent Sequential vs Batch Mode
- `--num-workers 1` → `run_group_sequential()` → `run_swe_agent_single()` per instance
- `--num-workers >1` → `run_group_batch()` → `run_swe_agent_batch()` with all instances
- **Always check both code paths** when making changes to the runner

## Neo4j Schema Reality
- **Actual nodes**: Trajectory, Fragment, ErrorPattern, Strategy, CanonicalRule, PlaybookEntry, Community, ProblemSummary
- **Actual relationships**: HAS_FRAGMENT, CAUSED_ERROR, DERIVED_FROM, MERGED_INTO, ADDRESSES_ERROR, IN_COMMUNITY, SUMMARIZES
- **No** Methodology nodes or RESOLVED_BY relationships (old code assumed these)
- **Always verify** with `CALL db.labels()` and `CALL db.relationshipTypes()` before writing retriever code

## Neo4j Node Property Names
- Fragment text field is `description` (NOT `content`)
- Strategy text field is `rule_text`
- PlaybookEntry text field is `text`
- ProblemSummary text field is `summary_text`
- ErrorPattern combines `error_type` + `error_keywords`

## ChatAnywhere Rate Limits
- Free tier supports ~6-9 concurrent API callers
- With 15+ callers, get 429 errors with 500+ second backoff
- Control (6: 2 workers × 3 attempts) + Treatment (3: 1 worker × 3 attempts) = 9 → safe
- **Never run treatment with >1 worker per attempt while control is running**

## Docker on Linux: host.docker.internal
- Not automatically available (unlike macOS/Windows Docker Desktop)
- Must pass `--add-host=host.docker.internal:host-gateway` via docker_args

## install.sh in Treatment Docker Containers
- Copies from `$CONTEXT_GRAPH_ROOT/agent_memory` at container startup
- Fixes to `agent_memory/` on host are automatically picked up by NEW containers
- BUT the `lib/` bundled copy is used as fallback — keep both in sync
