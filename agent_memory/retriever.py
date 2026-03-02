"""Memory Retriever - multi-dimensional search with keyword-overlap ranking."""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Set, TYPE_CHECKING
import re
import logging

from agent_memory.models import State, Methodology, Fragment

if TYPE_CHECKING:
    from agent_memory.neo4j_store import Neo4jStore
    from agent_memory.embeddings import EmbeddingClient

logger = logging.getLogger(__name__)

# Common stop words to skip when extracting keywords
_STOP_WORDS: Set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "just", "because", "but", "and", "or", "if",
    "while", "about", "up", "that", "this", "it", "its", "i", "me",
    "my", "we", "our", "you", "your", "he", "she", "they", "them",
    "what", "which", "who", "whom", "these", "those",
    "file", "error", "test", "tests", "line", "code",
}


@dataclass
class EnrichedFragment:
    """Fragment with trajectory context for better tool output."""

    fragment: Fragment
    repo: str = ""
    instance_id: str = ""
    trajectory_summary: str = ""
    error_type: str = ""
    error_keywords: List[str] = field(default_factory=list)
    action_summary: str = ""
    relevance_score: float = 0.0


@dataclass
class RetrievalResult:
    """Result from memory retrieval."""

    methodologies: List[Methodology] = field(default_factory=list)
    similar_fragments: List[Fragment] = field(default_factory=list)
    enriched_fragments: List[EnrichedFragment] = field(default_factory=list)
    error_solutions: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (
            not self.methodologies and
            not self.similar_fragments and
            not self.enriched_fragments and
            not self.error_solutions
        )


