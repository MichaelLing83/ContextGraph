"""Core data models for Agent Memory."""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime
import re


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
    last_action_type: str = "unknown"  # Last action category used by loop detection
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
        # Match common Python error patterns
        match = re.search(r'(\w+Error|\w+Exception|FAIL|ERROR)', error_msg)
        return match.group(1) if match else "Unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tools": self.tools,
            "repo_summary": self.repo_summary,
            "task_description": self.task_description,
            "current_error": self.current_error,
            "last_action_type": self.last_action_type,
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
            last_action_type=d.get("last_action_type", "unknown"),
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
class Strategy:
    """LLM-extracted reusable strategy from a trajectory."""

    id: str                    # "strat_{uuid12}"
    rule_text: str             # The strategy text (one sentence)
    category: str              # error_handling | debugging | testing | code_navigation | ...
    source_trajectory_id: str  # Which trajectory it came from
    source_repo: str           # e.g., "django/django"
    confidence: float = 0.8    # Initial confidence
    embedding: Optional[List[float]] = None

    VALID_CATEGORIES = frozenset([
        "error_handling",
        "debugging",
        "testing",
        "code_navigation",
        "dependency",
        "configuration",
    ])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "rule_text": self.rule_text,
            "category": self.category,
            "source_trajectory_id": self.source_trajectory_id,
            "source_repo": self.source_repo,
            "confidence": self.confidence,
            "embedding": self.embedding,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Strategy":
        return cls(
            id=d["id"],
            rule_text=d["rule_text"],
            category=d.get("category", "debugging"),
            source_trajectory_id=d.get("source_trajectory_id", ""),
            source_repo=d.get("source_repo", ""),
            confidence=d.get("confidence", 0.8),
            embedding=d.get("embedding"),
        )


@dataclass
class TemporalEdge:
    """Temporal validity metadata for graph edges (Dual Timeline model).

    t_created: When the edge was first observed
    t_valid:   When the edge became valid (usually same as t_created)
    t_invalid: When the edge was invalidated (None if still valid)
    t_expired: When the edge was fully expired/archived (None if not expired)
    """

    t_created: Optional[datetime] = None
    t_valid: Optional[datetime] = None
    t_invalid: Optional[datetime] = None
    t_expired: Optional[datetime] = None

    def is_valid(self) -> bool:
        """Check if this edge is currently valid (not invalidated)."""
        return self.t_invalid is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "t_created": self.t_created.isoformat() if self.t_created else None,
            "t_valid": self.t_valid.isoformat() if self.t_valid else None,
            "t_invalid": self.t_invalid.isoformat() if self.t_invalid else None,
            "t_expired": self.t_expired.isoformat() if self.t_expired else None,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TemporalEdge":
        def parse_dt(val):
            if isinstance(val, str):
                return datetime.fromisoformat(val)
            return val

        return cls(
            t_created=parse_dt(d.get("t_created")),
            t_valid=parse_dt(d.get("t_valid")),
            t_invalid=parse_dt(d.get("t_invalid")),
            t_expired=parse_dt(d.get("t_expired")),
        )


@dataclass
class Community:
    """Community node — a group of related fragments detected via label propagation."""

    id: str
    community_id: int
    summary: str
    node_count: int = 0
    error_types: List[str] = field(default_factory=list)
    embedding: Optional[List[float]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "community_id": self.community_id,
            "summary": self.summary,
            "node_count": self.node_count,
            "error_types": self.error_types,
            "embedding": self.embedding,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Community":
        return cls(
            id=d["id"],
            community_id=d["community_id"],
            summary=d["summary"],
            node_count=d.get("node_count", 0),
            error_types=d.get("error_types", []),
            embedding=d.get("embedding"),
        )


@dataclass
class CanonicalRule:
    """Deduplicated rule from clustering similar strategies.

    Represents the centroid of a cluster of Strategy nodes.
    Connected to Strategies via MERGED_INTO and to ErrorPatterns via ADDRESSES_ERROR.
    Used as the retrieval target for HippoRAG-style PPR search.
    """

    id: str                    # "rule_{uuid12}"
    rule_text: str             # Representative rule text (cluster centroid)
    category: str              # error_handling | debugging | testing | ...
    prefix: str                # shr, psw, cms, verify
    section: str               # Playbook section name
    member_count: int          # How many strategies were merged
    avg_confidence: float
    source_repos: List[str] = field(default_factory=list)
    error_types: List[str] = field(default_factory=list)
    embedding: Optional[List[float]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "rule_text": self.rule_text,
            "category": self.category,
            "prefix": self.prefix,
            "section": self.section,
            "member_count": self.member_count,
            "avg_confidence": self.avg_confidence,
            "source_repos": self.source_repos,
            "error_types": self.error_types,
            "embedding": self.embedding,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CanonicalRule":
        return cls(
            id=d["id"],
            rule_text=d["rule_text"],
            category=d.get("category", "debugging"),
            prefix=d.get("prefix", "misc"),
            section=d.get("section", "OTHERS"),
            member_count=d.get("member_count", 1),
            avg_confidence=d.get("avg_confidence", 0.8),
            source_repos=d.get("source_repos", []),
            error_types=d.get("error_types", []),
            embedding=d.get("embedding"),
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


# Playbook sections in canonical display order
PLAYBOOK_SECTIONS = {
    "shr": "STRATEGIES AND HARD RULES",
    "api": "APIs TO USE FOR SPECIFIC INFORMATION",
    "snippet": "USEFUL CODE SNIPPETS AND TEMPLATES",
    "cms": "COMMON MISTAKES AND CORRECT STRATEGIES",
    "psw": "PROBLEM-SOLVING HEURISTICS AND WORKFLOWS",
    "verify": "VERIFICATION CHECKLIST",
    "pitfall": "TROUBLESHOOTING AND PITFALLS",
    "misc": "OTHERS",
}


@dataclass
class PlaybookEntry:
    """A single rule in a playbook, identified by prefix-NNNNN id."""

    id: str                    # "shr-00001"
    prefix: str                # "shr"
    section: str               # "STRATEGIES AND HARD RULES"
    text: str                  # The rule text
    embedding: Optional[List[float]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "prefix": self.prefix,
            "section": self.section,
            "text": self.text,
            "embedding": self.embedding,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PlaybookEntry":
        return cls(
            id=d["id"],
            prefix=d["prefix"],
            section=d.get("section", "OTHERS"),
            text=d["text"],
            embedding=d.get("embedding"),
        )
