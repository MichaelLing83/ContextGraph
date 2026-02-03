# Suggested Commands for ContextGraph Project

## Development Commands

### Running the Project
```bash
# Run basic demo with sample trajectory
python demo.py

# Load data from HuggingFace
python demo.py --huggingface

# Run main analyzer module (if applicable)
python analyzer.py --input <trajectory_file> --output <output_dir> --format json
```

### Testing
```bash
# Currently no test suite configured
# TODO: Add pytest configuration
```

### Formatting and Linting
```bash
# Currently no formatters/linters configured
# Potential future commands:
# black .
# flake8 .
# mypy .
```

### Git Operations
```bash
# Standard git workflow
git status
git add <files>
git commit -m "message"
git push origin main

# View recent commits
git log --oneline -10

# Check diff
git diff
```

## Dependency Management

### Install Dependencies
```bash
# Basic dependencies
pip install datasets

# Optional dependencies
pip install boto3  # For S3 data loading
pip install sentence-transformers  # For embeddings (planned feature)
```

### List Installed Packages
```bash
pip list
pip list | grep datasets
```

## Data Management

### Download Data
```bash
# Data is automatically downloaded from HuggingFace when running:
python demo.py --huggingface

# Manual data loading via Python:
python -c "from data_loader import load_swebench_lite; load_swebench_lite()"
```

## System Utilities (macOS/Darwin)

### File Operations
```bash
# List files
ls -lah
ls -R  # Recursive listing

# Find files
find . -name "*.py"
find . -type f -name "*.json"

# Search in files (use ripgrep if available, otherwise grep)
rg "pattern" .
grep -r "pattern" .

# View file contents
cat filename
head -n 20 filename
tail -n 20 filename
less filename
```

### Directory Navigation
```bash
pwd  # Print working directory
cd <directory>
cd ..  # Go up one level
cd ~  # Go to home directory
```

### Process Management
```bash
# Find running Python processes
ps aux | grep python

# Kill a process
kill <PID>
kill -9 <PID>  # Force kill
```

## Output and Results

### View Generated Results
```bash
# List output files
ls -lah demo_output/

# View JSON output
cat demo_output/graph.json | python -m json.tool
cat demo_output/context_graph.json | head -50

# Check file sizes
du -sh demo_output/*
```

## Workflow Commands

### After Completing a Task
Since the project currently has no automated testing or CI/CD:

1. **Manual testing**: Run demos to verify functionality
   ```bash
   python demo.py
   python demo.py --huggingface
   ```

2. **Code review**: Manually review changes
   ```bash
   git diff
   ```

3. **Commit changes**: Follow git workflow
   ```bash
   git add .
   git commit -m "Descriptive message"
   git push
   ```

## Future Improvements
- Add pytest test suite
- Configure black/autopep8 for formatting
- Add flake8/pylint for linting
- Set up pre-commit hooks
- Create requirements.txt or pyproject.toml
- Add CI/CD pipeline
