"""SWE-agent tool implementation for querying agent memory."""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, TYPE_CHECKING
import json

from agent_memory.models import State

if TYPE_CHECKING:
    from agent_memory import AgentMemory


@dataclass
class QueryMemoryInput:
    """Input for query_memory tool."""

    current_error: str
    task_description: str
    phase: str  # exploring | understanding | locating | fixing | verifying


@dataclass
class MethodologyInfo:
    """Methodology information for tool output."""

    situation: str
    strategy: str
    confidence: float


@dataclass
class FragmentInfo:
    """Fragment information for tool output."""

    error_type: str
    resolution: str
    from_trajectory: str


@dataclass
class QueryMemoryOutput:
    """Output from query_memory tool."""

    methodologies: List[MethodologyInfo] = field(default_factory=list)
    similar_fragments: List[FragmentInfo] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps({
            "methodologies": [asdict(m) for m in self.methodologies],
            "similar_fragments": [asdict(f) for f in self.similar_fragments],
            "warnings": self.warnings,
        }, indent=2)


class QueryMemoryTool:
    """
    SWE-agent tool for querying agent memory.

    This tool can be registered with SWE-agent to provide
    memory-augmented capabilities during problem solving.
    """

    def __init__(self, memory: "AgentMemory"):
        self.memory = memory
        self.name = "query_memory"
        self.description = (
            "Query agent memory for similar past experiences and methodologies. "
            "Use this to get guidance based on previously solved problems."
        )

    def get_schema(self) -> Dict[str, Any]:
        """Return JSON schema for this tool."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "current_error": {
                        "type": "string",
                        "description": "The error message currently being debugged",
                    },
                    "task_description": {
                        "type": "string",
                        "description": "Brief description of the current task",
                    },
                    "phase": {
                        "type": "string",
                        "enum": [
                            "exploring",
                            "understanding",
                            "locating",
                            "fixing",
                            "verifying",
                            "testing",
                        ],
                        "description": "Current phase of problem solving",
                    },
                },
                "required": ["current_error", "task_description", "phase"],
            },
        }

    def invoke(self, input_data: QueryMemoryInput) -> QueryMemoryOutput:
        """
        Invoke the tool with given input.

        Args:
            input_data: QueryMemoryInput with current context

        Returns:
            QueryMemoryOutput with relevant methodologies and fragments
        """
        # Map phase to valid State phase
        phase_map = {
            "exploring": "understanding",
            "understanding": "understanding",
            "locating": "locating",
            "fixing": "fixing",
            "verifying": "testing",
            "testing": "testing",
        }
        mapped_phase = phase_map.get(input_data.phase, "fixing")

        # Create state for query
        state = State(
            tools=["bash", "edit", "view"],
            repo_summary="",
            task_description=input_data.task_description,
            current_error=input_data.current_error,
            phase=mapped_phase,
        )

        # Query memory
        context = self.memory.query(state)

        # Convert to output format
        methodologies = [
            MethodologyInfo(
                situation=m.situation,
                strategy=m.strategy,
                confidence=m.confidence,
            )
            for m in context.methodologies[:5]  # Limit to top 5
        ]

        fragments = [
            FragmentInfo(
                error_type=f.fragment_type,
                resolution=f.description,
                from_trajectory=f.id,
            )
            for f in context.similar_fragments[:5]  # Limit to top 5
        ]

        return QueryMemoryOutput(
            methodologies=methodologies,
            similar_fragments=fragments,
            warnings=context.warnings,
        )
