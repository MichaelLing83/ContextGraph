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
