# ContextGraph Project Overview

## Purpose
SWE-bench Trajectory Analyzer - A tool for analyzing SWE-bench agent trajectories to build Context Graphs for coding scenarios. The project analyzes both successful and failed agent execution paths to extract patterns and build knowledge graphs.

## Key Features
- Parse trajectories from multiple agent formats (SWE-agent, OpenHands)
- Extract code entities (files, functions, classes, variables, error patterns)
- Build three-layer Context Graph (Episode → Semantic → Community)
- Statistical analysis comparing successful vs failed cases
- Export to multiple formats (JSON, Cypher for Graphiti, A-MEM notes)

## Tech Stack
- **Language**: Python 3.x
- **Core Libraries**:
  - `datasets` - Loading data from HuggingFace
  - `json`, `pathlib` - Data handling
  - `dataclasses` - Data structures
  - `collections`, `datetime`, `hashlib` - Utilities
  - `re` - Pattern matching
  
- **Optional Libraries**:
  - `boto3` - S3 data loading
  - `sentence-transformers` - Embeddings (planned)

## Design Inspirations
The project integrates concepts from:
1. **Zep** - Three-layer graph architecture, bi-temporal modeling
2. **A-MEM** - Zettelkasten-based dynamic linking
3. **Context Graph** - Rich edge context information
4. **ExpeL** - Extracting patterns from success/failure trajectories

## Project Structure
```
ContextGraph/
├── analyzer.py         # Main analysis: trajectory parsing, entity extraction, statistics
├── data_loader.py      # Data loading from HuggingFace, local files, S3
├── context_graph.py    # Context Graph implementation
├── demo.py             # Demo scripts
├── README.md           # Documentation
├── demo_output/        # Generated analysis results
└── .git/               # Git repository
```

## Main Entry Points
- `python demo.py` - Run basic demo with sample trajectory
- `python demo.py --huggingface` - Load and display SWE-bench Lite data
- Direct usage via importing modules (see code_usage.md)
