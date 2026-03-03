"""Tests for EntityResolver."""

import pytest

from agent_memory.entity_resolver import EntityResolver
from agent_memory.models import ErrorPattern, Methodology


class TestEntityResolver:
    def test_resolve_error_pattern_new(self):
        """New error pattern with no store should return as new."""
        resolver = EntityResolver(store=None, embedder=None)
        pattern = ErrorPattern(
            id="err_1", error_type="ImportError",
            error_keywords=["import", "module"],
            context="test", frequency=1,
        )
        resolved, is_new = resolver.resolve_error_pattern(pattern)
        assert is_new
        assert resolved is pattern

    def test_resolve_error_pattern_merge(self):
        """Duplicate error pattern should be merged into existing."""
        class DummyStore:
            def execute_query(self, query, parameters=None):
                # Return an existing pattern with overlapping keywords
                return [{
                    "e": {
                        "id": "err_existing",
                        "error_type": "ImportError",
                        "error_keywords": ["import", "module", "django"],
                        "context": "test",
                        "frequency": 5,
                    }
                }]

        # Threshold 0.3 with keywords ["import", "settings"] vs ["import", "module", "django"]
        # Jaccard = |{import}| / |{import, settings, module, django}| = 1/4 = 0.25
        # So we need threshold <= 0.25. Use 0.2 to ensure merge.
        resolver = EntityResolver(store=DummyStore(), embedder=None, error_threshold=0.2)
        pattern = ErrorPattern(
            id="err_new", error_type="ImportError",
            error_keywords=["import", "settings"],
            context="test", frequency=1,
        )
        resolved, is_new = resolver.resolve_error_pattern(pattern)

        assert not is_new
        assert resolved.id == "err_existing"
        assert "settings" in resolved.error_keywords
        assert resolved.frequency == 6

    def test_resolve_error_pattern_no_match(self):
        """Non-matching pattern should stay new."""
        class DummyStore:
            def execute_query(self, query, parameters=None):
                return [{
                    "e": {
                        "id": "err_other",
                        "error_type": "ImportError",
                        "error_keywords": ["numpy", "array", "ndarray"],
                        "context": "scientific",
                        "frequency": 3,
                    }
                }]

        # High threshold ensures no match with disjoint keywords
        resolver = EntityResolver(store=DummyStore(), embedder=None, error_threshold=0.9)
        pattern = ErrorPattern(
            id="err_new", error_type="ImportError",
            error_keywords=["django", "settings", "installed_apps"],
            context="web", frequency=1,
        )
        resolved, is_new = resolver.resolve_error_pattern(pattern)
        assert is_new

    def test_resolve_methodology_new(self):
        """New methodology with no store should return as new."""
        resolver = EntityResolver(store=None, embedder=None)
        methodology = Methodology(
            id="meth_1",
            situation="When encountering ImportError",
            strategy="Check INSTALLED_APPS and verify imports",
            confidence=0.8,
            success_count=3,
        )
        resolved, is_new = resolver.resolve_methodology(methodology)
        assert is_new
        assert resolved is methodology

    def test_resolve_methodology_merge(self):
        """Matching methodology should be merged."""
        class DummyStore:
            def execute_query(self, query, parameters=None):
                return [{
                    "m": {
                        "id": "meth_existing",
                        "situation": "When encountering ImportError in Django",
                        "strategy": "Check INSTALLED_APPS and verify module paths",
                        "confidence": 0.8,
                        "success_count": 5,
                        "failure_count": 1,
                        "source_fragment_ids": ["f1", "f2"],
                    },
                    "score": 3.0,
                }]

        resolver = EntityResolver(store=DummyStore(), embedder=None, methodology_threshold=0.3)
        methodology = Methodology(
            id="meth_new",
            situation="When encountering ImportError in Django apps",
            strategy="Check INSTALLED_APPS configuration",
            confidence=0.9,
            success_count=2,
            failure_count=0,
            source_fragment_ids=["f3"],
        )
        resolved, is_new = resolver.resolve_methodology(methodology)

        assert not is_new
        assert resolved.id == "meth_existing"
        assert resolved.success_count == 7  # 5 + 2
        assert resolved.failure_count == 1  # 1 + 0
        assert "f3" in resolved.source_fragment_ids

    def test_cosine_similarity(self):
        """Test cosine similarity computation."""
        resolver = EntityResolver(store=None, embedder=None)

        # Identical vectors
        assert resolver._cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0, rel=0.01)

        # Orthogonal
        assert resolver._cosine_similarity([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0, abs=0.01)

        # Empty
        assert resolver._cosine_similarity([], []) == 0.0

    def test_text_similarity(self):
        """Test Jaccard text similarity."""
        resolver = EntityResolver(store=None, embedder=None)

        # Identical
        assert resolver._text_similarity("fix import error", "fix import error") == pytest.approx(1.0)

        # Partial overlap
        sim = resolver._text_similarity("fix import error in django", "fix import error in flask")
        assert 0.5 < sim < 1.0

        # No overlap
        sim = resolver._text_similarity("alpha beta gamma", "delta epsilon zeta")
        assert sim == pytest.approx(0.0)

        # Empty strings
        assert resolver._text_similarity("", "") == pytest.approx(1.0)
