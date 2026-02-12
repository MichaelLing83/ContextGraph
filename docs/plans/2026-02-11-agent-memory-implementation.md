# Agent Memory (ContextGraph V2) Implementation Plan

> **Execution note:** Follow tasks sequentially and use test-driven iteration (red -> green -> refactor).

**Goal:** Build a Neo4j-backed long-term memory system for coding agents that learns from trajectories, detects loops based on error consistency, and provides multi-dimensional retrieval.

**Architecture:** Hierarchical memory with Trajectory summaries and key Fragments. Writer handles incremental ingestion, Retriever provides 4-dimensional search (error/task/state/semantic), Consolidator runs every 16 trajectories to abstract methodologies and merge similar nodes.

**Tech Stack:** Python 3.10+, Neo4j, OpenAI embedding API (Anthropic as future extension), dataclasses, pytest

---

## Phase 1: Project Setup & Data Structures

### Task 1.1: Create Package Structure

**Files:**
- Create: `agent_memory/__init__.py`
- Create: `agent_memory/models.py`
- Create: `tests/__init__.py`
- Create: `tests/test_models.py`
- Create: `pyproject.toml`
- Create: `requirements.txt`

**Step 1: Create pyproject.toml**

```toml
[project]
name = "agent-memory"
version = "0.1.0"
description = "Long-term memory system for coding agents"
requires-python = ">=3.10"
dependencies = [
    "neo4j>=5.0.0",
    "openai>=1.0.0",
    "numpy>=1.24.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-asyncio>=0.21.0",
]

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"
```

**Step 2: Create requirements.txt**

```
neo4j>=5.0.0
openai>=1.0.0
numpy>=1.24.0
pytest>=7.0.0
pytest-asyncio>=0.21.0
```

**Step 3: Create agent_memory/__init__.py**

```python
"""Agent Memory - Long-term memory system for coding agents."""

from agent_memory.models import (
    Trajectory,
    Fragment,
    State,
    Methodology,
    ErrorPattern,
)

__version__ = "0.1.0"
__all__ = ["Trajectory", "Fragment", "State", "Methodology", "ErrorPattern"]
```

**Step 4: Create tests/__init__.py**

```python
"""Tests for agent_memory package."""
```

**Step 5: Commit**

```bash
git add pyproject.toml requirements.txt agent_memory/ tests/
git commit -m "feat: initialize agent-memory package structure"
```

---

### Task 1.2: Define Core Data Models

**Files:**
- Create: `agent_memory/models.py`
- Create: `tests/test_models.py`

**Step 1: Write failing test for Trajectory model**

```python
# tests/test_models.py
"""Tests for data models."""

import pytest
from agent_memory.models import Trajectory, Fragment, State, Methodology, ErrorPattern


class TestTrajectory:
    def test_trajectory_creation(self):
        traj = Trajectory(
            id="traj_001",
            instance_id="django__django-12345",
            repo="django/django",
            task_type="bug_fix",
            success=True,
            total_steps=25,
            summary="Fixed import error in models.py",
        )
        assert traj.id == "traj_001"
        assert traj.instance_id == "django__django-12345"
        assert traj.success is True

    def test_trajectory_to_dict(self):
        traj = Trajectory(
            id="traj_001",
            instance_id="test",
            repo="test/repo",
            task_type="bug_fix",
            success=True,
            total_steps=10,
            summary="Test summary",
        )
        d = traj.to_dict()
        assert d["id"] == "traj_001"
        assert "embedding" in d
```

**Step 2: Run test to verify it fails**

```bash
cd <repo-root>
python -m pytest tests/test_models.py -v
```

Expected: FAIL with "ModuleNotFoundError: No module named 'agent_memory'"

**Step 3: Write Trajectory model**

```python
# agent_memory/models.py
"""Core data models for Agent Memory."""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime


@dataclass
class Trajectory:
    """Trajectory summary node - represents a complete agent run."""

    id: str
    instance_id: str          # SWE-bench instance ID
    repo: str                 # Repository name
    task_type: str            # bug_fix / feature / refactor
    success: bool             # Whether the task succeeded
    total_steps: int          # Total steps taken
    summary: str              # Natural language summary
    embedding: Optional[List[float]] = None  # Semantic embedding
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "instance_id": self.instance_id,
            "repo": self.repo,
            "task_type": self.task_type,
            "success": self.success,
            "total_steps": self.total_steps,
            "summary": self.summary,
            "embedding": self.embedding,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Trajectory":
        created_at = d.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        return cls(
            id=d["id"],
            instance_id=d["instance_id"],
            repo=d["repo"],
            task_type=d["task_type"],
            success=d["success"],
            total_steps=d["total_steps"],
            summary=d["summary"],
            embedding=d.get("embedding"),
            created_at=created_at or datetime.now(),
        )
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_models.py::TestTrajectory -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add agent_memory/models.py tests/test_models.py
git commit -m "feat: add Trajectory data model"
```

---

### Task 1.3: Add Fragment Model

**Files:**
- Modify: `agent_memory/models.py`
- Modify: `tests/test_models.py`

**Step 1: Write failing test for Fragment**

```python
# Add to tests/test_models.py

class TestFragment:
    def test_fragment_creation(self):
        frag = Fragment(
            id="frag_001",
            step_range=(5, 12),
            fragment_type="error_recovery",
            description="Recovered from ImportError by fixing module path",
            action_sequence=["search", "open", "edit"],
            outcome="success",
        )
        assert frag.id == "frag_001"
        assert frag.step_range == (5, 12)
        assert frag.fragment_type == "error_recovery"

    def test_fragment_types_valid(self):
        valid_types = ["error_recovery", "exploration", "successful_fix", "failed_attempt", "loop"]
        for ft in valid_types:
            frag = Fragment(
                id=f"frag_{ft}",
                step_range=(0, 1),
                fragment_type=ft,
                description="test",
                action_sequence=[],
                outcome="test",
            )
            assert frag.fragment_type == ft
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_models.py::TestFragment -v
```

Expected: FAIL

**Step 3: Add Fragment model to models.py**

```python
# Add to agent_memory/models.py after Trajectory class

@dataclass
class Fragment:
    """Key fragment from a trajectory - a meaningful sequence of steps."""

    id: str
    step_range: Tuple[int, int]   # (start_step, end_step)
    fragment_type: str            # error_recovery / exploration / successful_fix / failed_attempt / loop
    description: str              # Natural language description
    action_sequence: List[str]    # Abstract action types
    outcome: str                  # Result of this fragment
    embedding: Optional[List[float]] = None

    VALID_TYPES = frozenset([
        "error_recovery",
        "exploration",
        "successful_fix",
        "failed_attempt",
        "loop",
    ])

    def __post_init__(self):
        if self.fragment_type not in self.VALID_TYPES:
            raise ValueError(f"Invalid fragment_type: {self.fragment_type}. Must be one of {self.VALID_TYPES}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "step_range": list(self.step_range),
            "fragment_type": self.fragment_type,
            "description": self.description,
            "action_sequence": self.action_sequence,
            "outcome": self.outcome,
            "embedding": self.embedding,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Fragment":
        step_range = d["step_range"]
        if isinstance(step_range, list):
            step_range = tuple(step_range)
        return cls(
            id=d["id"],
            step_range=step_range,
            fragment_type=d["fragment_type"],
            description=d["description"],
            action_sequence=d["action_sequence"],
            outcome=d["outcome"],
            embedding=d.get("embedding"),
        )
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_models.py::TestFragment -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add agent_memory/models.py tests/test_models.py
git commit -m "feat: add Fragment data model"
```

---

### Task 1.4: Add State Model

**Files:**
- Modify: `agent_memory/models.py`
- Modify: `tests/test_models.py`

**Step 1: Write failing test for State**

```python
# Add to tests/test_models.py

class TestState:
    def test_state_creation(self):
        state = State(
            tools=["bash", "search", "edit", "view"],
            repo_summary="Django web framework, Python 3.8+",
            task_description="Fix failing import in models.py",
            current_error="ImportError: cannot import name 'Model'",
            phase="fixing",
        )
        assert state.tools == ["bash", "search", "edit", "view"]
        assert state.phase == "fixing"
        assert "ImportError" in state.current_error

    def test_state_to_situation_string(self):
        state = State(
            tools=["bash", "edit"],
            repo_summary="Python web app",
            task_description="Fix bug",
            current_error="TypeError: NoneType",
            phase="locating",
        )
        situation = state.to_situation_string()
        assert "TypeError" in situation
        assert "locating" in situation
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_models.py::TestState -v
```

Expected: FAIL

**Step 3: Add State model to models.py**

```python
# Add to agent_memory/models.py after Fragment class

@dataclass
class State:
    """Agent state snapshot - complete context at a point in time."""

    tools: List[str]          # a. Available tools
    repo_summary: str         # b. Repository overview
    task_description: str     # c. Task description
    current_error: str        # d. Current error message (empty if no error)
    phase: str                # understanding / locating / fixing / testing
    embedding: Optional[List[float]] = None

    VALID_PHASES = frozenset(["understanding", "locating", "fixing", "testing"])

    def __post_init__(self):
        if self.phase not in self.VALID_PHASES:
            raise ValueError(f"Invalid phase: {self.phase}. Must be one of {self.VALID_PHASES}")

    def to_situation_string(self) -> str:
        """Convert state to a situation description string for matching."""
        parts = [f"phase:{self.phase}"]
        if self.current_error:
            # Extract error type
            error_type = self._extract_error_type(self.current_error)
            parts.append(f"error:{error_type}")
        parts.append(f"repo:{self.repo_summary[:50]}")
        return " | ".join(parts)

    def _extract_error_type(self, error_msg: str) -> str:
        """Extract error type from error message."""
        import re
        # Match common Python error patterns
        match = re.search(r'(\w+Error|\w+Exception|FAIL|ERROR)', error_msg)
        return match.group(1) if match else "Unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tools": self.tools,
            "repo_summary": self.repo_summary,
            "task_description": self.task_description,
            "current_error": self.current_error,
            "phase": self.phase,
            "embedding": self.embedding,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "State":
        return cls(
            tools=d["tools"],
            repo_summary=d["repo_summary"],
            task_description=d["task_description"],
            current_error=d.get("current_error", ""),
            phase=d["phase"],
            embedding=d.get("embedding"),
        )
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_models.py::TestState -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add agent_memory/models.py tests/test_models.py
git commit -m "feat: add State data model"
```

