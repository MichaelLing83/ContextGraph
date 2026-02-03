# Task Completion Checklist

## What to Do After Completing a Task

Since the ContextGraph project currently lacks automated testing, formatting, and CI/CD, follow this manual checklist:

### 1. Manual Testing
Run the demos to ensure functionality still works:
```bash
# Test basic demo
python demo.py

# Test HuggingFace data loading
python demo.py --huggingface
```

Expected outputs:
- Demo should complete without errors
- Output files should be generated in `demo_output/`
- Statistics should be displayed correctly

### 2. Code Review
Manually review your changes:
```bash
# View what changed
git diff

# View staged changes
git diff --cached

# Check file status
git status
```

### 3. Verify Code Quality
Since no automated linters are configured, manually check:
- [ ] Code follows existing style (see code_style_conventions.md)
- [ ] Type hints are used for new functions
- [ ] Docstrings are added for new classes/modules
- [ ] No obvious bugs or errors
- [ ] Variable names are descriptive (snake_case)
- [ ] Class names use PascalCase

### 4. Check Output Files
If your changes affect data processing or graph generation:
```bash
# Verify output files exist and are valid JSON
ls -lah demo_output/
cat demo_output/graph.json | python -m json.tool
cat demo_output/context_graph.json | python -m json.tool
```

### 5. Commit Changes
```bash
# Stage specific files or all changes
git add <specific_files>
# or
git add .

# Commit with descriptive message
git commit -m "Brief description of changes

More detailed explanation if needed.
- Key change 1
- Key change 2"

# Push to remote
git push origin main
```

### 6. Update Documentation (if applicable)
If you added new features or changed functionality:
- [ ] Update README.md
- [ ] Update docstrings
- [ ] Add usage examples if needed

## Common Issues to Check For

### Import Errors
```bash
# Ensure all dependencies are installed
pip list | grep datasets
pip list | grep boto3  # If using S3 features
```

### File Path Issues
- Ensure paths use `Path` objects from `pathlib`
- Check for cross-platform compatibility (macOS/Linux/Windows)

### JSON Serialization
- Ensure datetime objects use `.isoformat()` or `default=str`
- Check for circular references in data structures

### Memory/Performance
- For large datasets, ensure streaming is used where appropriate
- Check that file operations don't load entire files unnecessarily

## Future: When CI/CD is Added
Once the project has automated testing:
```bash
# Run tests
pytest
pytest -v  # Verbose output
pytest --cov  # With coverage

# Run formatters
black .
isort .

# Run linters
flake8 .
mypy .

# Pre-commit hooks will run automatically
```

## Version Control Best Practices
- Keep commits atomic (one logical change per commit)
- Write clear commit messages
- Don't commit generated files (demo_output/*, __pycache__/*, etc.)
- These are already in .gitignore
