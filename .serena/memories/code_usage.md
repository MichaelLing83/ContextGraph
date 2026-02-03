# Code Usage Guide

## Core Components and How to Use Them

### 1. Data Loading (data_loader.py)

#### Load SWE-bench datasets from HuggingFace
```python
from data_loader import HuggingFaceLoader, load_swebench_lite, load_swebench_verified

# Quick loading
instances = load_swebench_lite()  # 300 instances
instances = load_swebench_verified()  # Verified subset

# Custom loading
loader = HuggingFaceLoader()
instances = loader.load_dataset('swe-bench-lite', split='test')

# Stream trajectories
for traj_data in loader.load_trajectories('nebius/SWE-agent-trajectories'):
    # Process trajectory
    pass
```

#### Load from local files
```python
from data_loader import LocalFileLoader
from pathlib import Path

loader = LocalFileLoader(Path('path/to/trajectories'))

# Single file
traj_data = loader.load_trajectory_file(Path('sample.traj'))

# Iterate all trajectories
for traj_data in loader.iterate_trajectories('*.traj'):
    # Process trajectory
    pass

# Load predictions and results
predictions = loader.load_predictions(Path('predictions.jsonl'))
results = loader.load_results(Path('results.json'))
```

### 2. Trajectory Parsing (analyzer.py)

#### Parse trajectories
```python
from analyzer import TrajectoryParser
from pathlib import Path

parser = TrajectoryParser()

# Parse SWE-agent format
trajectory = parser.parse_swe_agent_trajectory(Path('sample.traj'))

# Parse OpenHands format
trajectory = parser.parse_openhands_trajectory(traj_data_dict)

# Access parsed data
print(f"Instance: {trajectory.instance_id}")
print(f"Steps: {trajectory.total_steps}")
print(f"Actions: {trajectory.action_distribution}")
print(f"Resolved: {trajectory.is_resolved}")

# Iterate steps
for step in trajectory.steps:
    print(f"Step {step.step_id}: {step.action_type}")
    print(f"  Thought: {step.thought}")
    print(f"  Action: {step.action}")
    print(f"  Files: {step.files_accessed}")
    print(f"  Errors: {step.errors_encountered}")
```

### 3. Statistical Analysis (analyzer.py)

#### Analyze multiple trajectories
```python
from analyzer import TrajectoryAnalyzer

analyzer = TrajectoryAnalyzer()

# Add trajectories
for traj in trajectories:
    analyzer.add_trajectory(traj)

# Compute statistics
stats = analyzer.compute_statistics()
print(f"Total: {stats['total_trajectories']}")
print(f"Resolved: {stats['resolved_count']}")
print(f"Resolution rate: {stats['resolution_rate']:.2%}")
print(f"Avg steps (resolved): {stats['avg_steps_resolved']}")
print(f"Avg steps (unresolved): {stats['avg_steps_unresolved']}")

# Compare resolved vs unresolved
comparison = analyzer.compare_resolved_vs_unresolved()
print("Action patterns:", comparison['action_pattern_differences'])

# Extract success patterns
patterns = analyzer.extract_success_patterns()
for pattern in patterns:
    print(f"{pattern['type']}: {pattern}")
```

### 4. Entity and Relation Extraction (analyzer.py)

Entities and relations are automatically extracted during parsing, but you can access them:

```python
# Access extracted entities
for entity in trajectory.entities:
    print(f"[{entity.entity_type}] {entity.name}")
    print(f"  ID: {entity.entity_id}")
    print(f"  File: {entity.file_path}")
    print(f"  Attributes: {entity.attributes}")

# Access extracted relations
for relation in trajectory.relations:
    print(f"{relation.source_id} --[{relation.relation_type}]--> {relation.target_id}")
    print(f"  Context: {relation.context}")
```

### 5. Context Graph Building (context_graph.py)

#### Build graph from trajectory
```python
from context_graph import GraphBuilder, ContextGraph

# Build from trajectory
builder = GraphBuilder()
graph = builder.build_from_trajectory(trajectory)

# Get statistics
stats = graph.stats()
print(f"Episode nodes: {stats['num_episode_nodes']}")
print(f"Semantic nodes: {stats['num_semantic_nodes']}")
print(f"Edges: {stats['num_edges']}")

# Query subgraph (BFS)
subgraph = graph.bfs_subgraph('node_id', max_hops=2)

# Save and load
graph.save(Path('output/context_graph.json'))
loaded_graph = ContextGraph.load(Path('output/context_graph.json'))
```