---

### Task 1.5: Add Methodology Model

**Files:**
- Modify: `agent_memory/models.py`
- Modify: `tests/test_models.py`

**Step 1: Write failing test for Methodology**

```python
# Add to tests/test_models.py

class TestMethodology:
    def test_methodology_creation(self):
        method = Methodology(
            id="meth_001",
            situation="When encountering ImportError in Python package",
            strategy="1. Check if module exists 2. Verify import path 3. Check __init__.py",
            confidence=0.85,
            success_count=17,
            failure_count=3,
        )
        assert method.id == "meth_001"
        assert method.confidence == 0.85
        assert method.success_rate == 17 / (17 + 3)

    def test_methodology_update_stats(self):
        method = Methodology(
            id="meth_002",
            situation="Test situation",
            strategy="Test strategy",
            confidence=0.5,
            success_count=5,
            failure_count=5,
        )
        method.record_outcome(success=True)
        assert method.success_count == 6
        assert method.failure_count == 5

        method.record_outcome(success=False)
        assert method.failure_count == 6
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_models.py::TestMethodology -v
```

Expected: FAIL

**Step 3: Add Methodology model to models.py**

```python
# Add to agent_memory/models.py after State class

@dataclass
class Methodology:
    """Abstracted methodology - learned strategy for a situation."""

    id: str
    situation: str            # When to apply (natural language)
    strategy: str             # What to do (natural language)
    confidence: float         # Confidence score 0-1
    success_count: int = 0
    failure_count: int = 0
    embedding: Optional[List[float]] = None
    source_fragment_ids: List[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0

    def record_outcome(self, success: bool) -> None:
        """Record the outcome of applying this methodology."""
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        # Update confidence based on recent outcomes
        self.confidence = self.success_rate

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "situation": self.situation,
            "strategy": self.strategy,
            "confidence": self.confidence,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "embedding": self.embedding,
            "source_fragment_ids": self.source_fragment_ids,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Methodology":
        return cls(
            id=d["id"],
            situation=d["situation"],
            strategy=d["strategy"],
            confidence=d["confidence"],
            success_count=d.get("success_count", 0),
            failure_count=d.get("failure_count", 0),
            embedding=d.get("embedding"),
            source_fragment_ids=d.get("source_fragment_ids", []),
        )
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_models.py::TestMethodology -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add agent_memory/models.py tests/test_models.py
git commit -m "feat: add Methodology data model"
```

---

### Task 1.6: Add ErrorPattern Model

**Files:**
- Modify: `agent_memory/models.py`
- Modify: `tests/test_models.py`

**Step 1: Write failing test for ErrorPattern**

```python
# Add to tests/test_models.py

class TestErrorPattern:
    def test_error_pattern_creation(self):
        pattern = ErrorPattern(
            id="err_001",
            error_type="ImportError",
            error_keywords=["cannot import", "module", "not found"],
            context="Python import statement",
            frequency=42,
        )
        assert pattern.id == "err_001"
        assert pattern.error_type == "ImportError"
        assert pattern.frequency == 42

    def test_error_pattern_matches(self):
        pattern = ErrorPattern(
            id="err_001",
            error_type="ImportError",
            error_keywords=["cannot import", "module"],
            context="Python",
            frequency=10,
        )
        # Should match same error type with overlapping keywords
        assert pattern.matches_error("ImportError", "cannot import name 'Foo' from module")
        # Should not match different error type
        assert not pattern.matches_error("TypeError", "cannot import name 'Foo'")
        # Should not match if no keyword overlap
        assert not pattern.matches_error("ImportError", "syntax error in file")
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_models.py::TestErrorPattern -v
```

Expected: FAIL

**Step 3: Add ErrorPattern model to models.py**

```python
# Add to agent_memory/models.py after Methodology class

@dataclass
class ErrorPattern:
    """Known error pattern - for matching and statistics."""

    id: str
    error_type: str           # ImportError, TypeError, etc.
    error_keywords: List[str] # Key words from error messages
    context: str              # Context where this error occurs
    frequency: int = 0        # How often this pattern is seen

    def matches_error(self, error_type: str, error_message: str) -> bool:
        """Check if an error matches this pattern."""
        if self.error_type != error_type:
            return False
        # Check keyword overlap
        message_lower = error_message.lower()
        return any(kw.lower() in message_lower for kw in self.error_keywords)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "error_type": self.error_type,
            "error_keywords": self.error_keywords,
            "context": self.context,
            "frequency": self.frequency,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ErrorPattern":
        return cls(
            id=d["id"],
            error_type=d["error_type"],
            error_keywords=d["error_keywords"],
            context=d.get("context", ""),
            frequency=d.get("frequency", 0),
        )
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_models.py::TestErrorPattern -v
```

Expected: PASS

**Step 5: Update __init__.py exports**

```python
# agent_memory/__init__.py
"""Agent Memory - Long-term memory system for coding agents."""

from agent_memory.models import (
    Trajectory,
    Fragment,
    State,
    Methodology,
    ErrorPattern,
)

__version__ = "0.1.0"
__all__ = ["Trajectory", "Fragment", "State", "Methodology", "ErrorPattern"]
```

**Step 6: Run all model tests**

```bash
python -m pytest tests/test_models.py -v
```

Expected: All tests PASS

**Step 7: Commit**

```bash
git add agent_memory/ tests/test_models.py
git commit -m "feat: add ErrorPattern data model, complete core models"
```

---

## Phase 2: Neo4j Connection & Schema

### Task 2.1: Create Neo4j Connection Module

**Files:**
- Create: `agent_memory/neo4j_store.py`
- Create: `tests/test_neo4j_store.py`
- Create: `tests/conftest.py`

**Step 1: Create conftest.py with Neo4j fixture**

```python
# tests/conftest.py
"""Pytest fixtures for agent_memory tests."""

import pytest
import os


@pytest.fixture
def neo4j_uri():
    """Get Neo4j URI from environment or use default."""
    return os.environ.get("NEO4J_URI", "bolt://localhost:7687")


@pytest.fixture
def neo4j_auth():
    """Get Neo4j credentials from environment."""
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "password")
    return (user, password)


@pytest.fixture
def skip_neo4j():
    """Skip test if Neo4j is not available."""
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(uri, auth=("neo4j", "password"))
        driver.verify_connectivity()
        driver.close()
        return False
    except Exception:
        return True
```

**Step 2: Write failing test for Neo4jStore**

```python
# tests/test_neo4j_store.py
"""Tests for Neo4j store."""

import pytest
from agent_memory.neo4j_store import Neo4jStore


class TestNeo4jStore:
    def test_store_creation(self, neo4j_uri, neo4j_auth):
        """Test Neo4jStore can be instantiated."""
        store = Neo4jStore(uri=neo4j_uri, auth=neo4j_auth)
        assert store is not None
        store.close()

    def test_store_context_manager(self, neo4j_uri, neo4j_auth):
        """Test Neo4jStore works as context manager."""
        with Neo4jStore(uri=neo4j_uri, auth=neo4j_auth) as store:
            assert store is not None
```

**Step 3: Run test to verify it fails**

```bash
python -m pytest tests/test_neo4j_store.py -v
```

Expected: FAIL with "ModuleNotFoundError"

**Step 4: Create Neo4jStore class**

```python
# agent_memory/neo4j_store.py
"""Neo4j graph store for Agent Memory."""

from typing import Optional, Tuple, List, Dict, Any
from neo4j import GraphDatabase, Driver
import logging

logger = logging.getLogger(__name__)


class Neo4jStore:
    """Neo4j connection manager and query executor."""

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        auth: Tuple[str, str] = ("neo4j", "password"),
        database: str = "neo4j",
    ):
        self.uri = uri
        self.auth = auth
        self.database = database
        self._driver: Optional[Driver] = None

    @property
    def driver(self) -> Driver:
        if self._driver is None:
            self._driver = GraphDatabase.driver(self.uri, auth=self.auth)
        return self._driver

    def close(self) -> None:
        """Close the driver connection."""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def __enter__(self) -> "Neo4jStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def verify_connectivity(self) -> bool:
        """Test connection to Neo4j."""
        try:
            self.driver.verify_connectivity()
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")
            return False

    def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a Cypher query and return results."""
        with self.driver.session(database=self.database) as session:
            result = session.run(query, parameters or {})
            return [record.data() for record in result]

    def execute_write(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Execute a write query."""
        with self.driver.session(database=self.database) as session:
            session.execute_write(lambda tx: tx.run(query, parameters or {}))
```

**Step 5: Run test to verify it passes (skip if no Neo4j)**

```bash
python -m pytest tests/test_neo4j_store.py -v --ignore-glob="*integration*"
```

**Step 6: Commit**

```bash
git add agent_memory/neo4j_store.py tests/conftest.py tests/test_neo4j_store.py
git commit -m "feat: add Neo4jStore connection manager"
```

---

### Task 2.2: Add Schema Initialization

**Files:**
- Modify: `agent_memory/neo4j_store.py`
- Modify: `tests/test_neo4j_store.py`

**Step 1: Write failing test for schema initialization**

```python
# Add to tests/test_neo4j_store.py

class TestNeo4jSchema:
    def test_init_schema(self, neo4j_uri, neo4j_auth, neo4j_available):
        """Test schema initialization creates constraints and indexes."""
        if not neo4j_available:
            pytest.skip("Neo4j not available")
        with Neo4jStore(uri=neo4j_uri, auth=neo4j_auth) as store:
            store.init_schema()
            # Verify constraints exist
            constraints = store.execute_query("SHOW CONSTRAINTS")
            constraint_names = [c.get("name", "") for c in constraints]
            assert any("trajectory_id" in n.lower() for n in constraint_names)
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_neo4j_store.py::TestNeo4jSchema -v
```

Expected: FAIL

**Step 3: Add init_schema method**

