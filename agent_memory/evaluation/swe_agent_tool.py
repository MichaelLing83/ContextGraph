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
class FragmentInfo:
    """Fragment information for tool output."""

    error_type: str
    resolution: str
    repo: str
    instance_id: str
    outcome: str


@dataclass
class QueryMemoryOutput:
    """Output from query_memory tool."""

    similar_experiences: List[FragmentInfo] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps({
            "similar_experiences": [asdict(f) for f in self.similar_experiences],
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
            QueryMemoryOutput with relevant experiences
        """
        # Create state for query
        state = State(
            tools=["bash", "edit", "view"],
            repo_summary="",
            task_description=input_data.task_description,
            current_error=input_data.current_error,
            phase="fixing",  # simplified — phase filtering removed
        )

        # Query memory — retriever now returns enriched fragments
        context = self.memory.query(state)

        # Use enriched fragments from the retriever if available
        enriched = getattr(context, '_enriched_fragments', None)
        if enriched is None:
            # Fallback: access retriever directly for enriched data
            retrieval_result = self.memory.retriever.retrieve(state)
            enriched = retrieval_result.enriched_fragments

        fragments = []
        for ef in enriched[:5]:
            # Build a useful resolution description
            resolution_parts = []
            if ef.action_summary:
                resolution_parts.append(ef.action_summary)
            if ef.fragment.outcome and ef.fragment.outcome != "unknown":
                resolution_parts.append(f"Outcome: {ef.fragment.outcome}")
            resolution = ". ".join(resolution_parts) if resolution_parts else ef.fragment.description

            fragments.append(FragmentInfo(
                error_type=ef.error_type or ef.fragment.fragment_type,
                resolution=resolution,
                repo=ef.repo,
                instance_id=ef.instance_id,
                outcome=ef.fragment.outcome,
            ))

        return QueryMemoryOutput(
            similar_experiences=fragments,
            warnings=context.warnings,
        )
