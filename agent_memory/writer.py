"""Memory Writer - incremental trajectory ingestion."""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple, TYPE_CHECKING
import re
import uuid
import logging

from agent_memory.models import Trajectory, Fragment, State, ErrorPattern

if TYPE_CHECKING:
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
        store: Optional["Neo4jStore"],
        embedder: Optional["EmbeddingClient"],
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

    def _has_error(self, observation: str) -> bool:
        """Check if observation indicates an error."""
        error_patterns = [
            r'\bError\b',
            r'\bException\b',
            r'\bFailed\b',
            r'\bfailed\b',
            r'\bTraceback\b',
            r'\bERROR\b',
        ]
        return any(re.search(p, observation) for p in error_patterns)

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
                id=f"err_{uuid.uuid4().hex[:12]}",
                error_type=error_type,
                error_keywords=unique_keywords,
                context="trajectory",
                frequency=data["count"],
            ))

        return patterns

    def _extract_error_type(self, text: str) -> Optional[str]:
        """Extract error type from text."""
        match = re.search(r'(\w+Error|\w+Exception)', text)
        return match.group(1) if match else None

    def _extract_keywords(self, text: str, top_n: int = 5) -> List[str]:
        """Extract top keywords from text."""
        stop_words = {"the", "a", "an", "in", "on", "at", "to", "for", "of", "is", "are", "was", "were"}
        words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', text.lower())
        keywords = [w for w in words if w not in stop_words and len(w) > 2]

        # Return first N unique
        seen = set()
        result = []
        for w in keywords:
            if w not in seen:
                seen.add(w)
                result.append(w)
                if len(result) >= top_n:
                    break
        return result

    def _generate_summary(self, raw: RawTrajectory) -> str:
        """Generate a summary of the trajectory."""
        outcome = "succeeded" if raw.success else "failed"
        actions = [s.get("action", "") for s in raw.steps]
        unique_actions = list(dict.fromkeys(actions))[:5]

        summary = f"Trajectory {outcome} after {raw.total_steps} steps. "
        summary += f"Actions: {', '.join(unique_actions)}. "

        if raw.problem_statement:
            summary += f"Task: {raw.problem_statement[:100]}"

        return summary

    def _infer_task_type(self, raw: RawTrajectory) -> str:
        """Infer task type from problem statement and steps."""
        text = raw.problem_statement.lower()

        if any(kw in text for kw in ["fix", "bug", "error", "issue", "crash"]):
            return "bug_fix"
        elif any(kw in text for kw in ["add", "feature", "implement", "create", "new"]):
            return "feature"
        elif any(kw in text for kw in ["refactor", "clean", "improve", "optimize"]):
            return "refactor"
        else:
            return "bug_fix"  # Default