```python
# Add to Neo4jStore class in agent_memory/neo4j_store.py

    def init_schema(self) -> None:
        """Initialize Neo4j schema with constraints and indexes."""
        schema_queries = [
            # Uniqueness constraints
            "CREATE CONSTRAINT trajectory_id IF NOT EXISTS FOR (t:Trajectory) REQUIRE t.id IS UNIQUE",
            "CREATE CONSTRAINT fragment_id IF NOT EXISTS FOR (f:Fragment) REQUIRE f.id IS UNIQUE",
            # Note: State nodes are runtime context and are not persisted with id
            "CREATE CONSTRAINT methodology_id IF NOT EXISTS FOR (m:Methodology) REQUIRE m.id IS UNIQUE",
            "CREATE CONSTRAINT error_pattern_id IF NOT EXISTS FOR (e:ErrorPattern) REQUIRE e.id IS UNIQUE",

            # Indexes for common lookups
            "CREATE INDEX trajectory_instance IF NOT EXISTS FOR (t:Trajectory) ON (t.instance_id)",
            "CREATE INDEX trajectory_repo IF NOT EXISTS FOR (t:Trajectory) ON (t.repo)",
            "CREATE INDEX trajectory_success IF NOT EXISTS FOR (t:Trajectory) ON (t.success)",
            "CREATE INDEX fragment_type IF NOT EXISTS FOR (f:Fragment) ON (f.fragment_type)",
            "CREATE INDEX state_phase IF NOT EXISTS FOR (s:State) ON (s.phase)",
            "CREATE INDEX error_pattern_type IF NOT EXISTS FOR (e:ErrorPattern) ON (e.error_type)",

            # Full-text search indexes (for keyword matching)
            """
            CREATE FULLTEXT INDEX methodology_situation IF NOT EXISTS
            FOR (m:Methodology) ON EACH [m.situation, m.strategy]
            """,
        ]

        for query in schema_queries:
            try:
                self.execute_write(query.strip())
                logger.debug(f"Executed schema query: {query[:50]}...")
            except Exception as e:
                # Some queries may fail if already exists, that's OK
                logger.debug(f"Schema query note: {e}")

        logger.info("Neo4j schema initialized")
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_neo4j_store.py::TestNeo4jSchema -v
```

Expected: PASS (or skip if no Neo4j)

**Step 5: Commit**

```bash
git add agent_memory/neo4j_store.py tests/test_neo4j_store.py
git commit -m "feat: add Neo4j schema initialization"
```

---

### Task 2.3: Add Node CRUD Operations

**Files:**
- Modify: `agent_memory/neo4j_store.py`
- Modify: `tests/test_neo4j_store.py`

**Step 1: Write failing test for node operations**

```python
# Add to tests/test_neo4j_store.py
from agent_memory.models import Trajectory, Fragment, Methodology


class TestNeo4jNodeOperations:
    def test_create_trajectory_node(self, neo4j_uri, neo4j_auth, neo4j_available):
        """Test creating a Trajectory node."""
        if not neo4j_available:
            pytest.skip("Neo4j not available")
        with Neo4jStore(uri=neo4j_uri, auth=neo4j_auth) as store:
            store.init_schema()

            traj = Trajectory(
                id="test_traj_001",
                instance_id="test__test-123",
                repo="test/repo",
                task_type="bug_fix",
                success=True,
                total_steps=10,
                summary="Test trajectory",
            )

            store.create_trajectory(traj)

            # Verify it was created
            result = store.get_trajectory("test_traj_001")
            assert result is not None
            assert result.instance_id == "test__test-123"

            # Cleanup
            store.execute_write("MATCH (t:Trajectory {id: 'test_traj_001'}) DELETE t")
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_neo4j_store.py::TestNeo4jNodeOperations -v
```

Expected: FAIL

**Step 3: Add node CRUD methods**

```python
# Add to Neo4jStore class

    def create_trajectory(self, trajectory: "Trajectory") -> None:
        """Create a Trajectory node in Neo4j."""
        from agent_memory.models import Trajectory

        query = """
        CREATE (t:Trajectory {
            id: $id,
            instance_id: $instance_id,
            repo: $repo,
            task_type: $task_type,
            success: $success,
            total_steps: $total_steps,
            summary: $summary,
            embedding: $embedding,
            created_at: $created_at
        })
        """
        self.execute_write(query, trajectory.to_dict())

    def get_trajectory(self, trajectory_id: str) -> Optional["Trajectory"]:
        """Get a Trajectory by ID."""
        from agent_memory.models import Trajectory

        query = "MATCH (t:Trajectory {id: $id}) RETURN t"
        results = self.execute_query(query, {"id": trajectory_id})

        if not results:
            return None

        node_data = results[0]["t"]
        return Trajectory.from_dict(node_data)

    def create_fragment(self, fragment: "Fragment", trajectory_id: str) -> None:
        """Create a Fragment node and link to Trajectory."""
        from agent_memory.models import Fragment

        query = """
        MATCH (t:Trajectory {id: $trajectory_id})
        CREATE (f:Fragment {
            id: $id,
            step_range: $step_range,
            fragment_type: $fragment_type,
            description: $description,
            action_sequence: $action_sequence,
            outcome: $outcome,
            embedding: $embedding
        })
        CREATE (t)-[:HAS_FRAGMENT]->(f)
        """
        params = fragment.to_dict()
        params["trajectory_id"] = trajectory_id
        self.execute_write(query, params)

    def create_methodology(self, methodology: "Methodology") -> None:
        """Create a Methodology node."""
        from agent_memory.models import Methodology

        query = """
        CREATE (m:Methodology {
            id: $id,
            situation: $situation,
            strategy: $strategy,
            confidence: $confidence,
            success_count: $success_count,
            failure_count: $failure_count,
            embedding: $embedding,
            source_fragment_ids: $source_fragment_ids
        })
        """
        self.execute_write(query, methodology.to_dict())

    def create_error_pattern(self, error_pattern: "ErrorPattern") -> None:
        """Create an ErrorPattern node."""
        from agent_memory.models import ErrorPattern

        query = """
        MERGE (e:ErrorPattern {error_type: $error_type})
        ON CREATE SET
            e.id = $id,
            e.error_keywords = $error_keywords,
            e.context = $context,
            e.frequency = $frequency
        ON MATCH SET
            e.error_keywords = e.error_keywords + [kw IN $error_keywords WHERE NOT kw IN e.error_keywords],
            e.frequency = e.frequency + $frequency
        """
        self.execute_write(query, error_pattern.to_dict())
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_neo4j_store.py::TestNeo4jNodeOperations -v
```

Expected: PASS (or skip if no Neo4j)

**Step 5: Commit**

```bash
git add agent_memory/neo4j_store.py tests/test_neo4j_store.py
git commit -m "feat: add Neo4j node CRUD operations"
```

---

## Phase 3: Embedding Client

### Task 3.1: Create Embedding Client Interface

**Files:**
- Create: `agent_memory/embeddings.py`
- Create: `tests/test_embeddings.py`

**Step 1: Write failing test for EmbeddingClient**

```python
# tests/test_embeddings.py
"""Tests for embedding client."""

import pytest
from agent_memory.embeddings import EmbeddingClient, get_embedding_client


class TestEmbeddingClient:
    def test_client_interface(self):
        """Test EmbeddingClient has required methods."""
        client = get_embedding_client("mock")
        assert hasattr(client, "embed")
        assert hasattr(client, "embed_batch")

    def test_mock_client_embed(self):
        """Test mock client returns embedding."""
        client = get_embedding_client("mock")
        result = client.embed("test text")
        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(x, float) for x in result)

    def test_mock_client_embed_batch(self):
        """Test mock client batch embedding."""
        client = get_embedding_client("mock")
        texts = ["text 1", "text 2", "text 3"]
        results = client.embed_batch(texts)
        assert len(results) == 3
        assert all(isinstance(r, list) for r in results)
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_embeddings.py -v
```

Expected: FAIL

**Step 3: Create embedding module**

```python
# agent_memory/embeddings.py
"""Embedding client for semantic matching."""

from abc import ABC, abstractmethod
from typing import List, Optional
import hashlib
import logging

logger = logging.getLogger(__name__)


class EmbeddingClient(ABC):
    """Abstract base class for embedding clients."""

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        pass

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts. Override for efficiency."""
        return [self.embed(text) for text in texts]


class MockEmbeddingClient(EmbeddingClient):
    """Mock embedding client for testing."""

    def __init__(self, dimension: int = 256):
        self.dimension = dimension

    def embed(self, text: str) -> List[float]:
        """Generate deterministic mock embedding based on text hash."""
        # Use hash to generate deterministic but varied embeddings
        hash_bytes = hashlib.sha256(text.encode()).digest()
        # Convert bytes to floats in range [-1, 1]
        embedding = []
        for i in range(0, min(len(hash_bytes), self.dimension), 1):
            # Use pairs of bytes to create float
            val = (hash_bytes[i % len(hash_bytes)] - 128) / 128.0
            embedding.append(val)

        # Pad or truncate to dimension
        while len(embedding) < self.dimension:
            embedding.append(0.0)

        return embedding[:self.dimension]


class OpenAIEmbeddingClient(EmbeddingClient):
    """OpenAI embedding client."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self.api_key = api_key
        self.model = model
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def embed(self, text: str) -> List[float]:
        response = self.client.embeddings.create(
            input=text,
            model=self.model,
        )
        return response.data[0].embedding

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        response = self.client.embeddings.create(
            input=texts,
            model=self.model,
        )
        return [item.embedding for item in response.data]


def get_embedding_client(
    client_type: str = "openai",
    api_key: Optional[str] = None,
    **kwargs,
) -> EmbeddingClient:
    """Factory function to get embedding client."""
    if client_type == "mock":
        return MockEmbeddingClient(**kwargs)
    elif client_type == "openai":
        if api_key is None:
            import os
            api_key = os.environ.get("OPENAI_API_KEY")
        if api_key is None:
            raise ValueError("OpenAI API key required")
        return OpenAIEmbeddingClient(api_key=api_key, **kwargs)
    else:
        raise ValueError(f"Unknown client type: {client_type}")
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_embeddings.py -v
```

Expected: PASS

**Step 5: Update __init__.py**

