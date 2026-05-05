#!/usr/bin/env python3
"""SWE-Bench-CL adapter for ContextGraph experiments.

Loads the SWE-Bench-CL curriculum (273 tasks across 8 repo sequences) and provides
the framework for running continual learning experiments comparing:
  - no_memory: Baseline with no memory (control)
  - faiss: Their FAISS-based semantic memory (sentence-transformers/all-MiniLM-L6-v2)
  - contextgraph: Our Neo4j graph memory (PlaybookRetriever + AgentMemory.learn())

This adapter does NOT run the actual SWE-agent (too expensive for iteration). Instead:
  1. Loads the curriculum and processes tasks in sequence order
  2. Manages the memory lifecycle (store after each task, retrieve before each task)
  3. Outputs a results JSON that the metrics calculator can analyze
  4. Can be integrated with run_real_swe_experiment.py for actual agent runs

Usage:
    # Dry run (no agent, just show tasks and memory operations):
    uv run python scripts/baselines/swebenchcl_adapter.py run --mode contextgraph --dry-run

    # Process results from a completed experiment run:
    uv run python scripts/baselines/swebenchcl_adapter.py run --mode faiss --results-dir results/swebenchcl/

    # List sequences and task counts:
    uv run python scripts/baselines/swebenchcl_adapter.py info

    # Run a specific sequence only:
    uv run python scripts/baselines/swebenchcl_adapter.py run --mode contextgraph --sequence django_django_sequence
"""

import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer

# Project root
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Default paths
DEFAULT_CURRICULUM = (
    REPO_ROOT / "baselines" / "agents-never-forget" / "data" / "SWE-Bench-CL-Curriculum.json"
)
DEFAULT_OUTPUT_DIR = REPO_ROOT / "results" / "swebenchcl"

# Neo4j connection for online learning (writable)
NEO4J_ONLINE_URI = "bolt://localhost:7690"
NEO4J_AUTH = ("neo4j", "contextgraph123")

# LiteLLM proxy for embeddings
LITELLM_BASE_URL = "http://localhost:4000/v1"

app = typer.Typer(
    name="swebenchcl",
    help="SWE-Bench-CL adapter for ContextGraph continual learning experiments.",
)


class MemoryMode(str, Enum):
    """Memory mode for experiments."""

    no_memory = "no_memory"
    faiss = "faiss"
    contextgraph = "contextgraph"


@dataclass
class TaskResult:
    """Result of a single task in a sequence."""

    instance_id: str
    sequence_id: str
    sequence_position: int
    repo: str
    success: bool
    tokens_used: int = 0
    time_seconds: float = 0.0
    memory_retrieved: int = 0  # Number of memory entries retrieved
    memory_stored: bool = False  # Whether experience was stored after this task
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SequenceResult:
    """Results for a complete sequence."""

    sequence_id: str
    repo: str
    num_tasks: int
    tasks: List[TaskResult] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if not self.tasks:
            return 0.0
        return sum(1 for t in self.tasks if t.success) / len(self.tasks)

    @property
    def success_count(self) -> int:
        return sum(1 for t in self.tasks if t.success)


@dataclass
class ExperimentResult:
    """Full experiment results."""

    mode: str
    curriculum_path: str
    start_time: str = ""
    end_time: str = ""
    sequences: List[SequenceResult] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_tasks(self) -> int:
        return sum(s.num_tasks for s in self.sequences)

    @property
    def total_success(self) -> int:
        return sum(s.success_count for s in self.sequences)

    @property
    def overall_success_rate(self) -> float:
        total = self.total_tasks
        return self.total_success / total if total > 0 else 0.0


