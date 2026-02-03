# Code Style and Conventions

## General Style
- **Docstrings**: Triple-quoted strings at module and class level (mixed Chinese and English)
- **Comments**: Both English and Chinese comments are used
- **Line Length**: Generally follows PEP 8 guidelines
- **File Encoding**: UTF-8

## Type Hints
- **Extensively used** throughout the codebase
- Function parameters and return types are annotated
- Examples:
  ```python
  def parse_swe_agent_trajectory(self, traj_path: Path) -> Trajectory:
  def extract_files_from_text(self, text: str) -> List[str]:
  ```

## Data Structures
- **Dataclasses** are heavily used for structured data:
  - `@dataclass` decorator with type hints
  - `field(default_factory=...)` for mutable defaults
  - Examples: `CodeEntity`, `CodeRelation`, `ActionStep`, `Trajectory`, `SWEBenchInstance`

## Naming Conventions
- **Classes**: PascalCase (e.g., `TrajectoryParser`, `CodeEntity`, `GraphExporter`)
- **Functions/Methods**: snake_case (e.g., `parse_swe_agent_trajectory`, `extract_entities_and_relations`)
- **Constants**: UPPER_SNAKE_CASE (e.g., `ACTION_CATEGORIES`, `DATASETS`)
- **Private Methods**: Leading underscore (e.g., `_parse_step`, `_extract_files_from_text`)

## Module Organization
- Large separator comments using `# ============================================================================`
- Sections clearly marked (e.g., "数据结构定义", "轨迹解析器", "实体和关系提取器")
- Imports organized at the top

## Documentation Headers
Each module starts with a triple-quoted docstring containing:
- Module name (English and Chinese)
- Purpose description
- Main features (numbered list)

Example from analyzer.py:
```python
\"\"\"
SWE-bench Trajectory Analyzer
=============================
分析 SWE-bench agent 轨迹，提取用于 Context Graph 构建的模式和实体

主要功能:
1. 加载和解析轨迹数据 (SWE-agent, OpenHands 等格式)
...
\"\"\"
```

## Error Handling
- Try-except blocks used for external dependencies (e.g., imports)
- Logger warnings for non-critical failures
- Silent failures with warnings for file loading iterations

## Default Values
- Optional parameters use `Optional[Type] = None`
- Dictionary/list defaults use `field(default_factory=dict/list)` in dataclasses
- Class constants defined at class level (e.g., `ACTION_CATEGORIES`, `DATASETS`)

## No Explicit Formatters/Linters
The project currently does not have:
- Black, autopep8, or other code formatters
- Flake8, pylint, or other linters
- Pre-commit hooks
- pytest configuration

Style appears to be manually maintained following PEP 8 principles.