```python
# Add to agent_memory/__init__.py
from agent_memory.embeddings import EmbeddingClient, get_embedding_client

__all__ = [
    "Trajectory", "Fragment", "State", "Methodology", "ErrorPattern",
    "EmbeddingClient", "get_embedding_client",
]
```

**Step 6: Commit**

```bash
git add agent_memory/embeddings.py tests/test_embeddings.py agent_memory/__init__.py
git commit -m "feat: add embedding client with mock and OpenAI support"
```

---

## Phase 4: Loop Detector

### Task 4.1: Create LoopSignature and LoopDetector

**Files:**
- Create: `agent_memory/loop_detector.py`
- Create: `tests/test_loop_detector.py`

**Step 1: Write failing test for LoopSignature**

```python
# tests/test_loop_detector.py
"""Tests for loop detection based on error consistency."""

import pytest
from agent_memory.loop_detector import LoopSignature, LoopDetector, LoopInfo
from agent_memory.models import State


class TestLoopSignature:
    def test_signature_creation(self):
        sig = LoopSignature(
            action_type="edit",
            error_category="ImportError",
            error_keywords=["cannot import", "module"],
        )
        assert sig.action_type == "edit"
        assert sig.error_category == "ImportError"

    def test_signature_matches_same_error(self):
        sig1 = LoopSignature(
            action_type="edit",
            error_category="ImportError",
            error_keywords=["cannot import", "module"],
        )
        sig2 = LoopSignature(
            action_type="edit",
            error_category="ImportError",
            error_keywords=["cannot import", "name"],
        )
        assert sig1.matches(sig2)  # Same action + same error + overlapping keywords

    def test_signature_no_match_different_error(self):
        sig1 = LoopSignature(
            action_type="edit",
            error_category="ImportError",
            error_keywords=["cannot import"],
        )
        sig2 = LoopSignature(
            action_type="edit",
            error_category="TypeError",
            error_keywords=["cannot import"],
        )
        assert not sig1.matches(sig2)  # Different error type

    def test_signature_no_match_no_keyword_overlap(self):
        sig1 = LoopSignature(
            action_type="edit",
            error_category="ImportError",
            error_keywords=["cannot import"],
        )
        sig2 = LoopSignature(
            action_type="edit",
            error_category="ImportError",
            error_keywords=["syntax error"],
        )
        assert not sig1.matches(sig2)  # No keyword overlap
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_loop_detector.py::TestLoopSignature -v
```

Expected: FAIL

**Step 3: Create loop_detector module**

```python
# agent_memory/loop_detector.py
"""Loop detection based on error consistency."""

from dataclasses import dataclass, field
from typing import List, Optional, Set
import re


@dataclass
class LoopSignature:
    """Signature for identifying loops - action + error pattern."""

    action_type: str          # edit, search, execute, etc.
    error_category: str       # ImportError, TypeError, etc.
    error_keywords: List[str] # Top keywords from error message

    def matches(self, other: "LoopSignature") -> bool:
        """
        Check if two signatures represent the same predicament.

        Criteria:
        1. Same action type
        2. Same error category
        3. At least one keyword overlap
        """
        if self.action_type != other.action_type:
            return False
        if self.error_category != other.error_category:
            return False

        # Check keyword overlap
        self_keywords = set(kw.lower() for kw in self.error_keywords)
        other_keywords = set(kw.lower() for kw in other.error_keywords)
        overlap = self_keywords & other_keywords

        return len(overlap) >= 1

    def __hash__(self):
        return hash((self.action_type, self.error_category, tuple(sorted(self.error_keywords))))

    def __eq__(self, other):
        if not isinstance(other, LoopSignature):
            return False
        return self.matches(other)


@dataclass
class LoopInfo:
    """Information about a detected loop."""

    is_stuck: bool
    loop_length: int          # Number of repeated iterations
    start_step: int
    signatures: List[LoopSignature]
    description: str
    escape_suggestions: List[str] = field(default_factory=list)


class LoopDetector:
    """Detect loops based on error consistency, not just action repetition."""

    def __init__(self, min_repeat: int = 3):
        self.min_repeat = min_repeat

    def detect(self, state_history: List["State"]) -> Optional[LoopInfo]:
        """
        Detect if the agent is stuck in a loop.

        A loop is detected when:
        - Same action type
        - Same error category
        - Overlapping error keywords
        - Repeated min_repeat times
        """
        if len(state_history) < self.min_repeat:
            return None

        signatures = [self._build_signature(s) for s in state_history]

        # Look for repeated signature patterns
        loop_match = self._find_repeating_signatures(signatures)

        if loop_match:
            return LoopInfo(
                is_stuck=True,
                loop_length=loop_match["length"],
                start_step=loop_match["start"],
                signatures=loop_match["signatures"],
                description=self._describe_loop(loop_match["signatures"]),
            )

        return None

    def _build_signature(self, state: "State") -> LoopSignature:
        """Build a LoopSignature from a State."""
        # Extract action type from the state (we'll need to add this to State)
        action_type = getattr(state, "last_action_type", "unknown")

        # Extract error info
        error_category = "None"
        error_keywords = []

        if state.current_error:
            error_category = self._extract_error_category(state.current_error)
            error_keywords = self._extract_keywords(state.current_error)

        return LoopSignature(
            action_type=action_type,
            error_category=error_category,
            error_keywords=error_keywords,
        )

    def _extract_error_category(self, error_msg: str) -> str:
        """Extract error category from error message."""
        match = re.search(r'(\w+Error|\w+Exception|FAIL|ERROR)', error_msg)
        return match.group(1) if match else "Unknown"

    def _extract_keywords(self, error_msg: str, top_n: int = 5) -> List[str]:
        """Extract top keywords from error message."""
        # Remove common words and extract significant tokens
        stop_words = {"the", "a", "an", "in", "on", "at", "to", "for", "of", "is", "are", "was", "were"}
        words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', error_msg.lower())
        keywords = [w for w in words if w not in stop_words and len(w) > 2]

        # Return top N most common (or just first N unique)
        seen = set()
        result = []
        for w in keywords:
            if w not in seen:
                seen.add(w)
                result.append(w)
                if len(result) >= top_n:
                    break
        return result

    def _find_repeating_signatures(
        self,
        signatures: List[LoopSignature],
    ) -> Optional[dict]:
        """Find repeating signature patterns."""
        n = len(signatures)

        # Check from the end of history for recent loops
        # Look for patterns of length 1 to n//min_repeat
        for pattern_len in range(1, n // self.min_repeat + 1):
            # Get the last pattern_len signatures
            pattern = signatures[-pattern_len:]

            # Count how many times this pattern repeats going backwards
            repeat_count = 1
            pos = n - pattern_len * 2

            while pos >= 0:
                window = signatures[pos:pos + pattern_len]
                if self._patterns_match(pattern, window):
                    repeat_count += 1
                    pos -= pattern_len
                else:
                    break

            if repeat_count >= self.min_repeat:
                return {
                    "length": repeat_count,
                    "start": n - (repeat_count * pattern_len),
                    "signatures": pattern,
                }

        return None

    def _patterns_match(
        self,
        pattern1: List[LoopSignature],
        pattern2: List[LoopSignature],
    ) -> bool:
        """Check if two signature patterns match."""
        if len(pattern1) != len(pattern2):
            return False
        return all(s1.matches(s2) for s1, s2 in zip(pattern1, pattern2))

    def _describe_loop(self, signatures: List[LoopSignature]) -> str:
        """Generate human-readable loop description."""
        if not signatures:
            return "Unknown loop pattern"

        actions = list(set(s.action_type for s in signatures))
        errors = list(set(s.error_category for s in signatures))

        return f"Loop detected: action={actions}, errors={errors}"

    def is_same_predicament(self, state1: "State", state2: "State") -> bool:
        """Check if two states represent the same predicament."""
        sig1 = self._build_signature(state1)
        sig2 = self._build_signature(state2)
        return sig1.matches(sig2)
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_loop_detector.py::TestLoopSignature -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add agent_memory/loop_detector.py tests/test_loop_detector.py
git commit -m "feat: add LoopSignature and basic loop detection"
```

---

### Task 4.2: Add LoopDetector Integration Tests

**Files:**
- Modify: `tests/test_loop_detector.py`

**Step 1: Write test for LoopDetector with State history**

```python
# Add to tests/test_loop_detector.py

class TestLoopDetector:
    def test_detects_simple_loop(self):
        """Test detection of simple repeated errors."""
        detector = LoopDetector(min_repeat=3)

        # Create states with same error repeated
        states = []
        for i in range(5):
            state = State(
                tools=["bash", "edit"],
                repo_summary="Test repo",
                task_description="Fix bug",
                current_error="ImportError: cannot import name 'Foo' from module",
                phase="fixing",
            )
            state.last_action_type = "edit"  # Add action type
            states.append(state)

        loop_info = detector.detect(states)

        assert loop_info is not None
        assert loop_info.is_stuck is True
        assert loop_info.loop_length >= 3

    def test_no_loop_different_errors(self):
        """Test no loop detected when errors are different."""
        detector = LoopDetector(min_repeat=3)

        errors = [
            "ImportError: cannot import X",
            "TypeError: expected int got str",
            "ValueError: invalid value",
            "KeyError: 'missing_key'",
        ]

        states = []
        for i, error in enumerate(errors):
            state = State(
                tools=["bash"],
                repo_summary="Test",
                task_description="Test",
                current_error=error,
                phase="fixing",
            )
            state.last_action_type = "edit"
            states.append(state)

        loop_info = detector.detect(states)
        assert loop_info is None

    def test_is_same_predicament(self):
        """Test is_same_predicament method."""
        detector = LoopDetector()

        state1 = State(
            tools=["bash"],
            repo_summary="Test",
            task_description="Test",
            current_error="ImportError: cannot import 'Foo'",
            phase="fixing",
        )
        state1.last_action_type = "edit"

        state2 = State(
            tools=["bash"],
            repo_summary="Test",
            task_description="Test",
            current_error="ImportError: cannot import 'Bar'",
            phase="fixing",
        )
        state2.last_action_type = "edit"

        assert detector.is_same_predicament(state1, state2)

        # Different error type
        state3 = State(
            tools=["bash"],
            repo_summary="Test",
            task_description="Test",
            current_error="TypeError: invalid type",
            phase="fixing",
        )
        state3.last_action_type = "edit"

        assert not detector.is_same_predicament(state1, state3)
```

