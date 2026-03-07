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
- [ ] Add tests for new functionality
- [ ] Tests use class-based organization with descriptive method names

### Validation
- [ ] No simulation experiments (only real agent runs)
- [ ] Changes are backward-compatible or all references updated
- [ ] No sensitive data (API keys, passwords) in committed code
- [ ] `.env` file is gitignored

### Integration Points
- [ ] If modifying `agent_memory/`, sync bundle: `cp agent_memory/*.py tools/query_memory/lib/agent_memory/`
- [ ] If modifying data models, check `to_dict()`/`from_dict()` consistency
- [ ] If modifying Neo4j schema, graph may need rebuilding
- [ ] If adding new node types, add to `reembed_all_nodes.py` NODE_TEXT_FIELDS
- [ ] If changing embedding dimensions, update vector indexes

### Before Running Experiments
- [ ] Delete stale treatment output dirs before re-running
- [ ] Verify bundle is synced to `tools/query_memory/lib/agent_memory/`
- [ ] Verify Neo4j container is running (45,115 nodes expected)
- [ ] Verify docker_args fix is in both `run_swe_agent_single()` and `run_swe_agent_batch()`
- [ ] Keep total concurrent API callers ≤ 9 to avoid ChatAnywhere rate limits
- [ ] Use absolute path for tool bundle in YAML configs

### Commands to Run
```bash
pytest
python -c "import agent_memory"
python -c "from agent_memory.playbook import PlaybookRetriever"
```