#### Manual graph construction
```python
from context_graph import ContextGraph, EpisodeNode, SemanticNode, Edge

graph = ContextGraph()

# Add episode node
episode = EpisodeNode(
    node_id='episode_001',
    thought='Looking for the bug',
    action='grep "error" .',
    observation='Found error in file.py'
)
graph.add_episode_node(episode)

# Add semantic node
semantic = SemanticNode(
    node_id='file_001',
    node_type='file',
    name='file.py',
    file_path='src/file.py'
)
graph.add_semantic_node(semantic)

# Add edge
edge = Edge(
    edge_id='edge_001',
    edge_type='ACCESSES',
    source_id='episode_001',
    target_id='file_001',
    fact='Episode accessed file.py',
    context={'step': 1}
)
graph.add_edge(edge)

# Generate automatic links (A-MEM style)
new_links = graph.generate_links(semantic)
```

### 6. Export Results (analyzer.py)

#### Export to different formats
```python
from analyzer import GraphExporter
from pathlib import Path

exporter = GraphExporter()

# Export to JSON (general purpose)
exporter.export_to_json(trajectories, Path('output/graph.json'))

# Export to Cypher (for Graphiti/Neo4j)
exporter.export_for_graphiti(trajectories, Path('output/graph.cypher'))

# Export to A-MEM format (notes structure)
exporter.export_for_amem(trajectories, Path('output/notes.json'))
```

### 7. Entity Types and Relation Types

#### Node Types
- `episode`: Raw interaction records
- `file`: Code files
- `function`: Functions/methods
- `class`: Class definitions
- `variable`: Variables
- `error_pattern`: Error patterns
- `module`: Modules/packages
- `community`: High-level concepts

#### Edge Types
- `CALLS`: Function calls
- `IMPORTS`: Import relations
- `DEFINED_IN`: Definition location
- `MODIFIES`: Modification operations
- `CAUSED_BY`: Error sources
- `RAISED_BY`: Error raising
- `SIMILAR_TO`: Similarity
- `EVOLVED_FROM`: Evolution
- `RELATED_TO`: General association

### 8. Common Workflows

#### Workflow 1: Analyze failed trajectories
```python
# Load and parse
parser = TrajectoryParser()
analyzer = TrajectoryAnalyzer()

for traj_file in Path('trajectories/').glob('*.traj'):
    traj = parser.parse_swe_agent_trajectory(traj_file)
    analyzer.add_trajectory(traj)

# Filter failed cases
failed_trajs = [t for t in analyzer.trajectories if not t.is_resolved]

# Analyze failure patterns
for traj in failed_trajs:
    print(f"Failed: {traj.instance_id}")
    print(f"  Steps: {traj.total_steps}")
    print(f"  Actions: {traj.action_distribution}")
    print(f"  Entities: {len(traj.entities)}")
```

#### Workflow 2: Build and query knowledge graph
```python
# Build graph from multiple trajectories
builder = GraphBuilder()
merged_graph = ContextGraph()

for traj in trajectories:
    graph = builder.build_from_trajectory(traj)
    # Merge into main graph (manual merging needed)
    for node in graph.semantic_nodes.values():
        merged_graph.add_semantic_node(node)

# Query specific patterns
for node in merged_graph.semantic_nodes.values():
    if node.node_type == 'error_pattern':
        # Find what caused this error
        subgraph = merged_graph.bfs_subgraph(node.node_id, max_hops=2)
```

## Data Structures Reference

### SWEBenchInstance
- `instance_id`: Unique identifier
- `repo`: Repository name
- `problem_statement`: Issue description
- `patch`: Gold patch (correct solution)
- `test_patch`: Test case

### Trajectory
- `instance_id`, `repo`, `problem_statement`
- `steps`: List of ActionStep
- `is_resolved`: Whether solved
- `entities`: Extracted CodeEntity list
- `relations`: Extracted CodeRelation list

### ActionStep
- `step_id`, `thought`, `action`, `observation`
- `action_type`: Classified type (search, edit, etc.)
- `files_accessed`: Accessed files
- `errors_encountered`: Errors found