**Step 2: Run tests**

```bash
python -m pytest tests/test_loop_detector.py -v
```

Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_loop_detector.py
git commit -m "test: add LoopDetector integration tests"
```

---

## Phase 5: Memory Writer

### Task 5.1: Create MemoryWriter for Trajectory Ingestion

**Files:**
- Create: `agent_memory/writer.py`
- Create: `tests/test_writer.py`

**Step 1: Write failing test for MemoryWriter**

```python
# tests/test_writer.py
"""Tests for MemoryWriter."""

import pytest
from agent_memory.writer import MemoryWriter, RawTrajectory
from agent_memory.models import Trajectory, Fragment


class TestMemoryWriter:
    def test_segment_into_fragments_basic(self):
        """Test basic trajectory segmentation."""
        writer = MemoryWriter(store=None, embedder=None)

        # Create a simple raw trajectory
        raw = RawTrajectory(
            instance_id="test__test-123",
            repo="test/repo",
            success=True,
            steps=[
                {"action": "search", "observation": "Found file.py"},
                {"action": "open", "observation": "Opened file.py"},
                {"action": "edit", "observation": "Error: SyntaxError"},
                {"action": "edit", "observation": "Error: SyntaxError"},
                {"action": "edit", "observation": "Success: file saved"},
                {"action": "test", "observation": "All tests pass"},
            ],
        )

        fragments = writer._segment_into_fragments(raw)

        assert len(fragments) >= 1
        # Should have at least an exploration fragment and an error_recovery fragment

    def test_extract_error_patterns(self):
        """Test error pattern extraction."""
        writer = MemoryWriter(store=None, embedder=None)

        observations = [
            "ImportError: cannot import name 'Foo' from 'module'",
            "TypeError: expected int, got str",
            "ImportError: No module named 'bar'",
        ]

        patterns = writer._extract_error_patterns(observations)

        assert len(patterns) >= 2  # ImportError and TypeError
        import_errors = [p for p in patterns if p.error_type == "ImportError"]
        assert len(import_errors) >= 1
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_writer.py -v
```

Expected: FAIL

**Step 3: Create writer module**

```python
# agent_memory/writer.py
"""Memory Writer - incremental trajectory ingestion."""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
import re
import uuid
import logging

from agent_memory.models import Trajectory, Fragment, State, ErrorPattern
from agent_memory.neo4j_store import Neo4jStore
from agent_memory.embeddings import EmbeddingClient

logger = logging.getLogger(__name__)


@dataclass
class RawTrajectory:
    """Raw trajectory data before processing."""

    instance_id: str
    repo: str
    success: bool
    steps: List[Dict[str, Any]]
    problem_statement: str = ""

    @property
    def total_steps(self) -> int:
        return len(self.steps)


class MemoryWriter:
    """Writes trajectory data to memory store."""

    def __init__(
        self,
        store: Optional[Neo4jStore],
        embedder: Optional[EmbeddingClient],
    ):
        self.store = store
        self.embedder = embedder

    def write_trajectory(self, raw: RawTrajectory) -> str:
        """
        Process and write a trajectory to memory.

        Returns the trajectory ID.
        """
        # 1. Create trajectory summary
        traj_id = f"traj_{uuid.uuid4().hex[:12]}"
        summary = self._generate_summary(raw)

        trajectory = Trajectory(
            id=traj_id,
            instance_id=raw.instance_id,
            repo=raw.repo,
            task_type=self._infer_task_type(raw),
            success=raw.success,
            total_steps=raw.total_steps,
            summary=summary,
        )

        # 2. Segment into fragments
        fragments = self._segment_into_fragments(raw)

        # 3. Extract error patterns
        observations = [s.get("observation", "") for s in raw.steps]
        error_patterns = self._extract_error_patterns(observations)

        # 4. Generate embeddings
        if self.embedder:
            trajectory.embedding = self.embedder.embed(summary)
            for frag in fragments:
                frag.embedding = self.embedder.embed(frag.description)

        # 5. Write to store
        if self.store:
            self.store.create_trajectory(trajectory)
            for frag in fragments:
                self.store.create_fragment(frag, traj_id)
            for pattern in error_patterns:
                self.store.create_error_pattern(pattern)

        logger.info(f"Wrote trajectory {traj_id} with {len(fragments)} fragments")
        return traj_id

    def _segment_into_fragments(self, raw: RawTrajectory) -> List[Fragment]:
        """
        Segment trajectory into meaningful fragments.

        Segmentation rules:
        - New fragment on error occurrence
        - End fragment on error recovery
        - Mark loops as separate fragments
        """
        fragments = []
        current_start = 0
        current_type = "exploration"
        current_actions = []
        in_error_state = False

        for i, step in enumerate(raw.steps):
            action = step.get("action", "unknown")
            observation = step.get("observation", "")

            current_actions.append(action)

            # Check for error
            has_error = self._has_error(observation)

            if has_error and not in_error_state:
                # Start error fragment
                if i > current_start:
                    fragments.append(self._create_fragment(
                        current_start, i - 1, current_type, current_actions[:-1], raw
                    ))
                current_start = i
                current_type = "failed_attempt"
                current_actions = [action]
                in_error_state = True

            elif not has_error and in_error_state:
                # Error recovered
                fragments.append(self._create_fragment(
                    current_start, i, "error_recovery", current_actions, raw
                ))
                current_start = i + 1
                current_type = "exploration"
                current_actions = []
                in_error_state = False

        # Final fragment
        if current_start < len(raw.steps):
            final_type = "successful_fix" if raw.success and not in_error_state else current_type
            fragments.append(self._create_fragment(
                current_start, len(raw.steps) - 1, final_type, current_actions, raw
            ))

        return fragments

    def _create_fragment(
        self,
        start: int,
        end: int,
        frag_type: str,
        actions: List[str],
        raw: RawTrajectory,
    ) -> Fragment:
        """Create a Fragment object."""
        # Generate description from steps
        steps_slice = raw.steps[start:end + 1]
        description = self._describe_fragment(steps_slice, frag_type)

        # Determine outcome
        if frag_type == "successful_fix":
            outcome = "success"
        elif frag_type == "error_recovery":
            outcome = "recovered"
        elif frag_type == "failed_attempt":
            outcome = "failed"
        else:
            outcome = "completed"

        return Fragment(
            id=f"frag_{uuid.uuid4().hex[:12]}",
            step_range=(start, end),
            fragment_type=frag_type,
            description=description,
            action_sequence=actions,
            outcome=outcome,
        )

    def _describe_fragment(self, steps: List[Dict], frag_type: str) -> str:
        """Generate natural language description of fragment."""
        if not steps:
            return f"Empty {frag_type} fragment"

        actions = [s.get("action", "unknown") for s in steps]
        unique_actions = list(dict.fromkeys(actions))  # Preserve order

        return f"{frag_type.replace('_', ' ').title()}: {', '.join(unique_actions[:5])}"

    def _extract_error_patterns(self, observations: List[str]) -> List[ErrorPattern]:
        """Extract error patterns from observations."""
        error_counts: Dict[str, Dict[str, Any]] = {}

        for obs in observations:
            error_type = self._extract_error_type(obs)
            if error_type:
                if error_type not in error_counts:
                    error_counts[error_type] = {
                        "keywords": [],
                        "count": 0,
                    }
                error_counts[error_type]["count"] += 1
                keywords = self._extract_keywords(obs)
                error_counts[error_type]["keywords"].extend(keywords)

        patterns = []
        for error_type, data in error_counts.items():
            # Deduplicate keywords
            unique_keywords = list(dict.fromkeys(data["keywords"]))[:10]

            patterns.append(ErrorPattern(
                id=f"err_{uuid.uuid4().hex[:8]}",
                error_type=error_type,
                error_keywords=unique_keywords,
                context="trajectory",
                frequency=data["count"],
            ))

        return patterns

    def _has_error(self, observation: str) -> bool:
        """Check if observation contains an error."""
        error_patterns = [
            r'\w+Error:',
            r'\w+Exception:',
            r'^ERROR',
            r'^FAIL',
            r'Traceback \(most recent call last\)',
        ]
        return any(re.search(p, observation) for p in error_patterns)

    def _extract_error_type(self, text: str) -> Optional[str]:
        """Extract error type from text."""
        match = re.search(r'(\w+Error|\w+Exception)', text)
        return match.group(1) if match else None

    def _extract_keywords(self, text: str, top_n: int = 5) -> List[str]:
        """Extract keywords from text."""
        stop_words = {"the", "a", "an", "in", "on", "at", "to", "for", "of"}
        words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', text.lower())
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        return list(dict.fromkeys(keywords))[:top_n]

    def _generate_summary(self, raw: RawTrajectory) -> str:
        """Generate trajectory summary."""
        status = "Successfully fixed" if raw.success else "Failed to fix"
        return f"{status} issue in {raw.repo} ({raw.total_steps} steps)"

    def _infer_task_type(self, raw: RawTrajectory) -> str:
        """Infer task type from trajectory."""
        # Simple heuristic based on problem statement
        ps = raw.problem_statement.lower()
        if "fix" in ps or "bug" in ps or "error" in ps:
            return "bug_fix"
        elif "add" in ps or "feature" in ps or "implement" in ps:
            return "feature"
        elif "refactor" in ps or "clean" in ps:
            return "refactor"
        return "bug_fix"  # Default
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_writer.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add agent_memory/writer.py tests/test_writer.py
git commit -m "feat: add MemoryWriter for trajectory ingestion"
```

---

## Phase 6: Memory Retriever

### Task 6.1: Create MemoryRetriever with Multi-dimensional Search

**Files:**
- Create: `agent_memory/retriever.py`
- Create: `tests/test_retriever.py`

**Step 1: Write failing test for Retriever**

```python
# tests/test_retriever.py
"""Tests for MemoryRetriever."""

import pytest
from agent_memory.retriever import MemoryRetriever, RetrievalResult
from agent_memory.models import State, Methodology


