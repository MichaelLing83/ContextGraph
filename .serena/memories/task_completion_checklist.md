# Task Completion Checklist

## Before Completing a Task

### Code Quality
- [ ] Code follows project style conventions (see `code_style_conventions` memory)
- [ ] Type hints on function signatures
- [ ] Docstrings on public classes and methods
- [ ] No print statements in library code (use logging)
- [ ] dataclasses used for data models (not Pydantic)

### Testing
- [ ] Run `pytest` to ensure all tests pass
- [ ] Add tests for new functionality in appropriate `tests/` location
- [ ] Tests use class-based organization with descriptive method names

### Validation
- [ ] No simulation experiments (only real agent runs)
- [ ] Changes are backward-compatible or all references updated
- [ ] No sensitive data (API keys, passwords) in committed code
- [ ] `.env` file is gitignored

### Integration Points
- [ ] If modifying `agent_memory/`, check if `tools/query_memory/lib/` bundle needs updating
- [ ] If modifying `agent_memory/`, ALSO sync to `~/codes/SWE-agent/tools/query_memory/lib/agent_memory/`
- [ ] If modifying data models, check `to_dict()`/`from_dict()` consistency
- [ ] If modifying Neo4j schema, graph may need rebuilding
- [ ] If modifying SWE-agent integration, update `configs/swe_agent_*.yaml` if needed

### Before Restarting Treatment Runs
- [ ] Delete ALL stale treatment output dirs (swe_agent_treatment, _attempt2, _attempt3)
- [ ] Verify retriever.py changes are synced to both bundle locations
- [ ] Verify Neo4j container is running with data intact (16,194 nodes expected)
- [ ] Verify docker_args fix is in both `run_swe_agent_single()` and `run_swe_agent_batch()`
- [ ] Keep total concurrent API callers ≤ 9 to avoid ChatAnywhere rate limits

### Commands to Run
```bash
# Run tests
pytest

# Check for import errors
python -c "import agent_memory"

# Verify specific module
python -c "from agent_memory.evaluation.metrics import calculate_metrics"
```
