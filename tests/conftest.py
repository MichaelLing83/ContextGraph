"""Pytest fixtures for agent_memory tests."""

import pytest
import os
import sys
from pathlib import Path

# Make sibling test modules importable as plain `from _http_helpers import …`
# from any test file under tests/. Pytest doesn't add tests/ to sys.path by
# default; this is the single owner of that path setup.
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


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
def neo4j_available():
    """Check if Neo4j is available."""
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "password")
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        driver.close()
        return True
    except Exception:
        return False