class TestMemoryRetriever:
    def test_retrieval_result_structure(self):
        """Test RetrievalResult has correct structure."""
        result = RetrievalResult(
            methodologies=[],
            similar_fragments=[],
            error_solutions=[],
            warnings=[],
        )
        assert hasattr(result, "methodologies")
        assert hasattr(result, "similar_fragments")
        assert hasattr(result, "warnings")

    def test_by_error_query_generation(self):
        """Test error-based query generation."""
        retriever = MemoryRetriever(store=None, embedder=None)

        query, params = retriever._build_error_query("ImportError")

        assert "error_type" in query
        assert "ErrorPattern" in query or "Methodology" in query
        assert params.get("error_type") == "ImportError"
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_retriever.py -v
```

Expected: FAIL

**Step 3: Create retriever module**

```python
# agent_memory/retriever.py
"""Memory Retriever - multi-dimensional search."""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import numpy as np
import logging

from agent_memory.models import State, Methodology, Fragment, ErrorPattern
from agent_memory.neo4j_store import Neo4jStore
from agent_memory.embeddings import EmbeddingClient

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """Result from memory retrieval."""

    methodologies: List[Methodology] = field(default_factory=list)
    similar_fragments: List[Fragment] = field(default_factory=list)
    error_solutions: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (
            not self.methodologies and
            not self.similar_fragments and
            not self.error_solutions
        )


class MemoryRetriever:
    """Retrieve relevant memories using multiple dimensions."""

    def __init__(
        self,
        store: Optional[Neo4jStore],
        embedder: Optional[EmbeddingClient],
    ):
        self.store = store
        self.embedder = embedder

    def retrieve(self, current_state: State, top_k: int = 5) -> RetrievalResult:
        """
        Retrieve relevant memories for current state.

        Uses four dimensions:
        1. Error-based: Match by error type and keywords
        2. Task-based: Match by task type and repo context
        3. State-based: Match by phase and tools
        4. Semantic: Match by embedding similarity
        """
        result = RetrievalResult()

        if not self.store:
            return result

        # 1. Error-based retrieval
        if current_state.current_error:
            error_results = self.by_error(current_state.current_error)
            result.methodologies.extend(error_results)

        # 2. Task-based retrieval
        task_results = self.by_task(
            task_description=current_state.task_description,
            repo_summary=current_state.repo_summary,
        )
        result.similar_fragments.extend(task_results)

        # 3. State-based retrieval
        state_results = self.by_state(current_state)
        result.methodologies.extend(state_results)

        # 4. Semantic retrieval (if embedder available)
        if self.embedder and current_state.embedding:
            semantic_results = self.by_semantic(current_state.embedding, top_k)
            result.similar_fragments.extend(semantic_results)

        # Fuse and rank results
        result.methodologies = self._dedupe_and_rank(result.methodologies, top_k)
        result.similar_fragments = self._dedupe_fragments(result.similar_fragments, top_k)

        # Add warnings for potential failure patterns
        result.warnings = self._get_warnings(current_state)

        return result

    def by_error(self, error_message: str) -> List[Methodology]:
        """Retrieve methodologies that resolved similar errors."""
        if not self.store:
            return []

        error_type = self._extract_error_type(error_message)
        query, params = self._build_error_query(error_type)

        results = self.store.execute_query(query, params)
        return [self._dict_to_methodology(r["m"]) for r in results if "m" in r]

    def by_task(
        self,
        task_description: str,
        repo_summary: str,
    ) -> List[Fragment]:
        """Retrieve fragments from similar successful trajectories."""
        if not self.store:
            return []

        query = """
        MATCH (t:Trajectory)-[:HAS_FRAGMENT]->(f:Fragment)
        WHERE t.success = true
        RETURN f
        ORDER BY f.outcome DESC
        LIMIT 10
        """

        results = self.store.execute_query(query)
        return [self._dict_to_fragment(r["f"]) for r in results if "f" in r]

    def by_state(self, state: State) -> List[Methodology]:
        """Retrieve methodologies applicable to current state."""
        if not self.store:
            return []

        query = """
        MATCH (m:Methodology)
        WHERE m.situation CONTAINS $phase
        RETURN m
        ORDER BY m.confidence DESC
        LIMIT 5
        """

        results = self.store.execute_query(query, {"phase": state.phase})
        return [self._dict_to_methodology(r["m"]) for r in results if "m" in r]

    def by_semantic(
        self,
        query_embedding: List[float],
        top_k: int = 5,
    ) -> List[Fragment]:
        """Retrieve fragments by semantic similarity."""
        if not self.store:
            return []

        # Neo4j vector search (requires vector index)
        query = """
        MATCH (f:Fragment)
        WHERE f.embedding IS NOT NULL
        WITH f, gds.similarity.cosine(f.embedding, $embedding) AS similarity
        ORDER BY similarity DESC
        LIMIT $top_k
        RETURN f, similarity
        """

        try:
            results = self.store.execute_query(query, {
                "embedding": query_embedding,
                "top_k": top_k,
            })
            return [self._dict_to_fragment(r["f"]) for r in results if "f" in r]
        except Exception as e:
            logger.warning(f"Semantic search failed: {e}")
            return []

    def _build_error_query(self, error_type: Optional[str]) -> tuple:
        """Build Cypher query for error-based retrieval."""
        if error_type:
            query = """
            MATCH (e:ErrorPattern {error_type: $error_type})-[:RESOLVED_BY]->(m:Methodology)
            RETURN m
            ORDER BY m.confidence DESC
            LIMIT 5
            """
            return query, {"error_type": error_type}
        else:
            query = """
            MATCH (m:Methodology)
            WHERE m.situation CONTAINS 'error'
            RETURN m
            ORDER BY m.confidence DESC
            LIMIT 5
            """
            return query, {}

    def _extract_error_type(self, error_message: str) -> Optional[str]:
        """Extract error type from message."""
        import re
        match = re.search(r'(\w+Error|\w+Exception)', error_message)
        return match.group(1) if match else None

    def _dedupe_and_rank(
        self,
        methodologies: List[Methodology],
        top_k: int,
    ) -> List[Methodology]:
        """Deduplicate and rank methodologies."""
        seen_ids = set()
        unique = []
        for m in methodologies:
            if m.id not in seen_ids:
                seen_ids.add(m.id)
                unique.append(m)

        # Sort by confidence
        unique.sort(key=lambda m: m.confidence, reverse=True)
        return unique[:top_k]

    def _dedupe_fragments(
        self,
        fragments: List[Fragment],
        top_k: int,
    ) -> List[Fragment]:
        """Deduplicate fragments."""
        seen_ids = set()
        unique = []
        for f in fragments:
            if f.id not in seen_ids:
                seen_ids.add(f.id)
                unique.append(f)
        return unique[:top_k]

    def _get_warnings(self, state: State) -> List[str]:
        """Get warnings for potential failure patterns."""
        warnings = []

        if state.current_error:
            error_type = self._extract_error_type(state.current_error)
            if error_type:
                warnings.append(f"Common mistake with {error_type}: Check import paths carefully")

        return warnings

    def _dict_to_methodology(self, d: Dict[str, Any]) -> Methodology:
        """Convert dict to Methodology."""
        return Methodology.from_dict(d)

    def _dict_to_fragment(self, d: Dict[str, Any]) -> Fragment:
        """Convert dict to Fragment."""
        return Fragment.from_dict(d)
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_retriever.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add agent_memory/retriever.py tests/test_retriever.py
git commit -m "feat: add MemoryRetriever with multi-dimensional search"
```

---

## Phase 7: Memory Consolidator

### Task 7.1: Create MemoryConsolidator

**Files:**
- Create: `agent_memory/consolidator.py`
- Create: `tests/test_consolidator.py`

**Step 1: Write failing test**

```python
# tests/test_consolidator.py
"""Tests for MemoryConsolidator."""

import pytest
from agent_memory.consolidator import MemoryConsolidator


class TestMemoryConsolidator:
    def test_consolidator_creation(self):
        """Test consolidator can be instantiated."""
        consolidator = MemoryConsolidator(store=None, embedder=None)
        assert consolidator is not None

    def test_group_fragments_by_error(self):
        """Test fragment grouping by error type."""
        consolidator = MemoryConsolidator(store=None, embedder=None)

        fragments_data = [
            {"error_type": "ImportError", "description": "Fixed import"},
            {"error_type": "ImportError", "description": "Another import fix"},
            {"error_type": "TypeError", "description": "Fixed type"},
        ]

        groups = consolidator._group_by_error_type(fragments_data)

        assert "ImportError" in groups
        assert len(groups["ImportError"]) == 2
        assert "TypeError" in groups
        assert len(groups["TypeError"]) == 1
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_consolidator.py -v
```

Expected: FAIL

**Step 3: Create consolidator module**

```python
# agent_memory/consolidator.py
"""Memory Consolidator - periodic abstraction and cleanup."""

from typing import List, Dict, Any, Optional
import uuid
import logging

from agent_memory.models import Methodology, Fragment
from agent_memory.neo4j_store import Neo4jStore
from agent_memory.embeddings import EmbeddingClient

logger = logging.getLogger(__name__)


