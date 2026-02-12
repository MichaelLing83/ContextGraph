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
