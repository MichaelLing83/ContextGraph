"""LLM-based strategy extraction from trajectory data.

Uses an LLM to analyze agent trajectories and extract reusable
debugging/coding strategies that can be applied to new problems.
"""

import re
import uuid
import logging
from typing import List, Dict, Any, Optional

from agent_memory.models import Strategy, PlaybookEntry, PLAYBOOK_SECTIONS

logger = logging.getLogger(__name__)

# Prompt template for strategy extraction
_EXTRACTION_PROMPT = """You are analyzing a coding agent's trajectory to extract reusable debugging strategies.

Repository: {repo}
Task: {problem_statement}
Outcome: {outcome} after {n_steps} steps
Key errors encountered: {error_types}
Key actions: {action_summary}

Extract 3-5 concise, reusable rules from this trajectory. Each rule should be:
- Abstract (not tied to specific file names or variables)
- Actionable (tells an agent what to do or avoid)
- One sentence

Format each rule on its own line as: [category] Rule text
Categories: error_handling, debugging, testing, code_navigation, dependency, configuration

Rules:"""

# Valid categories for parsing
_VALID_CATEGORIES = {
    "error_handling", "debugging", "testing",
    "code_navigation", "dependency", "configuration",
}


class StrategyExtractor:
    """Extract reusable strategies from trajectories using an LLM."""

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
    ):
        """Initialize with an OpenAI-compatible API endpoint.

        Args:
            api_base: Base URL for an OpenAI-compatible API (e.g. litellm proxy).
            api_key: API key for the endpoint.
            model: Model name recognized by the endpoint.
        """
        from openai import OpenAI
        self.client = OpenAI(base_url=api_base, api_key=api_key)
        self.model = model

    def extract(self, trajectory_data: Dict[str, Any]) -> List[Strategy]:
        """Extract strategies from a single trajectory's data.

        Args:
            trajectory_data: Dict with keys:
                - trajectory_id: str
                - repo: str
                - problem_statement: str (truncated)
                - success: bool
                - total_steps: int
                - error_types: List[str]
                - action_summary: str
                - fragments: List[dict] (optional, for richer context)

        Returns:
            List of Strategy objects extracted from this trajectory.
        """
        prompt = self._build_prompt(trajectory_data)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.3,
            )
            content = response.choices[0].message.content or ""
        except Exception as e:
            logger.warning("LLM call failed for trajectory %s: %s",
                           trajectory_data.get("trajectory_id", "?"), e)
            return []

        return self._parse_response(content, trajectory_data)

    def build_prompt(self, trajectory_data: Dict[str, Any]) -> str:
        """Build the LLM prompt for a trajectory (public API for dry-run/debugging)."""
        return self._build_prompt(trajectory_data)

    def _build_prompt(self, data: Dict[str, Any]) -> str:
        """Build LLM prompt from trajectory data."""
        repo = data.get("repo", "unknown")
        problem = data.get("problem_statement", "")[:300]
        outcome = "succeeded" if data.get("success") else "failed"
        n_steps = data.get("total_steps", 0)
        error_types = ", ".join(data.get("error_types", [])) or "none"

        # Build action summary from fragments if available
        action_summary = data.get("action_summary", "")
        if not action_summary and data.get("fragments"):
            parts = []
            for frag in data["fragments"][:5]:
                desc = frag.get("description", "")
                if desc:
                    parts.append(desc[:100])
            action_summary = "; ".join(parts)

        action_summary = action_summary[:500] or "no details available"

        return _EXTRACTION_PROMPT.format(
            repo=repo,
            problem_statement=problem or "no description available",
            outcome=outcome,
            n_steps=n_steps,
            error_types=error_types,
            action_summary=action_summary,
        )

    def _parse_response(
        self, content: str, data: Dict[str, Any]
    ) -> List[Strategy]:
        """Parse LLM output into Strategy objects.

        Expected format per line: [category] Rule text
        """
        strategies = []
        trajectory_id = data.get("trajectory_id", "")
        repo = data.get("repo", "")

        for line in content.strip().splitlines():
            line = line.strip()
            if not line:
                continue

            # Strip leading bullet/number markers
            line = re.sub(r"^[\d]+[.)]\s*", "", line)
            line = re.sub(r"^[-*]\s*", "", line)

            # Match [category] Rule text
            match = re.match(r"\[(\w+)\]\s+(.+)", line)
            if not match:
                continue

            category = match.group(1).lower()
            rule_text = match.group(2).strip()

            # Validate category, default to "debugging" if unknown
            if category not in _VALID_CATEGORIES:
                category = "debugging"

            # Skip very short or generic rules
            if len(rule_text) < 15:
                continue

            strategy_id = f"strat_{uuid.uuid4().hex[:12]}"
            strategies.append(Strategy(
                id=strategy_id,
                rule_text=rule_text,
                category=category,
                source_trajectory_id=trajectory_id,
                source_repo=repo,
                confidence=0.8 if data.get("success") else 0.5,
            ))

        return strategies


# Mapping from Strategy.category to playbook prefix
CATEGORY_TO_PREFIX = {
    "error_handling": "shr",
    "debugging": "psw",
    "testing": "verify",
    "code_navigation": "psw",
    "dependency": "cms",
    "configuration": "cms",
}


def strategies_to_playbook_entries(
    strategies: List[Strategy],
    start_index: int = 1,
) -> List[PlaybookEntry]:
    """Convert Strategy objects to PlaybookEntry objects with playbook-style ids.

    Args:
        strategies: List of Strategy objects to convert.
        start_index: Starting index for numbering within each prefix group.

    Returns:
        List of PlaybookEntry objects with proper ids and sections.
    """
    # Track next index per prefix
    counters: Dict[str, int] = {}
    entries = []

    for strategy in strategies:
        prefix = CATEGORY_TO_PREFIX.get(strategy.category, "misc")
        section = PLAYBOOK_SECTIONS.get(prefix, "OTHERS")

        if prefix not in counters:
            counters[prefix] = start_index
        idx = counters[prefix]
        counters[prefix] += 1

        entry_id = f"{prefix}-{idx:05d}"
        entries.append(PlaybookEntry(
            id=entry_id,
            prefix=prefix,
            section=section,
            text=strategy.rule_text,
            embedding=strategy.embedding,
        ))

    return entries