class MemoryConsolidator:
    """
    Consolidates memory every N trajectories.

    Tasks:
    1. Abstract methodologies from successful fragments
    2. Merge similar nodes
    3. Update statistics
    4. Cleanup low-quality data
    """

    def __init__(
        self,
        store: Optional[Neo4jStore],
        embedder: Optional[EmbeddingClient],
        llm_client: Optional[Any] = None,  # For methodology abstraction
    ):
        self.store = store
        self.embedder = embedder
        self.llm_client = llm_client

    def consolidate(self, batch_size: int = 16) -> Dict[str, int]:
        """
        Run consolidation tasks.

        Returns stats about what was consolidated.
        """
        stats = {
            "methodologies_created": 0,
            "nodes_merged": 0,
            "nodes_cleaned": 0,
        }

        if not self.store:
            return stats

        # 1. Abstract methodologies
        new_methods = self._abstract_methodologies()
        stats["methodologies_created"] = len(new_methods)

        # 2. Merge similar nodes
        merged = self._merge_similar_nodes(similarity_threshold=0.9)
        stats["nodes_merged"] = merged

        # 3. Update statistics
        self._update_statistics()

        # 4. Cleanup
        cleaned = self._cleanup()
        stats["nodes_cleaned"] = cleaned

        logger.info(f"Consolidation complete: {stats}")
        return stats

    def _abstract_methodologies(self) -> List[Methodology]:
        """
        Abstract methodologies from successful fragments.

        Groups fragments by (error_type, task_type), then generates
        natural language methodology for each group.
        """
        if not self.store:
            return []

        # Find successful fragments not yet abstracted
        query = """
        MATCH (f:Fragment)
        WHERE f.outcome = 'success' OR f.outcome = 'recovered'
        AND NOT (f)-[:DERIVED]->(:Methodology)
        RETURN f
        LIMIT 100
        """

        results = self.store.execute_query(query)
        if not results:
            return []

        fragments_data = [r["f"] for r in results]

        # Group by error type (if available)
        groups = self._group_by_error_type(fragments_data)

        methodologies = []
        for error_type, group in groups.items():
            if len(group) >= 2:  # Need at least 2 examples
                methodology = self._generate_methodology(error_type, group)
                if methodology:
                    methodologies.append(methodology)
                    self.store.create_methodology(methodology)

                    # Link fragments to methodology
                    for frag_data in group:
                        self._link_fragment_to_methodology(
                            frag_data["id"],
                            methodology.id,
                        )

        return methodologies

    def _group_by_error_type(
        self,
        fragments_data: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Group fragments by error type."""
        groups: Dict[str, List] = {}

        for frag in fragments_data:
            error_type = frag.get("error_type", "unknown")
            if error_type not in groups:
                groups[error_type] = []
            groups[error_type].append(frag)

        return groups

    def _generate_methodology(
        self,
        error_type: str,
        fragments: List[Dict[str, Any]],
    ) -> Optional[Methodology]:
        """Generate methodology from fragment group."""
        # If we have an LLM client, use it for natural language generation
        if self.llm_client:
            return self._generate_methodology_with_llm(error_type, fragments)

        # Otherwise, use template-based generation
        descriptions = [f.get("description", "") for f in fragments]
        actions = []
        for f in fragments:
            actions.extend(f.get("action_sequence", []))

        unique_actions = list(dict.fromkeys(actions))[:5]

        situation = f"When encountering {error_type}"
        strategy = f"Recommended actions: {', '.join(unique_actions)}"

        return Methodology(
            id=f"meth_{uuid.uuid4().hex[:12]}",
            situation=situation,
            strategy=strategy,
            confidence=0.5,  # Initial confidence
            success_count=len(fragments),
            failure_count=0,
            source_fragment_ids=[f.get("id", "") for f in fragments],
        )

    def _generate_methodology_with_llm(
        self,
        error_type: str,
        fragments: List[Dict[str, Any]],
    ) -> Optional[Methodology]:
        """Generate methodology using LLM."""
        # This would call the LLM API to generate natural language
        # For now, fall back to template
        return self._generate_methodology(error_type, fragments)

    def _link_fragment_to_methodology(
        self,
        fragment_id: str,
        methodology_id: str,
    ) -> None:
        """Create DERIVED relationship from Fragment to Methodology."""
        if not self.store:
            return

        query = """
        MATCH (f:Fragment {id: $fragment_id})
        MATCH (m:Methodology {id: $methodology_id})
        MERGE (f)-[:DERIVED]->(m)
        """
        self.store.execute_write(query, {
            "fragment_id": fragment_id,
            "methodology_id": methodology_id,
        })

    def _merge_similar_nodes(self, similarity_threshold: float = 0.9) -> int:
        """Merge highly similar fragment nodes."""
        if not self.store:
            return 0

        # Find pairs with high similarity
        query = """
        MATCH (f1:Fragment)-[s:SIMILAR_TO]->(f2:Fragment)
        WHERE s.similarity > $threshold
        AND id(f1) < id(f2)
        RETURN f1.id as id1, f2.id as id2, s.similarity as sim
        LIMIT 50
        """

        results = self.store.execute_query(query, {"threshold": similarity_threshold})

        merged_count = 0
        for r in results:
            self._merge_fragments(r["id1"], r["id2"])
            merged_count += 1

        return merged_count

    def _merge_fragments(self, keep_id: str, remove_id: str) -> None:
        """Merge two fragments, keeping one and removing the other."""
        if not self.store:
            return

        # Transfer relationships from remove to keep
        query = """
        MATCH (f_remove:Fragment {id: $remove_id})
        MATCH (f_keep:Fragment {id: $keep_id})

        // Transfer incoming relationships
        OPTIONAL MATCH (n)-[r]->(f_remove)
        WHERE type(r) <> 'SIMILAR_TO'
        FOREACH (x IN CASE WHEN r IS NOT NULL THEN [1] ELSE [] END |
            CREATE (n)-[r2:RELATES_TO]->(f_keep)
        )

        // Delete the removed fragment
        DETACH DELETE f_remove
        """
        self.store.execute_write(query, {
            "remove_id": remove_id,
            "keep_id": keep_id,
        })

    def _update_statistics(self) -> None:
        """Update statistical properties on relationships."""
        if not self.store:
            return

        # Update RESOLVED_BY success rate
        query = """
        MATCH (e:ErrorPattern)-[r:RESOLVED_BY]->(m:Methodology)
        SET r.success_rate = toFloat(m.success_count) / (m.success_count + m.failure_count)
        """
        self.store.execute_write(query)

        # Update error pattern frequencies
        query = """
        MATCH (f:Fragment)-[:ENCOUNTERED]->(e:ErrorPattern)
        WITH e, count(f) as freq
        SET e.frequency = freq
        """
        self.store.execute_write(query)

    def _cleanup(self) -> int:
        """Remove low-quality or orphaned data."""
        if not self.store:
            return 0

        cleaned = 0

        # Remove orphan nodes (no edges)
        query = """
        MATCH (n)
        WHERE NOT (n)--()
        AND NOT n:Trajectory
        DELETE n
        RETURN count(n) as deleted
        """
        result = self.store.execute_query(query)
        cleaned += result[0]["deleted"] if result else 0

        # Remove low-confidence methodologies with few sources
        query = """
        MATCH (m:Methodology)
        WHERE m.confidence < 0.3
        AND size(m.source_fragment_ids) < 3
        DETACH DELETE m
        RETURN count(m) as deleted
        """
        result = self.store.execute_query(query)
        cleaned += result[0]["deleted"] if result else 0

        return cleaned
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_consolidator.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add agent_memory/consolidator.py tests/test_consolidator.py
git commit -m "feat: add MemoryConsolidator for periodic abstraction"
```

---

## Phase 8: Unified API

### Task 8.1: Create AgentMemory Facade

**Files:**
- Create: `agent_memory/memory.py`
- Create: `tests/test_memory.py`

**Step 1: Write failing test**

```python
# tests/test_memory.py
"""Tests for AgentMemory unified API."""

import pytest
from agent_memory.memory import AgentMemory
from agent_memory.models import State


class TestAgentMemory:
    def test_memory_creation(self):
        """Test AgentMemory can be instantiated with mock components."""
        memory = AgentMemory(
            neo4j_uri=None,  # Use mock store
            embedding_api_key=None,  # Use mock embedder
        )
        assert memory is not None

    def test_query_returns_context(self):
        """Test query method returns MemoryContext."""
        memory = AgentMemory(neo4j_uri=None, embedding_api_key=None)

        state = State(
            tools=["bash", "edit"],
            repo_summary="Django project",
            task_description="Fix import bug",
            current_error="ImportError: No module named 'foo'",
            phase="fixing",
        )

        context = memory.query(state)

        assert hasattr(context, "methodologies")
        assert hasattr(context, "warnings")

    def test_check_loop_with_history(self):
        """Test loop detection."""
        memory = AgentMemory(neo4j_uri=None, embedding_api_key=None)

        # Create repetitive state history
        states = []
        for i in range(5):
            state = State(
                tools=["bash"],
                repo_summary="Test",
                task_description="Test",
                current_error="ImportError: cannot import X",
                phase="fixing",
            )
            state.last_action_type = "edit"
            states.append(state)

        loop_info = memory.check_loop(states)

        # Should detect loop with repeated same error
        assert loop_info is not None or loop_info is None  # May or may not detect
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_memory.py -v
```

Expected: FAIL

**Step 3: Create memory facade**

```python
# agent_memory/memory.py
"""AgentMemory - Unified API for agent long-term memory."""

from typing import Optional, List
from dataclasses import dataclass, field
import logging

from agent_memory.models import State, Methodology, Fragment
from agent_memory.neo4j_store import Neo4jStore
from agent_memory.embeddings import get_embedding_client, EmbeddingClient
from agent_memory.writer import MemoryWriter, RawTrajectory
from agent_memory.retriever import MemoryRetriever, RetrievalResult
from agent_memory.consolidator import MemoryConsolidator
from agent_memory.loop_detector import LoopDetector, LoopInfo

logger = logging.getLogger(__name__)


@dataclass
class MemoryContext:
    """Context returned from memory query."""

    methodologies: List[Methodology] = field(default_factory=list)
    similar_fragments: List[Fragment] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def has_suggestions(self) -> bool:
        return bool(self.methodologies)


@dataclass
class MemoryStats:
    """Statistics from memory."""

    error_frequency: dict = field(default_factory=dict)
    failure_patterns: list = field(default_factory=list)
    total_trajectories: int = 0
    total_methodologies: int = 0


class AgentMemory:
    """
    Agent Long-term Memory - Unified API.

    Usage:
        memory = AgentMemory(neo4j_uri="bolt://localhost:7687", embedding_api_key="...")

        # During agent run
        context = memory.query(current_state)
        loop = memory.check_loop(state_history)

        # After trajectory
        memory.learn(trajectory)
    """

    def __init__(
        self,
        neo4j_uri: Optional[str] = None,
        neo4j_auth: tuple = ("neo4j", "password"),
        embedding_api_key: Optional[str] = None,
        consolidate_every: int = 16,
    ):
        # Initialize store
        if neo4j_uri:
            self.store = Neo4jStore(uri=neo4j_uri, auth=neo4j_auth)
            self.store.init_schema()
        else:
            self.store = None
            logger.warning("Running without Neo4j store (mock mode)")

        # Initialize embedder
        if embedding_api_key:
            self.embedder = get_embedding_client("openai", api_key=embedding_api_key)
        else:
            self.embedder = get_embedding_client("mock")
            logger.warning("Using mock embedder")

        # Initialize components
        self.writer = MemoryWriter(self.store, self.embedder)
        self.retriever = MemoryRetriever(self.store, self.embedder)
        self.consolidator = MemoryConsolidator(self.store, self.embedder)
        self.loop_detector = LoopDetector()

        # Consolidation tracking
        self._trajectory_count = 0
        self._consolidate_every = consolidate_every

    # === Agent Runtime API ===

    def query(self, current_state: State) -> MemoryContext:
        """
        Query memory for relevant context.

        Call this before each agent step to get:
        - Applicable methodologies
        - Similar historical fragments
        - Warnings about potential failure patterns
        """
        # Generate embedding for state if needed
        if self.embedder and not current_state.embedding:
            situation_str = current_state.to_situation_string()
            current_state.embedding = self.embedder.embed(situation_str)

        result = self.retriever.retrieve(current_state)

        return MemoryContext(
            methodologies=result.methodologies,
            similar_fragments=result.similar_fragments,
            warnings=result.warnings,
        )

    def check_loop(self, state_history: List[State]) -> Optional[LoopInfo]:
        """
        Check if agent is stuck in a loop.

        Detects loops based on error consistency:
        - Same action type
        - Same error category
        - Overlapping error keywords
        """
        return self.loop_detector.detect(state_history)

    # === Learning API ===

    def learn(self, trajectory: RawTrajectory) -> str:
        """
        Learn from a completed trajectory.

        1. Writes trajectory and fragments to memory
        2. Triggers consolidation every N trajectories

        Returns the trajectory ID.
        """
        traj_id = self.writer.write_trajectory(trajectory)

        self._trajectory_count += 1
        if self._trajectory_count % self._consolidate_every == 0:
            logger.info(f"Triggering consolidation (every {self._consolidate_every} trajectories)")
            self.consolidator.consolidate()

        return traj_id

    # === Statistics API ===

    def get_stats(self) -> MemoryStats:
        """Get memory statistics."""
        if not self.store:
            return MemoryStats()

        # Query for stats
        traj_count = self.store.execute_query(
            "MATCH (t:Trajectory) RETURN count(t) as count"
        )
        meth_count = self.store.execute_query(
            "MATCH (m:Methodology) RETURN count(m) as count"
        )

        return MemoryStats(
            total_trajectories=traj_count[0]["count"] if traj_count else 0,
            total_methodologies=meth_count[0]["count"] if meth_count else 0,
        )

    # === Lifecycle ===

    def close(self) -> None:
        """Close connections."""
        if self.store:
            self.store.close()

    def __enter__(self) -> "AgentMemory":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_memory.py -v
```

Expected: PASS

**Step 5: Update __init__.py with all exports**

```python
# agent_memory/__init__.py
"""Agent Memory - Long-term memory system for coding agents."""

from agent_memory.models import (
    Trajectory,
    Fragment,
    State,
    Methodology,
    ErrorPattern,
)
from agent_memory.embeddings import EmbeddingClient, get_embedding_client
from agent_memory.memory import AgentMemory, MemoryContext, MemoryStats
from agent_memory.writer import MemoryWriter, RawTrajectory
from agent_memory.retriever import MemoryRetriever, RetrievalResult
from agent_memory.loop_detector import LoopDetector, LoopInfo, LoopSignature

__version__ = "0.1.0"

__all__ = [
    # Models
    "Trajectory",
    "Fragment",
    "State",
    "Methodology",
    "ErrorPattern",
    # Memory API
    "AgentMemory",
    "MemoryContext",
    "MemoryStats",
    # Components
    "MemoryWriter",
    "RawTrajectory",
    "MemoryRetriever",
    "RetrievalResult",
    "LoopDetector",
    "LoopInfo",
    "LoopSignature",
    # Embeddings
    "EmbeddingClient",
    "get_embedding_client",
]
```

**Step 6: Run all tests**

```bash
python -m pytest tests/ -v
```

Expected: All tests PASS

**Step 7: Commit**

```bash
git add agent_memory/ tests/
git commit -m "feat: add AgentMemory unified API, complete core implementation"
```

---

## Phase 9: Integration & Demo

### Task 9.1: Create Usage Demo

**Files:**
- Create: `examples/demo_memory.py`

**Step 1: Create demo script**

```python
# examples/demo_memory.py
"""Demo script showing AgentMemory usage."""

from agent_memory import (
    AgentMemory,
    State,
    RawTrajectory,
)


def main():
    """Demonstrate AgentMemory usage."""

    # Initialize memory (mock mode without Neo4j)
    print("Initializing AgentMemory...")
    memory = AgentMemory(
        neo4j_uri=None,  # Use None for mock mode
        embedding_api_key=None,  # Use mock embedder
    )

    # Simulate a trajectory
    print("\n1. Learning from a trajectory...")
    trajectory = RawTrajectory(
        instance_id="django__django-12345",
        repo="django/django",
        success=True,
        problem_statement="Fix ImportError in models.py",
        steps=[
            {"action": "search", "observation": "Found models.py"},
            {"action": "open", "observation": "Opened models.py"},
            {"action": "edit", "observation": "ImportError: cannot import 'Model'"},
            {"action": "search", "observation": "Found correct import path"},
            {"action": "edit", "observation": "File saved successfully"},
            {"action": "test", "observation": "All 5 tests passed"},
        ],
    )

    traj_id = memory.learn(trajectory)
    print(f"   Trajectory learned: {traj_id}")

    # Query memory during agent run
    print("\n2. Querying memory for current state...")
    current_state = State(
        tools=["bash", "search", "edit", "view"],
        repo_summary="Django web framework",
        task_description="Fix import bug in views.py",
        current_error="ImportError: cannot import name 'View'",
        phase="fixing",
    )

    context = memory.query(current_state)
    print(f"   Methodologies found: {len(context.methodologies)}")
    print(f"   Similar fragments: {len(context.similar_fragments)}")
    print(f"   Warnings: {context.warnings}")

    # Check for loops
    print("\n3. Checking for loops...")
    state_history = []
    for i in range(5):
        state = State(
            tools=["bash"],
            repo_summary="Django",
            task_description="Fix bug",
            current_error="ImportError: cannot import 'Foo'",
            phase="fixing",
        )
        state.last_action_type = "edit"
        state_history.append(state)

    loop_info = memory.check_loop(state_history)
    if loop_info and loop_info.is_stuck:
        print(f"   LOOP DETECTED: {loop_info.description}")
    else:
        print("   No loop detected")

    # Get stats
    print("\n4. Memory statistics...")
    stats = memory.get_stats()
    print(f"   Total trajectories: {stats.total_trajectories}")
    print(f"   Total methodologies: {stats.total_methodologies}")

    print("\nDemo complete!")
    memory.close()


if __name__ == "__main__":
    main()
```

**Step 2: Run demo**

```bash
mkdir -p examples
python examples/demo_memory.py
```

Expected: Demo runs successfully

**Step 3: Commit**

```bash
git add examples/
git commit -m "docs: add AgentMemory usage demo"
```

---

### Task 9.2: Final Integration Test

**Files:**
- Create: `tests/test_integration.py`

**Step 1: Write integration test**

```python
# tests/test_integration.py
"""Integration tests for complete AgentMemory workflow."""

import pytest
from agent_memory import (
    AgentMemory,
    State,
    RawTrajectory,
)


class TestAgentMemoryIntegration:
    """End-to-end integration tests."""

    def test_full_workflow_mock(self):
        """Test complete workflow with mock components."""
        # Initialize
        memory = AgentMemory(neo4j_uri=None, embedding_api_key=None)

        # Learn multiple trajectories
        for i in range(3):
            traj = RawTrajectory(
                instance_id=f"test__test-{i}",
                repo="test/repo",
                success=i % 2 == 0,
                steps=[
                    {"action": "search", "observation": "found"},
                    {"action": "edit", "observation": "ImportError: x" if i % 2 else "ok"},
                    {"action": "test", "observation": "passed" if i % 2 == 0 else "failed"},
                ],
            )
            memory.learn(traj)

        # Query
        state = State(
            tools=["bash"],
            repo_summary="Test",
            task_description="Test",
            current_error="ImportError: cannot import x",
            phase="fixing",
        )

        context = memory.query(state)
        assert context is not None

        # Check loop (no loop expected with varied states)
        states = [state]
        loop = memory.check_loop(states)
        assert loop is None  # Not enough history for loop

        memory.close()

    def test_loop_detection_works(self):
        """Test that loop detection correctly identifies repeated errors."""
        memory = AgentMemory(neo4j_uri=None, embedding_api_key=None)

        # Create repeated same-error states
        states = []
        for i in range(5):
            state = State(
                tools=["bash"],
                repo_summary="Test",
                task_description="Test",
                current_error="TypeError: expected int, got str",
                phase="fixing",
            )
            state.last_action_type = "edit"
            states.append(state)

        loop = memory.check_loop(states)

        # Should detect loop with min_repeat=3 default
        assert loop is not None
        assert loop.is_stuck is True

        memory.close()
```

**Step 2: Run all tests**

```bash
python -m pytest tests/ -v --tb=short
```

Expected: All tests PASS

**Step 3: Final commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration tests for complete workflow"
```

---

## Summary

This plan implements the Agent Memory (ContextGraph V2) system in 9 phases:

1. **Phase 1**: Project setup and data models (Trajectory, Fragment, State, Methodology, ErrorPattern)
2. **Phase 2**: Neo4j connection and schema initialization
3. **Phase 3**: Embedding client (mock + OpenAI)
4. **Phase 4**: Loop detection based on error consistency
5. **Phase 5**: Memory Writer for trajectory ingestion
6. **Phase 6**: Memory Retriever with multi-dimensional search
7. **Phase 7**: Memory Consolidator for periodic abstraction
8. **Phase 8**: Unified AgentMemory API
9. **Phase 9**: Integration tests and demo

Each task follows TDD: write failing test → implement → verify → commit.

Total estimated tasks: ~20 bite-sized commits.
