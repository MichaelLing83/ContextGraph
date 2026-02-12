"""Loop detection based on error consistency."""

from dataclasses import dataclass, field
from typing import List, Optional, TYPE_CHECKING
import re

if TYPE_CHECKING:
    from agent_memory.models import State


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
        action_type = state.last_action_type or "unknown"

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
