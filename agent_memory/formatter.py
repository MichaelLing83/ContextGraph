"""Structured Context Formatter — XML-tagged output for agent consumption.

Produces token-efficient, structured output following the Zep paper's
chi (structured context) approach:

<PAST_EXPERIENCES>
  <experience confidence="0.85" repo="django/django">
    <error_type>ImportError</error_type>
    <fix_steps>1. Check INSTALLED_APPS 2. Verify migration</fix_steps>
    <outcome>success (3/3 past cases)</outcome>
  </experience>
</PAST_EXPERIENCES>
<WARNINGS>
  <warning>ImportError appeared in 150+ past trajectories</warning>
</WARNINGS>
"""

from typing import List, Optional
from xml.sax.saxutils import escape as xml_escape
import logging

from agent_memory.retriever import EnrichedFragment, RetrievalResult
from agent_memory.models import Methodology, ErrorPattern, Strategy

logger = logging.getLogger(__name__)

# Token budget caps
MAX_STRATEGIES = 5
MAX_EXPERIENCES = 3
MAX_ERROR_PATTERNS = 2
MAX_METHODOLOGIES = 1


class StructuredContextFormatter:
    """Formats retrieval results as structured XML for agent consumption.

    Caps output to stay within token budget:
      - 3 experiences
      - 2 error patterns
      - 1 methodology
    """

    def __init__(
        self,
        max_strategies: int = MAX_STRATEGIES,
        max_experiences: int = MAX_EXPERIENCES,
        max_error_patterns: int = MAX_ERROR_PATTERNS,
        max_methodologies: int = MAX_METHODOLOGIES,
    ):
        self.max_strategies = max_strategies
        self.max_experiences = max_experiences
        self.max_error_patterns = max_error_patterns
        self.max_methodologies = max_methodologies

    def format(
        self,
        result: RetrievalResult,
        error_patterns: Optional[List[ErrorPattern]] = None,
    ) -> str:
        """Format a RetrievalResult into structured XML context.

        Returns a string suitable for injection into agent prompts.
        """
        parts = []

        # Strategies (primary output — concise, high-level rules)
        if result.strategies:
            strategies_xml = self._format_strategies(result.strategies)
            if strategies_xml:
                parts.append(strategies_xml)

        # Past experiences from enriched fragments
        experiences = self._format_experiences(result.enriched_fragments)
        if experiences:
            parts.append(experiences)

        # Methodologies
        if result.methodologies:
            methodologies = self._format_methodologies(result.methodologies)
            if methodologies:
                parts.append(methodologies)

        # Error patterns
        if error_patterns:
            patterns = self._format_error_patterns(error_patterns)
            if patterns:
                parts.append(patterns)

        # Warnings
        if result.warnings:
            warnings = self._format_warnings(result.warnings)
            parts.append(warnings)

        if not parts:
            return ""

        return "\n".join(parts)

    def format_enriched(
        self,
        enriched_fragments: List[EnrichedFragment],
        warnings: Optional[List[str]] = None,
        methodologies: Optional[List[Methodology]] = None,
        error_patterns: Optional[List[ErrorPattern]] = None,
    ) -> str:
        """Format from individual components (alternative to full RetrievalResult)."""
        result = RetrievalResult(
            enriched_fragments=enriched_fragments,
            methodologies=methodologies or [],
            warnings=warnings or [],
        )
        return self.format(result, error_patterns=error_patterns)

    def _format_strategies(self, strategies: List[Strategy]) -> str:
        """Format strategies as <STRATEGIES> XML."""
        if not strategies:
            return ""

        lines = ["<STRATEGIES>"]
        for s in strategies[:self.max_strategies]:
            confidence = f"{s.confidence:.2f}"
            repo = xml_escape(s.source_repo) if s.source_repo else "unknown"
            lines.append(
                f'  <strategy category="{xml_escape(s.category)}" '
                f'confidence="{confidence}" repo="{repo}">'
            )
            lines.append(f"    {xml_escape(s.rule_text)}")
            lines.append("  </strategy>")
        lines.append("</STRATEGIES>")
        return "\n".join(lines)

    def _format_experiences(self, fragments: List[EnrichedFragment]) -> str:
        """Format enriched fragments as <PAST_EXPERIENCES> XML."""
        if not fragments:
            return ""

        lines = ["<PAST_EXPERIENCES>"]

        for ef in fragments[:self.max_experiences]:
            confidence = f"{ef.relevance_score:.2f}" if ef.relevance_score else "0.50"
            repo = xml_escape(ef.repo) if ef.repo else "unknown"

            lines.append(f'  <experience confidence="{confidence}" repo="{repo}">')

            if ef.error_type:
                lines.append(f"    <error_type>{xml_escape(ef.error_type)}</error_type>")

            if ef.action_summary:
                lines.append(f"    <fix_steps>{xml_escape(ef.action_summary)}</fix_steps>")
            elif ef.fragment.description:
                lines.append(
                    f"    <fix_steps>{xml_escape(ef.fragment.description[:200])}</fix_steps>"
                )

            outcome = ef.fragment.outcome or "unknown"
            lines.append(f"    <outcome>{xml_escape(outcome)}</outcome>")

            if ef.trajectory_summary:
                lines.append(
                    f"    <context>{xml_escape(ef.trajectory_summary[:150])}</context>"
                )

            lines.append("  </experience>")

        lines.append("</PAST_EXPERIENCES>")
        return "\n".join(lines)

    def _format_methodologies(self, methodologies: List[Methodology]) -> str:
        """Format methodologies as <METHODOLOGIES> XML."""
        if not methodologies:
            return ""

        lines = ["<METHODOLOGIES>"]

        for m in methodologies[:self.max_methodologies]:
            confidence = f"{m.confidence:.2f}"
            lines.append(f'  <methodology confidence="{confidence}">')
            lines.append(f"    <situation>{xml_escape(m.situation[:200])}</situation>")
            lines.append(f"    <strategy>{xml_escape(m.strategy[:200])}</strategy>")

            total = m.success_count + m.failure_count
            if total > 0:
                lines.append(
                    f"    <track_record>{m.success_count}/{total} successes</track_record>"
                )

            lines.append("  </methodology>")

        lines.append("</METHODOLOGIES>")
        return "\n".join(lines)

    def _format_error_patterns(self, patterns: List[ErrorPattern]) -> str:
        """Format error patterns as <ERROR_PATTERNS> XML."""
        if not patterns:
            return ""

        lines = ["<ERROR_PATTERNS>"]

        for p in patterns[:self.max_error_patterns]:
            lines.append(f'  <pattern type="{xml_escape(p.error_type)}" frequency="{p.frequency}">')
            if p.error_keywords:
                kw_str = ", ".join(p.error_keywords[:5])
                lines.append(f"    <keywords>{xml_escape(kw_str)}</keywords>")
            if p.context:
                lines.append(f"    <context>{xml_escape(p.context[:100])}</context>")
            lines.append("  </pattern>")

        lines.append("</ERROR_PATTERNS>")
        return "\n".join(lines)

    def _format_warnings(self, warnings: List[str]) -> str:
        """Format warnings as <WARNINGS> XML."""
        if not warnings:
            return ""

        lines = ["<WARNINGS>"]
        for w in warnings:
            lines.append(f"  <warning>{xml_escape(w)}</warning>")
        lines.append("</WARNINGS>")
        return "\n".join(lines)
