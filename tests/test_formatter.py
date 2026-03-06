"""Tests for StructuredContextFormatter."""

import pytest

from agent_memory.formatter import StructuredContextFormatter
from agent_memory.retriever import EnrichedFragment, RetrievalResult
from agent_memory.models import Fragment, Methodology, ErrorPattern, Strategy, ProblemSummary


def _make_fragment(fid="f1", desc="Fixed import issue", outcome="success"):
    return Fragment(
        id=fid, step_range=(0, 5), fragment_type="error_recovery",
        description=desc, action_sequence=["edit", "test"], outcome=outcome,
    )


def _make_enriched(fid="f1", repo="django/django", error_type="ImportError",
                   score=0.85, outcome="success"):
    return EnrichedFragment(
        fragment=_make_fragment(fid, outcome=outcome),
        repo=repo,
        instance_id="test__test-123",
        trajectory_summary="Fixed import in Django models",
        error_type=error_type,
        action_summary="Edited: models.py, settings.py",
        relevance_score=score,
    )


class TestStructuredContextFormatter:
    def test_format_experiences(self):
        """Test experience formatting produces valid XML structure."""
        formatter = StructuredContextFormatter()
        result = RetrievalResult(
            enriched_fragments=[_make_enriched()],
        )

        output = formatter.format(result)

        assert "<PAST_EXPERIENCES>" in output
        assert "</PAST_EXPERIENCES>" in output
        assert 'confidence="0.85"' in output
        assert 'repo="django/django"' in output
        assert "<error_type>ImportError</error_type>" in output
        assert "<outcome>success</outcome>" in output

    def test_format_empty_results(self):
        """Empty results should produce empty string."""
        formatter = StructuredContextFormatter()
        result = RetrievalResult()
        assert formatter.format(result) == ""

    def test_format_warnings(self):
        """Warnings should be formatted as XML."""
        formatter = StructuredContextFormatter()
        result = RetrievalResult(
            warnings=["ImportError appeared in 150 trajectories"],
        )

        output = formatter.format(result)

        assert "<WARNINGS>" in output
        assert "</WARNINGS>" in output
        assert "ImportError appeared in 150 trajectories" in output

    def test_format_methodologies(self):
        """Methodologies should be formatted as XML."""
        formatter = StructuredContextFormatter()
        meth = Methodology(
            id="m1",
            situation="When encountering ImportError in Django",
            strategy="Check INSTALLED_APPS and verify module paths",
            confidence=0.85,
            success_count=10,
            failure_count=2,
        )
        result = RetrievalResult(methodologies=[meth])

        output = formatter.format(result)

        assert "<METHODOLOGIES>" in output
        assert "</METHODOLOGIES>" in output
        assert 'confidence="0.85"' in output
        assert "INSTALLED_APPS" in output
        assert "10/12 successes" in output

    def test_format_error_patterns(self):
        """Error patterns should be formatted as XML."""
        formatter = StructuredContextFormatter()
        pattern = ErrorPattern(
            id="err_1", error_type="ImportError",
            error_keywords=["import", "module", "django"],
            context="web framework", frequency=42,
        )
        result = RetrievalResult()

        output = formatter.format(result, error_patterns=[pattern])

        assert "<ERROR_PATTERNS>" in output
        assert 'type="ImportError"' in output
        assert 'frequency="42"' in output

    def test_max_experiences_cap(self):
        """Should cap experiences to max_experiences."""
        formatter = StructuredContextFormatter(max_experiences=2)
        fragments = [_make_enriched(f"f{i}") for i in range(5)]
        result = RetrievalResult(enriched_fragments=fragments)

        output = formatter.format(result)

        # Count <experience> tags
        assert output.count("<experience ") == 2

    def test_special_char_escaping(self):
        """XML special characters should be escaped."""
        formatter = StructuredContextFormatter()
        ef = _make_enriched()
        ef.trajectory_summary = 'Error: x < 5 && y > 3'

        result = RetrievalResult(enriched_fragments=[ef])
        output = formatter.format(result)

        assert "&lt;" in output
        assert "&amp;" in output
        # Should not break XML structure
        assert "<PAST_EXPERIENCES>" in output
        assert "</PAST_EXPERIENCES>" in output

    def test_format_enriched_convenience(self):
        """Test format_enriched convenience method."""
        formatter = StructuredContextFormatter()

        output = formatter.format_enriched(
            enriched_fragments=[_make_enriched()],
            warnings=["Test warning"],
        )

        assert "<PAST_EXPERIENCES>" in output
        assert "<WARNINGS>" in output

    def test_format_similar_problems(self):
        """Test similar problems formatting produces valid XML structure."""
        formatter = StructuredContextFormatter()
        ps = ProblemSummary(
            id="ps_test1",
            summary_text="Fixed a TypeError in Django's prefetch_related by correcting argument types.",
            source_trajectory_id="traj_1",
            source_repo="django/django",
            success=True,
            total_steps=12,
        )
        result = RetrievalResult(problem_summaries=[ps])

        output = formatter.format(result)

        assert "<SIMILAR_PROBLEMS>" in output
        assert "</SIMILAR_PROBLEMS>" in output
        assert 'repo="django/django"' in output
        assert 'outcome="success"' in output
        assert 'steps="12"' in output
        assert "prefetch_related" in output

    def test_format_similar_problems_failure(self):
        """Test failed problem summary renders outcome='failure'."""
        formatter = StructuredContextFormatter()
        ps = ProblemSummary(
            id="ps_test2",
            summary_text="Agent failed to resolve the import issue after 50 steps.",
            source_trajectory_id="traj_2",
            source_repo="flask/flask",
            success=False,
            total_steps=50,
        )
        result = RetrievalResult(problem_summaries=[ps])

        output = formatter.format(result)

        assert 'outcome="failure"' in output
        assert 'steps="50"' in output

    def test_max_similar_problems_cap(self):
        """Should cap similar problems to max_similar_problems."""
        formatter = StructuredContextFormatter(max_similar_problems=2)
        summaries = [
            ProblemSummary(
                id=f"ps_{i}", summary_text=f"Summary {i}",
                source_trajectory_id=f"traj_{i}", source_repo="test/repo",
                success=True, total_steps=10,
            )
            for i in range(5)
        ]
        result = RetrievalResult(problem_summaries=summaries)

        output = formatter.format(result)

        assert output.count("<problem ") == 2

    def test_similar_problems_xml_escaping(self):
        """XML special characters in summary_text should be escaped."""
        formatter = StructuredContextFormatter()
        ps = ProblemSummary(
            id="ps_esc",
            summary_text='Error: x < 5 && repo="test"',
            source_trajectory_id="traj_esc",
            source_repo="test/repo",
            success=True,
            total_steps=5,
        )
        result = RetrievalResult(problem_summaries=[ps])

        output = formatter.format(result)

        assert "&lt;" in output
        assert "&amp;" in output
        assert "<SIMILAR_PROBLEMS>" in output
        assert "</SIMILAR_PROBLEMS>" in output

    def test_similar_problems_ordered_before_experiences(self):
        """SIMILAR_PROBLEMS should appear between STRATEGIES and PAST_EXPERIENCES."""
        formatter = StructuredContextFormatter()
        ps = ProblemSummary(
            id="ps_order", summary_text="Test summary",
            source_trajectory_id="traj_o", source_repo="r/r",
            success=True, total_steps=5,
        )
        result = RetrievalResult(
            enriched_fragments=[_make_enriched()],
            problem_summaries=[ps],
        )

        output = formatter.format(result)

        sp_pos = output.index("<SIMILAR_PROBLEMS>")
        pe_pos = output.index("<PAST_EXPERIENCES>")
        assert sp_pos < pe_pos

    def test_full_section_ordering(self):
        """STRATEGIES < SIMILAR_PROBLEMS < PAST_EXPERIENCES when all present."""
        formatter = StructuredContextFormatter()
        strat = Strategy(
            id="s1", rule_text="Check imports carefully",
            category="debugging", source_trajectory_id="t1",
            source_repo="django/django", confidence=0.9,
        )
        ps = ProblemSummary(
            id="ps_ord", summary_text="Test summary",
            source_trajectory_id="traj_o", source_repo="r/r",
            success=True, total_steps=5,
        )
        result = RetrievalResult(
            strategies=[strat],
            problem_summaries=[ps],
            enriched_fragments=[_make_enriched()],
        )

        output = formatter.format(result)

        st_pos = output.index("<STRATEGIES>")
        sp_pos = output.index("<SIMILAR_PROBLEMS>")
        pe_pos = output.index("<PAST_EXPERIENCES>")
        assert st_pos < sp_pos < pe_pos
