# Code Usage Examples

## Core Library Usage

### AgentMemory (main entry point)
```python
from agent_memory import AgentMemory

# As context manager
with AgentMemory(
    neo4j_uri="bolt://localhost:7687",
    neo4j_auth=("neo4j", "contextgraph123"),
    embedding_api_key="sk-...",
) as memory:
    # Query for relevant past experience
    context = memory.query(problem_description="fix import error in django views")
    
    # Learn from a new trajectory
    memory.learn(trajectory)
    
    # Check for loops
    memory.check_loop(action_sequence)
    
    # Get stats
    stats = memory.get_stats()
```

### Data Models
```python
from agent_memory.models import Fragment, Trajectory, State, Methodology, ErrorPattern

# Fragment types: error_recovery, exploration, successful_fix, failed_attempt, loop
fragment = Fragment(
    id="frag-001",
    step_range=(1, 5),
    fragment_type="successful_fix",
    description="Fixed import error by adding missing dependency",
    action_sequence=["search", "edit", "test"],
    outcome="Tests pass",
)
data = fragment.to_dict()
restored = Fragment.from_dict(data)
```

### Evaluation Metrics
```python
from agent_memory.evaluation.metrics import ProblemResult, calculate_metrics

result = ProblemResult(
    problem_id="instance-123",
    attempts=[True, False],
    tokens=[1234, 5678],
)
assert result.pass_at_1 is True
assert result.total_tokens == 6912

metrics = calculate_metrics([result1, result2, ...])
```

### SWE-agent Tool
```python
from agent_memory.evaluation.swe_agent_tool import QueryMemoryTool

tool = QueryMemoryTool(
    neo4j_uri="bolt://localhost:7687",
    neo4j_auth=("neo4j", "contextgraph123"),
)
schema = tool.get_schema()  # For function calling
output = tool.invoke(QueryMemoryInput(problem_description="..."))
```

### OpenHands Integration
```python
from experiments.ab_test.openhands_integration import (
    MemoryHooks, ExperimentGroup, create_memory_hooks, assign_experiment_group,
)

group = assign_experiment_group(problem_id="instance-123")
hooks = create_memory_hooks(neo4j_uri="bolt://localhost:7687")
context = hooks.pre_action_hook(state)
```

## Script Usage
```bash
# Build context graph
python scripts/build_context_graph.py

# Run real SWE-agent experiment
python scripts/run_real_swe_experiment.py

# Run real OpenHands experiment
python scripts/run_real_openhands_experiment.py --n 200
```
