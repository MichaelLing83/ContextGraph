# Code Style & Conventions

## Naming
- **snake_case** for functions, methods, variables, module names
- **PascalCase** for classes (e.g., `AgentMemory`, `MemoryContext`, `QueryMemoryTool`)
- **UPPER_SNAKE_CASE** for constants (e.g., `SPLIT_PATH`, `NEO4J_URI`, `VALID_TYPES`)
- **Module names**: lowercase, no hyphens (e.g., `neo4j_store.py`, `loop_detector.py`)

## Data Models
- **dataclasses** used extensively for data models (not Pydantic)
- Models implement `to_dict()` and `@classmethod from_dict()` for serialization
- `__post_init__` used for validation
- `Optional[...]` with `= None` for optional fields
- `field(default_factory=...)` for mutable defaults

## Type Hints
- Type hints used on function signatures and class fields
- Imports from `typing`: `Optional`, `List`, `Dict`, `Tuple`, `Any`
- Return types annotated (e.g., `-> Dict[str, Any]`, `-> "Fragment"`)

## Docstrings
- Triple-quote docstrings on classes and public methods
- Short one-line docstrings for simple methods
- Multi-line docstrings with description for complex functions
- Module-level docstrings at top of files

## Logging
- `logging` module used (not print statements for production code)
- `logger = logging.getLogger(__name__)` pattern
- `logging.basicConfig()` in scripts

## Imports
- Standard library first, then third-party, then local
- Scripts use `sys.path.insert(0, str(project_root))` for project imports
- `from pathlib import Path` preferred over `os.path`

## Testing
- pytest with class-based test organization (`class TestFoo:`)
- Fixtures in `conftest.py`
- Test methods named `test_<what_is_being_tested>`
- Short docstrings on test methods

## File Organization
- Core library in `agent_memory/`
- Scripts in `scripts/` (standalone, runnable)
- Experiment framework in `experiments/ab_test/`
- Tests mirror source structure in `tests/`

## Error Handling
- `ValueError` for invalid inputs (e.g., invalid fragment_type)
- `pytest.raises` for testing exceptions
- Graceful fallback with warnings (e.g., mock mode when Neo4j unavailable)

## Configuration
- Nested dataclasses for configuration (`ExperimentConfig` → `PathConfig`, `SplitConfig`, etc.)
- Constants defined at module level
- Environment variables via `os.environ.get()` with defaults
- YAML configs for SWE-agent

## Patterns
- Context manager support (`__enter__`/`__exit__`) on `AgentMemory`
- `frozenset` for immutable valid value sets
- `@classmethod` factory methods (e.g., `from_dict`)
- `field(default_factory=...)` for nested config defaults
