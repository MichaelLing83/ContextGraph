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
class StrategyInfo:
    """Strategy information for tool output."""

    rule_text: str
    category: str
    repo: str
    confidence: float


@dataclass
class ProblemSummaryInfo:
    """Problem summary information for tool output."""

    summary: str
    repo: str
    success: bool
    total_steps: int


@dataclass
class QueryMemoryOutput:
    """Output from query_memory tool."""

    similar_experiences: List[FragmentInfo] = field(default_factory=list)
    strategies: List[StrategyInfo] = field(default_factory=list)
    similar_problems: List[ProblemSummaryInfo] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    playbook_text: str = ""

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps({
            "similar_experiences": [asdict(f) for f in self.similar_experiences],
            "strategies": [asdict(s) for s in self.strategies],
            "similar_problems": [asdict(p) for p in self.similar_problems],
            "warnings": self.warnings,
            "playbook_text": self.playbook_text,
        }, indent=2)

    def to_structured(self) -> str:
        """Serialize to structured XML format (Zep-style).

        Token-efficient format that's easier for LLMs to parse.
        Prepends playbook text if available.
        """
        from agent_memory.formatter import StructuredContextFormatter
        from agent_memory.retriever import EnrichedFragment, RetrievalResult
        from agent_memory.models import Fragment, Strategy, ProblemSummary

        # Convert FragmentInfo back to EnrichedFragment for the formatter
        enriched = []
        for i, fi in enumerate(self.similar_experiences):
            frag = Fragment(
                id=f"output_frag_{i}",
                step_range=(0, 0),
                fragment_type="error_recovery",
                description=fi.resolution,
                action_sequence=[],
                outcome=fi.outcome,
            )
            enriched.append(EnrichedFragment(
                fragment=frag,
                repo=fi.repo,
                instance_id=fi.instance_id,
                error_type=fi.error_type,
                action_summary=fi.resolution,
                relevance_score=max(0.0, 0.8 - (i * 0.1)),
            ))

        # Convert StrategyInfo to Strategy models
        strategy_models = [
            Strategy(
                id=f"output_strat_{i}",
                rule_text=si.rule_text,
                category=si.category,
                source_trajectory_id="",
                source_repo=si.repo,
                confidence=si.confidence,
            )
            for i, si in enumerate(self.strategies)
        ]

        # Convert ProblemSummaryInfo to ProblemSummary models
        ps_models = [
            ProblemSummary(
                id=f"output_ps_{i}",
                summary_text=pi.summary,
                source_trajectory_id="",
                source_repo=pi.repo,
                success=pi.success,
                total_steps=pi.total_steps,
            )
            for i, pi in enumerate(self.similar_problems)
        ]

        result = RetrievalResult(
            enriched_fragments=enriched,
            strategies=strategy_models,
            problem_summaries=ps_models,
            warnings=self.warnings,
        )
        formatter = StructuredContextFormatter()
        xml_text = formatter.format(result)

        if self.playbook_text:
            return self.playbook_text + "\n\n" + xml_text
        return xml_text


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

        # Get playbook context
        playbook_text = self.memory.query_playbook(state)

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

        # Convert strategies from memory context
        strategy_infos = [
            StrategyInfo(
                rule_text=s.rule_text,
                category=s.category,
                repo=s.source_repo,
                confidence=s.confidence,
            )
            for s in context.strategies[:5]
        ]

        # Convert problem summaries from memory context
        problem_infos = [
            ProblemSummaryInfo(
                summary=ps.summary_text,
                repo=ps.source_repo,
                success=ps.success,
                total_steps=ps.total_steps,
            )
            for ps in context.problem_summaries[:5]
        ]

        return QueryMemoryOutput(
            similar_experiences=fragments,
            strategies=strategy_infos,
            similar_problems=problem_infos,
            warnings=context.warnings,
            playbook_text=playbook_text,
        )