class MemoryRetriever:
    """Retrieve relevant memories using multiple dimensions."""

    def __init__(
        self,
        store: Optional["Neo4jStore"],
        embedder: Optional["EmbeddingClient"],
    ):
        self.store = store
        self.embedder = embedder

    def retrieve(self, current_state: State, top_k: int = 5) -> RetrievalResult:
        """
        Retrieve relevant memories for current state.

        Uses two dimensions:
        1. Error-based: Find fragments from successful trajectories that handled
           the same error type, ranked by keyword overlap with the actual error.
        2. Task-based: Match by task description keywords against trajectory summaries.
        """
        result = RetrievalResult()

        if not self.store:
            return result

        # 1. Error-based retrieval (keyword-ranked)
        if current_state.current_error:
            error_results = self.by_error(current_state.current_error)
            result.enriched_fragments.extend(error_results)

        # 2. Task-based retrieval (keyword matching)
        task_results = self.by_task(
            task_description=current_state.task_description,
            repo_summary=current_state.repo_summary,
        )
        result.enriched_fragments.extend(task_results)

        # Dedupe and keep top_k
        result.enriched_fragments = self._dedupe_enriched(
            result.enriched_fragments, top_k
        )

        # Also populate similar_fragments for backward compat
        result.similar_fragments = [
            ef.fragment for ef in result.enriched_fragments
        ]

        # Add warnings for potential failure patterns
        result.warnings = self._get_warnings(current_state)

        return result

    def by_error(self, error_message: str) -> List[EnrichedFragment]:
        """Retrieve fragments from successful trajectories that dealt with the same error type.

        Matches by error_type, then ranks by keyword overlap between the actual
        error message and the ErrorPattern's error_keywords.
        """
        if not self.store:
            return []

        error_type = self._extract_error_type(error_message)
        if not error_type:
            return []

        query = """
        MATCH (t:Trajectory)-[:HAS_FRAGMENT]->(f:Fragment)-[:CAUSED_ERROR]->(e:ErrorPattern)
        WHERE e.error_type = $error_type AND t.success = true
        RETURN f, t.instance_id AS instance_id, t.repo AS repo,
               coalesce(t.summary, '') AS summary,
               e.error_keywords AS keywords, e.error_type AS etype,
               f.action_sequence AS actions
        LIMIT 20
        """

        try:
            results = self.store.execute_query(query, {"error_type": error_type})
        except Exception as e:
            logger.warning("Error-based retrieval failed: %s", e)
            return []

        if not results:
            return []

        # Extract keywords from the query error message for ranking
        query_keywords = self._extract_keywords(error_message)

        enriched = []
        for r in results:
            if "f" not in r:
                continue

            frag = self._dict_to_fragment(r["f"])
            kw_list = r.get("keywords") or []
            if isinstance(kw_list, str):
                kw_list = [k.strip() for k in kw_list.split(",")]

            # Score by keyword overlap
            frag_keywords = {k.lower() for k in kw_list if k}
            overlap = query_keywords & frag_keywords
            score = len(overlap) / max(len(query_keywords), 1)

            actions = r.get("actions") or frag.action_sequence
            enriched.append(EnrichedFragment(
                fragment=frag,
                repo=r.get("repo", ""),
                instance_id=r.get("instance_id", ""),
                trajectory_summary=r.get("summary", ""),
                error_type=r.get("etype", error_type),
                error_keywords=kw_list,
                action_summary=self._summarize_actions(actions),
                relevance_score=score,
            ))

        # Sort by relevance (highest first)
        enriched.sort(key=lambda x: x.relevance_score, reverse=True)
        return enriched[:5]

    def by_task(
        self,
        task_description: str,
        repo_summary: str,
    ) -> List[EnrichedFragment]:
        """Retrieve fragments from trajectories whose summary shares keywords with the task."""
        if not self.store:
            return []

        # Extract meaningful keywords from the task description
        task_keywords = self._extract_keywords(task_description)
        if not task_keywords:
            return []

        # Build a keyword-OR match against trajectory summary
        # Use CONTAINS for each keyword and count matches
        keyword_list = list(task_keywords)[:10]  # cap to avoid huge queries

        # Build WHERE clauses for keyword matching
        where_parts = []
        params: Dict[str, Any] = {}
        for i, kw in enumerate(keyword_list):
            param = f"kw{i}"
            where_parts.append(
                f"CASE WHEN toLower(coalesce(t.summary, '')) CONTAINS ${param} THEN 1 ELSE 0 END"
            )
            params[param] = kw.lower()

        score_expr = " + ".join(where_parts)

        query = f"""
        MATCH (t:Trajectory)-[:HAS_FRAGMENT]->(f:Fragment)
        WHERE t.success = true
        WITH f, t, ({score_expr}) AS kw_score
        WHERE kw_score > 0
        RETURN f, t.instance_id AS instance_id, t.repo AS repo,
               coalesce(t.summary, '') AS summary, kw_score,
               f.action_sequence AS actions
        ORDER BY kw_score DESC
        LIMIT 10
        """

        try:
            results = self.store.execute_query(query, params)
        except Exception as e:
            logger.warning("Task-based retrieval failed: %s", e)
            return []

        enriched = []
        for r in results:
            if "f" not in r:
                continue

            frag = self._dict_to_fragment(r["f"])
            kw_score = r.get("kw_score", 0)
            actions = r.get("actions") or frag.action_sequence

            enriched.append(EnrichedFragment(
                fragment=frag,
                repo=r.get("repo", ""),
                instance_id=r.get("instance_id", ""),
                trajectory_summary=r.get("summary", ""),
                action_summary=self._summarize_actions(actions),
                relevance_score=kw_score / max(len(keyword_list), 1),
            ))

        return enriched

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_error_type(self, error_message: str) -> Optional[str]:
        """Extract error type from message."""
        match = re.search(r'(\w+Error|\w+Exception)', error_message)
        return match.group(1) if match else None

    def _extract_keywords(self, text: str) -> Set[str]:
        """Extract meaningful keywords from text, skipping stop words."""
        if not text:
            return set()
        # Tokenize: split on non-alphanumeric, keep words >= 3 chars
        words = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', text.lower())
        return {w for w in words if len(w) >= 3 and w not in _STOP_WORDS}

    def _summarize_actions(self, actions: Any) -> str:
        """Summarize action_sequence into a human-readable string.

        Extracts edit operations and file paths, condenses repeated actions.
        """
        if not actions:
            return ""

        if isinstance(actions, str):
            # Try to parse as list-like string
            actions = [a.strip() for a in actions.split(",")]

        if not isinstance(actions, list):
            return str(actions)

        edits = []
        other_actions = []
        bash_count = 0

        for action in actions:
            action_str = str(action).strip()
            if not action_str:
                continue

            # Extract edit actions with file info
            edit_match = re.match(
                r'edit\s+(\S+?)(?::(\d+))?(?::(\d+))?$', action_str, re.IGNORECASE
            )
            if edit_match:
                filepath = edit_match.group(1)
                # Get just the filename
                filename = filepath.rsplit("/", 1)[-1] if "/" in filepath else filepath
                edits.append(filename)
                continue

            if action_str.lower() in ("bash", "bash_command"):
                bash_count += 1
                continue

            other_actions.append(action_str)

        parts = []
        if edits:
            # Dedupe while preserving order
            seen = set()
            unique_edits = []
            for e in edits:
                if e not in seen:
                    seen.add(e)
                    unique_edits.append(e)
            parts.append(f"Edited: {', '.join(unique_edits[:5])}")
        if bash_count:
            parts.append(f"{bash_count} bash commands")
        if other_actions:
            parts.append(f"Other: {', '.join(other_actions[:3])}")

        return "; ".join(parts) if parts else f"{len(actions)} actions"

    def _dedupe_enriched(
        self,
        fragments: List[EnrichedFragment],
        top_k: int,
    ) -> List[EnrichedFragment]:
        """Deduplicate enriched fragments by fragment id, keeping highest score."""
        seen_ids: Dict[str, EnrichedFragment] = {}
        for ef in fragments:
            fid = ef.fragment.id
            if fid not in seen_ids or ef.relevance_score > seen_ids[fid].relevance_score:
                seen_ids[fid] = ef
        # Sort by score descending
        result = sorted(seen_ids.values(), key=lambda x: x.relevance_score, reverse=True)
        return result[:top_k]

    def _dedupe_fragments(
        self,
        fragments: List[Fragment],
        top_k: int,
    ) -> List[Fragment]:
        """Deduplicate fragments."""
        seen_ids: set = set()
        unique = []
        for f in fragments:
            if f.id not in seen_ids:
                seen_ids.add(f.id)
                unique.append(f)
        return unique[:top_k]

    def _get_warnings(self, state: State) -> List[str]:
        """Get warnings for potential failure patterns."""
        warnings = []

        if state.current_error:
            error_type = self._extract_error_type(state.current_error)
            if error_type and self.store:
                try:
                    results = self.store.execute_query(
                        "MATCH (e:ErrorPattern {error_type: $et}) RETURN e.frequency AS freq",
                        {"et": error_type},
                    )
                    for r in results:
                        freq = r.get("freq", 0)
                        if freq and freq > 100:
                            warnings.append(
                                f"{error_type} appeared in {freq} past trajectories. "
                                "Consider checking import paths and argument types carefully."
                            )
                except Exception:
                    pass

        return warnings

    def _dict_to_methodology(self, d: Dict[str, Any]) -> Methodology:
        """Convert dict to Methodology."""
        return Methodology.from_dict(d)

    def _dict_to_fragment(self, d: Dict[str, Any]) -> Fragment:
        """Convert dict to Fragment."""
        return Fragment.from_dict(d)