def load_curriculum(path: Path) -> Dict[str, Any]:
    """Load the SWE-Bench-CL curriculum JSON.

    Returns:
        Dict with keys: metadata, evaluation_metrics, sequences
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Curriculum not found at {path}. "
            f"Ensure baselines/agents-never-forget/ is cloned."
        )
    with open(path) as f:
        data = json.load(f)
    logger.info(
        "Loaded curriculum: %d sequences, %d total tasks",
        data["metadata"]["num_sequences"],
        data["metadata"]["total_tasks"],
    )
    return data


def _create_faiss_memory(use_our_embeddings: bool = False):
    """Create a FAISS memory instance.

    Args:
        use_our_embeddings: If True, use text-embedding-3-large via LiteLLM proxy.
            If False (default), use sentence-transformers/all-MiniLM-L6-v2 (their approach).
    """
    from scripts.baselines.faiss_memory import FAISSMemory

    if use_our_embeddings:
        api_key = os.environ.get("LITELLM_MASTER_KEY", "")
        return FAISSMemory(
            embedding_provider="openai",
            model_name="text-embedding-3-large",
            api_key=api_key,
            base_url=LITELLM_BASE_URL,
            top_k=3,
        )
    else:
        return FAISSMemory(
            embedding_provider="sentence_transformers",
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            top_k=3,
        )


def _create_contextgraph_memory():
    """Create a ContextGraph memory instance (AgentMemory with online Neo4j).

    Uses the writable Neo4j instance (port 7690) for online learning.
    """
    from agent_memory.memory import AgentMemory

    api_key = os.environ.get("LITELLM_MASTER_KEY", "")
    return AgentMemory(
        neo4j_uri=NEO4J_ONLINE_URI,
        neo4j_auth=NEO4J_AUTH,
        embedding_api_key=api_key,
        embedding_base_url=LITELLM_BASE_URL,
        embedding_model="text-embedding-3-large",
    )


def _retrieve_context_faiss(memory, problem_text: str, sequence_id: str) -> List[Dict]:
    """Retrieve relevant context from FAISS memory."""
    return memory.retrieve_relevant(
        query=problem_text,
        top_k=3,
        sequence_filter=sequence_id,
    )


def _retrieve_context_contextgraph(memory, problem_text: str, repo: str) -> str:
    """Retrieve relevant context from ContextGraph.

    Uses the PlaybookRetriever (3-channel: cosine + BM25 + PPR).
    """
    from agent_memory.models import State

    state = State(
        tools=["edit", "search", "run_tests"],
        repo_summary=repo,
        task_description=problem_text[:500],
        current_error="",
        phase="understanding",
    )
    return memory.query_playbook(state, top_k=5)


def _store_experience_faiss(
    memory,
    instance_id: str,
    problem_text: str,
    solution_text: str,
    success: bool,
    sequence_id: str,
) -> None:
    """Store experience in FAISS memory."""
    memory.add_experience(
        instance_id=instance_id,
        problem_text=problem_text,
        solution_text=solution_text,
        success=success,
        sequence_id=sequence_id,
    )


def _store_experience_contextgraph(
    memory,
    instance_id: str,
    problem_text: str,
    solution_text: str,
    success: bool,
    repo: str,
) -> Optional[str]:
    """Store experience in ContextGraph via AgentMemory.learn().

    Returns the trajectory ID.
    """
    from agent_memory.writer import RawTrajectory

    # Create a minimal trajectory from the task result
    steps = [
        {
            "action": "understand_problem",
            "observation": problem_text[:300],
        },
        {
            "action": "apply_fix",
            "observation": solution_text[:500] if solution_text else "No solution provided",
        },
    ]

    trajectory = RawTrajectory(
        instance_id=instance_id,
        repo=repo,
        success=success,
        steps=steps,
        problem_statement=problem_text,
    )

    return memory.learn(trajectory)


def run_sequence(
    sequence: Dict[str, Any],
    mode: MemoryMode,
    memory: Any,
    dry_run: bool = False,
    results_lookup: Optional[Dict[str, bool]] = None,
) -> SequenceResult:
    """Run all tasks in a sequence, managing memory lifecycle.

    Args:
        sequence: Sequence dict from curriculum JSON
        mode: Memory mode (no_memory, faiss, contextgraph)
        memory: Memory instance (None for no_memory mode)
        dry_run: If True, simulate without actual agent execution
        results_lookup: Optional dict mapping instance_id -> success (from prior runs)

    Returns:
        SequenceResult with all task results
    """
    seq_id = sequence["id"]
    repo = sequence["repo"]
    tasks = sequence["tasks"]

    logger.info("Starting sequence: %s (%s, %d tasks)", seq_id, repo, len(tasks))

    seq_result = SequenceResult(
        sequence_id=seq_id,
        repo=repo,
        num_tasks=len(tasks),
    )

    for task_data in tasks:
        instance_id = task_data["metadata"]["instance_id"]
        problem_text = task_data["task"]["problem_statement"]
        position = task_data["continual_learning"]["sequence_position"]
        patch = task_data["evaluation"].get("patch", "")

        task_start = time.time()

        # --- Retrieve from memory ---
        memory_retrieved = 0
        if mode == MemoryMode.faiss and memory is not None:
            results = _retrieve_context_faiss(memory, problem_text, seq_id)
            memory_retrieved = len(results)
            if dry_run:
                logger.info(
                    "  [FAISS] Retrieved %d entries for %s", memory_retrieved, instance_id
                )
        elif mode == MemoryMode.contextgraph and memory is not None:
            context = _retrieve_context_contextgraph(memory, problem_text, repo)
            memory_retrieved = 1 if context else 0
            if dry_run:
                logger.info(
                    "  [ContextGraph] Retrieved context (%d chars) for %s",
                    len(context) if context else 0,
                    instance_id,
                )

        # --- Determine task success ---
        if results_lookup is not None:
            # Use pre-computed results
            success = results_lookup.get(instance_id, False)
        elif dry_run:
            # Simulate: mark as failed (placeholder)
            success = False
        else:
            # In real mode, this would invoke the agent
            # For now, mark as not attempted
            success = False
            logger.warning(
                "  Task %s: no agent configured, marking as failed", instance_id
            )

        # --- Store experience in memory ---
        memory_stored = False
        solution_text = patch if success else ""

        if mode == MemoryMode.faiss and memory is not None:
            _store_experience_faiss(
                memory, instance_id, problem_text, solution_text, success, seq_id
            )
            memory_stored = True
        elif mode == MemoryMode.contextgraph and memory is not None:
            try:
                _store_experience_contextgraph(
                    memory, instance_id, problem_text, solution_text, success, repo
                )
                memory_stored = True
            except Exception as e:
                logger.error("  Failed to store in ContextGraph: %s", e)

        elapsed = time.time() - task_start

        task_result = TaskResult(
            instance_id=instance_id,
            sequence_id=seq_id,
            sequence_position=position,
            repo=repo,
            success=success,
            time_seconds=elapsed,
            memory_retrieved=memory_retrieved,
            memory_stored=memory_stored,
        )
        seq_result.tasks.append(task_result)

        if dry_run:
            logger.info(
                "  [%d/%d] %s -> success=%s, memory_retrieved=%d",
                position,
                len(tasks),
                instance_id,
                success,
                memory_retrieved,
            )

    logger.info(
        "Sequence %s complete: %d/%d solved (%.1f%%)",
        seq_id,
        seq_result.success_count,
        seq_result.num_tasks,
        seq_result.success_rate * 100,
    )
    return seq_result


def save_results(result: ExperimentResult, output_dir: Path) -> Path:
    """Save experiment results to JSON.

    Returns the path to the saved file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"swebenchcl_{result.mode}_{int(time.time())}.json"
    output_path = output_dir / filename

    # Convert dataclasses to dicts
    data = {
        "mode": result.mode,
        "curriculum_path": result.curriculum_path,
        "start_time": result.start_time,
        "end_time": result.end_time,
        "config": result.config,
        "overall_success_rate": result.overall_success_rate,
        "total_tasks": result.total_tasks,
        "total_success": result.total_success,
        "sequences": [
            {
                "sequence_id": s.sequence_id,
                "repo": s.repo,
                "num_tasks": s.num_tasks,
                "success_rate": s.success_rate,
                "success_count": s.success_count,
                "tasks": [asdict(t) for t in s.tasks],
            }
            for s in result.sequences
        ],
    }

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    logger.info("Results saved to %s", output_path)
    return output_path


@app.command()
def info(
    curriculum: Path = typer.Option(
        DEFAULT_CURRICULUM, "--curriculum", "-c", help="Path to curriculum JSON"
    ),
):
    """Show information about the SWE-Bench-CL curriculum."""
    data = load_curriculum(curriculum)
    meta = data["metadata"]

    typer.echo(f"Name: {meta['name']}")
    typer.echo(f"Version: {meta['version']}")
    typer.echo(f"Total sequences: {meta['num_sequences']}")
    typer.echo(f"Total tasks: {meta['total_tasks']}")
    typer.echo(f"Repositories: {', '.join(meta['repositories'])}")
    typer.echo("")
    typer.echo("Sequences:")
    for seq in data["sequences"]:
        typer.echo(f"  {seq['id']}: {seq['num_tasks']} tasks ({seq['repo']})")
        if "statistics" in seq:
            stats = seq["statistics"]
            typer.echo(f"    Difficulty: {stats.get('difficulty_distribution', {})}")


@app.command()
def run(
    mode: MemoryMode = typer.Option(
        MemoryMode.contextgraph, "--mode", "-m", help="Memory mode"
    ),
    curriculum: Path = typer.Option(
        DEFAULT_CURRICULUM, "--curriculum", "-c", help="Path to curriculum JSON"
    ),
    output_dir: Path = typer.Option(
        DEFAULT_OUTPUT_DIR, "--output-dir", "-o", help="Output directory for results"
    ),
    sequence: Optional[str] = typer.Option(
        None, "--sequence", "-s", help="Run only this sequence (by id)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Simulate without running agent"
    ),
    use_our_embeddings: bool = typer.Option(
        False,
        "--use-our-embeddings",
        help="For FAISS mode: use text-embedding-3-large instead of MiniLM",
    ),
    results_file: Optional[Path] = typer.Option(
        None,
        "--results-file",
        help="JSON file with prior results (instance_id -> success mapping)",
    ),
):
    """Run a SWE-Bench-CL experiment with the specified memory mode.

    Processes tasks in curriculum order within each sequence. For each task:
    1. Retrieve relevant context from memory (if memory mode enabled)
    2. Execute the task (or use pre-computed results)
    3. Store the experience in memory (if memory mode enabled)
    4. Record metrics

    Results are saved as JSON for analysis with swebenchcl_metrics.py.
    """
    data = load_curriculum(curriculum)

    # Load pre-computed results if provided
    results_lookup: Optional[Dict[str, bool]] = None
    if results_file and results_file.exists():
        with open(results_file) as f:
            raw = json.load(f)
        # Support both {instance_id: bool} and [{instance_id: ..., success: ...}]
        if isinstance(raw, dict):
            results_lookup = {k: bool(v) for k, v in raw.items()}
        elif isinstance(raw, list):
            results_lookup = {r["instance_id"]: r.get("success", False) for r in raw}
        logger.info("Loaded %d pre-computed results", len(results_lookup))

    # Initialize memory
    memory = None
    if mode == MemoryMode.faiss:
        memory = _create_faiss_memory(use_our_embeddings=use_our_embeddings)
        logger.info("Initialized FAISS memory (provider=%s)", memory.embedding_provider)
    elif mode == MemoryMode.contextgraph:
        if not dry_run:
            memory = _create_contextgraph_memory()
            logger.info("Initialized ContextGraph memory (Neo4j %s)", NEO4J_ONLINE_URI)
        else:
            logger.info("Dry run: skipping ContextGraph connection")

    # Filter sequences if requested
    sequences = data["sequences"]
    if sequence:
        sequences = [s for s in sequences if s["id"] == sequence]
        if not sequences:
            typer.echo(f"Sequence '{sequence}' not found.", err=True)
            raise typer.Exit(1)

    # Run experiment
    experiment = ExperimentResult(
        mode=mode.value,
        curriculum_path=str(curriculum),
        start_time=time.strftime("%Y-%m-%dT%H:%M:%S"),
        config={
            "use_our_embeddings": use_our_embeddings,
            "dry_run": dry_run,
            "sequence_filter": sequence,
            "neo4j_uri": NEO4J_ONLINE_URI if mode == MemoryMode.contextgraph else None,
        },
    )

    for seq_data in sequences:
        seq_result = run_sequence(
            sequence=seq_data,
            mode=mode,
            memory=memory,
            dry_run=dry_run,
            results_lookup=results_lookup,
        )
        experiment.sequences.append(seq_result)

        # For FAISS mode, optionally save memory state per sequence
        if mode == MemoryMode.faiss and memory is not None and not dry_run:
            mem_dir = output_dir / "memory_states" / seq_data["id"]
            memory.save(mem_dir)

    experiment.end_time = time.strftime("%Y-%m-%dT%H:%M:%S")

    # Save results
    output_path = save_results(experiment, output_dir)

    # Print summary
    typer.echo("")
    typer.echo("=" * 60)
    typer.echo(f"SWE-Bench-CL Experiment Complete ({mode.value})")
    typer.echo("=" * 60)
    typer.echo(f"Total tasks:   {experiment.total_tasks}")
    typer.echo(f"Total success: {experiment.total_success}")
    typer.echo(f"Success rate:  {experiment.overall_success_rate:.1%}")
    typer.echo(f"Results:       {output_path}")
    typer.echo("")
    typer.echo("Per-sequence results:")
    for s in experiment.sequences:
        typer.echo(f"  {s.sequence_id}: {s.success_count}/{s.num_tasks} ({s.success_rate:.1%})")

    # Cleanup
    if mode == MemoryMode.contextgraph and memory is not None:
        memory.close()


@app.command()
def from_results(
    results_dir: Path = typer.Argument(..., help="Directory with SWE-agent result JSONs"),
    mode: MemoryMode = typer.Option(
        MemoryMode.contextgraph, "--mode", "-m", help="Memory mode to simulate"
    ),
    curriculum: Path = typer.Option(
        DEFAULT_CURRICULUM, "--curriculum", "-c", help="Path to curriculum JSON"
    ),
    output_dir: Path = typer.Option(
        DEFAULT_OUTPUT_DIR, "--output-dir", "-o", help="Output directory"
    ),
):
    """Process existing SWE-agent results through the CL memory pipeline.

    Takes a directory of result files (from run_real_swe_experiment.py or similar)
    and replays the experiment with the specified memory mode, recording what
    context would have been retrieved at each step.

    This is useful for post-hoc analysis: given actual agent results, measure
    how different memory systems would have performed in a CL setting.
    """
    # Collect results from the directory
    results_lookup: Dict[str, bool] = {}
    for json_file in sorted(results_dir.glob("*.json")):
        try:
            with open(json_file) as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if "instance_id" in item:
                        results_lookup[item["instance_id"]] = item.get("success", False)
            elif isinstance(data, dict):
                if "instance_id" in data:
                    results_lookup[data["instance_id"]] = data.get("success", False)
        except (json.JSONDecodeError, KeyError):
            continue

    if not results_lookup:
        typer.echo("No valid results found in the directory.", err=True)
        raise typer.Exit(1)

    logger.info("Loaded %d results from %s", len(results_lookup), results_dir)

    # Delegate to run command logic
    data = load_curriculum(curriculum)

    memory = None
    if mode == MemoryMode.faiss:
        memory = _create_faiss_memory()
    elif mode == MemoryMode.contextgraph:
        memory = _create_contextgraph_memory()

    experiment = ExperimentResult(
        mode=mode.value,
        curriculum_path=str(curriculum),
        start_time=time.strftime("%Y-%m-%dT%H:%M:%S"),
        config={"source_results_dir": str(results_dir)},
    )

    for seq_data in data["sequences"]:
        seq_result = run_sequence(
            sequence=seq_data,
            mode=mode,
            memory=memory,
            dry_run=False,
            results_lookup=results_lookup,
        )
        experiment.sequences.append(seq_result)

    experiment.end_time = time.strftime("%Y-%m-%dT%H:%M:%S")
    output_path = save_results(experiment, output_dir)

    typer.echo(f"Results saved to {output_path}")
    typer.echo(f"Overall success rate: {experiment.overall_success_rate:.1%}")

    if mode == MemoryMode.contextgraph and memory is not None:
        memory.close()


if __name__ == "__main__":
    app()
