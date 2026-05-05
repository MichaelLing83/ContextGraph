#!/usr/bin/env python3
"""SWE-Bench-CL continual learning metrics calculator.

Computes CL-specific metrics from experiment result JSONs produced by
swebenchcl_adapter.py. Supports comparing multiple modes (no_memory, faiss,
contextgraph) side by side.

Metrics implemented:
- ACC: Average success rate across all sequences
- Forward Transfer (FWT): Memory vs no-memory improvement per task position
- AULC: Area Under the Learning Curve (cumulative success over sequence)
- Per-sequence success rate
- Memory utilization statistics

Usage:
    # Compute metrics for a single result file:
    uv run python scripts/baselines/swebenchcl_metrics.py compute results/swebenchcl/result.json

    # Compare multiple modes:
    uv run python scripts/baselines/swebenchcl_metrics.py compare \
        --no-memory results/swebenchcl/no_memory.json \
        --faiss results/swebenchcl/faiss.json \
        --contextgraph results/swebenchcl/contextgraph.json

    # Generate a LaTeX-ready table:
    uv run python scripts/baselines/swebenchcl_metrics.py compare --format latex \
        --no-memory results/swebenchcl/no_memory.json \
        --contextgraph results/swebenchcl/contextgraph.json
"""

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import typer

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = typer.Typer(
    name="swebenchcl-metrics",
    help="Compute continual learning metrics from SWE-Bench-CL experiment results.",
)


@dataclass
class SequenceMetrics:
    """Metrics for a single sequence."""

    sequence_id: str
    repo: str
    num_tasks: int
    success_rate: float  # ACC for this sequence
    aulc: float  # Area Under the Learning Curve
    learning_curve: List[float]  # Cumulative success rate at each position
    success_by_position: List[bool]  # Success/fail at each position


@dataclass
class ExperimentMetrics:
    """Full experiment metrics."""

    mode: str
    acc: float  # Average success rate across sequences
    aulc: float  # Average AULC across sequences
    total_tasks: int
    total_success: int
    sequences: List[SequenceMetrics] = field(default_factory=list)
    memory_stats: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ComparisonReport:
    """Comparison of multiple experiment modes."""

    modes: List[str]
    metrics: Dict[str, ExperimentMetrics]  # mode -> metrics
    forward_transfer: Dict[str, float]  # mode -> FWT (relative to no_memory)
    per_sequence_comparison: Dict[str, Dict[str, float]]  # seq_id -> {mode -> acc}


def load_results(path: Path) -> Dict[str, Any]:
    """Load experiment results from JSON."""
    with open(path) as f:
        data = json.load(f)
    return data


def compute_learning_curve(successes: List[bool]) -> List[float]:
    """Compute cumulative success rate at each position.

    Returns a list where position i is the success rate for tasks 0..i.
    """
    if not successes:
        return []

    curve = []
    cumulative_success = 0
    for i, s in enumerate(successes):
        cumulative_success += int(s)
        curve.append(cumulative_success / (i + 1))
    return curve


def compute_aulc(learning_curve: List[float]) -> float:
    """Compute Area Under the Learning Curve.

    Uses trapezoidal rule normalized by number of tasks.
    AULC in [0, 1] where 1 means perfect performance from the start.
    """
    if not learning_curve:
        return 0.0

    n = len(learning_curve)
    if n == 1:
        return learning_curve[0]

    # Trapezoidal integration normalized to [0, 1]
    area = 0.0
    for i in range(1, n):
        area += (learning_curve[i - 1] + learning_curve[i]) / 2.0
    return area / (n - 1)


def compute_metrics(results: Dict[str, Any]) -> ExperimentMetrics:
    """Compute CL metrics from experiment results.

    Args:
        results: Loaded JSON results from swebenchcl_adapter.py

    Returns:
        ExperimentMetrics with ACC, AULC, per-sequence metrics
    """
    mode = results["mode"]
    sequences_data = results["sequences"]

    seq_metrics_list = []
    total_tasks = 0
    total_success = 0
    total_memory_retrieved = 0
    total_memory_stored = 0

    for seq_data in sequences_data:
        tasks = seq_data["tasks"]
        successes = [t["success"] for t in tasks]

        learning_curve = compute_learning_curve(successes)
        aulc = compute_aulc(learning_curve)

        seq_metrics = SequenceMetrics(
            sequence_id=seq_data["sequence_id"],
            repo=seq_data["repo"],
            num_tasks=seq_data["num_tasks"],
            success_rate=seq_data["success_rate"],
            aulc=aulc,
            learning_curve=learning_curve,
            success_by_position=successes,
        )
        seq_metrics_list.append(seq_metrics)

        total_tasks += seq_data["num_tasks"]
        total_success += seq_data["success_count"]

        # Memory stats
        for t in tasks:
            total_memory_retrieved += t.get("memory_retrieved", 0)
            total_memory_stored += int(t.get("memory_stored", False))

    # Average ACC across sequences (not weighted by task count)
    acc = (
        sum(s.success_rate for s in seq_metrics_list) / len(seq_metrics_list)
        if seq_metrics_list
        else 0.0
    )

    # Average AULC across sequences
    avg_aulc = (
        sum(s.aulc for s in seq_metrics_list) / len(seq_metrics_list)
        if seq_metrics_list
        else 0.0
    )

    memory_stats = {
        "total_retrieved": total_memory_retrieved,
        "total_stored": total_memory_stored,
        "avg_retrieved_per_task": (
            total_memory_retrieved / total_tasks if total_tasks > 0 else 0
        ),
    }

    return ExperimentMetrics(
        mode=mode,
        acc=acc,
        aulc=avg_aulc,
        total_tasks=total_tasks,
        total_success=total_success,
        sequences=seq_metrics_list,
        memory_stats=memory_stats,
    )


def compute_forward_transfer(
    baseline_metrics: ExperimentMetrics,
    memory_metrics: ExperimentMetrics,
) -> float:
    """Compute Forward Transfer: improvement of memory over no-memory.

    FWT = (1/S) * sum_s [ ACC_memory(s) - ACC_baseline(s) ]
    where S is the number of sequences.

    Positive FWT means the memory system helps.
    """
    baseline_by_seq = {s.sequence_id: s.success_rate for s in baseline_metrics.sequences}
    memory_by_seq = {s.sequence_id: s.success_rate for s in memory_metrics.sequences}

    common_seqs = set(baseline_by_seq.keys()) & set(memory_by_seq.keys())
    if not common_seqs:
        return 0.0

    fwt_sum = 0.0
    for seq_id in common_seqs:
        fwt_sum += memory_by_seq[seq_id] - baseline_by_seq[seq_id]

    return fwt_sum / len(common_seqs)


def compute_position_transfer(
    baseline_results: Dict[str, Any],
    memory_results: Dict[str, Any],
) -> List[Tuple[int, float]]:
    """Compute per-position forward transfer.

    For each position in the sequence, compute:
    FWT(pos) = P(success | memory, pos) - P(success | no_memory, pos)

    This shows whether memory helps more on later tasks (expected for CL).

    Returns:
        List of (position, fwt) tuples
    """
    # Aggregate successes by position
    baseline_by_pos: Dict[int, List[bool]] = {}
    memory_by_pos: Dict[int, List[bool]] = {}

    for seq in baseline_results["sequences"]:
        for task in seq["tasks"]:
            pos = task["sequence_position"]
            baseline_by_pos.setdefault(pos, []).append(task["success"])

    for seq in memory_results["sequences"]:
        for task in seq["tasks"]:
            pos = task["sequence_position"]
            memory_by_pos.setdefault(pos, []).append(task["success"])

    results = []
    all_positions = sorted(set(baseline_by_pos.keys()) | set(memory_by_pos.keys()))
    for pos in all_positions:
        baseline_rate = (
            sum(baseline_by_pos.get(pos, [])) / len(baseline_by_pos.get(pos, [1]))
            if baseline_by_pos.get(pos)
            else 0.0
        )
        memory_rate = (
            sum(memory_by_pos.get(pos, [])) / len(memory_by_pos.get(pos, [1]))
            if memory_by_pos.get(pos)
            else 0.0
        )
        results.append((pos, memory_rate - baseline_rate))

    return results


def format_table(report: ComparisonReport, fmt: str = "text") -> str:
    """Format comparison report as a table.

    Args:
        report: ComparisonReport to format
        fmt: "text" for console, "latex" for LaTeX, "csv" for CSV
    """
    modes = report.modes
    metrics = report.metrics

    if fmt == "latex":
        return _format_latex(modes, metrics, report)
    elif fmt == "csv":
        return _format_csv(modes, metrics, report)
    else:
        return _format_text(modes, metrics, report)


def _format_text(
    modes: List[str],
    metrics: Dict[str, ExperimentMetrics],
    report: ComparisonReport,
) -> str:
    """Plain text table format."""
    lines = []
    lines.append("=" * 70)
    lines.append("SWE-Bench-CL Continual Learning Metrics Comparison")
    lines.append("=" * 70)
    lines.append("")

    # Summary table
    header = f"{'Metric':<20}" + "".join(f"{m:>15}" for m in modes)
    lines.append(header)
    lines.append("-" * len(header))

    # ACC
    row = f"{'ACC':<20}" + "".join(
        f"{metrics[m].acc:>14.1%}" for m in modes
    )
    lines.append(row)

    # AULC
    row = f"{'AULC':<20}" + "".join(
        f"{metrics[m].aulc:>14.3f}" for m in modes
    )
    lines.append(row)

    # Total solved
    row = f"{'Solved':<20}" + "".join(
        f"{metrics[m].total_success:>10}/{metrics[m].total_tasks}" for m in modes
    )
    lines.append(row)

    # Forward Transfer
    if report.forward_transfer:
        lines.append("")
        lines.append("Forward Transfer (vs no_memory):")
        for mode_name, fwt in report.forward_transfer.items():
            sign = "+" if fwt >= 0 else ""
            lines.append(f"  {mode_name}: {sign}{fwt:.1%}")

    # Per-sequence breakdown
    lines.append("")
    lines.append("-" * 70)
    lines.append("Per-Sequence ACC:")
    lines.append("")
    seq_header = f"{'Sequence':<35}" + "".join(f"{m:>12}" for m in modes)
    lines.append(seq_header)
    lines.append("-" * len(seq_header))

    for seq_id, mode_rates in report.per_sequence_comparison.items():
        row = f"{seq_id:<35}" + "".join(
            f"{mode_rates.get(m, 0.0):>11.1%}" for m in modes
        )
        lines.append(row)

    lines.append("")
    return "\n".join(lines)


def _format_latex(
    modes: List[str],
    metrics: Dict[str, ExperimentMetrics],
    report: ComparisonReport,
) -> str:
    """LaTeX table format."""
    mode_labels = {
        "no_memory": "No Memory",
        "faiss": "FAISS (MiniLM)",
        "contextgraph": "ContextGraph",
    }
    cols = " & ".join(mode_labels.get(m, m) for m in modes)

    lines = []
    lines.append(r"\begin{table}[t]")
    lines.append(r"\centering")
    lines.append(r"\caption{SWE-Bench-CL Continual Learning Results}")
    lines.append(r"\begin{tabular}{l" + "c" * len(modes) + "}")
    lines.append(r"\toprule")
    lines.append(f"Metric & {cols} \\\\")
    lines.append(r"\midrule")

    # ACC
    acc_vals = " & ".join(f"{metrics[m].acc:.1%}" for m in modes)
    lines.append(f"ACC & {acc_vals} \\\\")

    # AULC
    aulc_vals = " & ".join(f"{metrics[m].aulc:.3f}" for m in modes)
    lines.append(f"AULC & {aulc_vals} \\\\")

    # FWT
    if report.forward_transfer:
        fwt_vals = " & ".join(
            f"{report.forward_transfer.get(m, 0.0):+.1%}" if m != "no_memory" else "---"
            for m in modes
        )
        lines.append(f"FWT & {fwt_vals} \\\\")

    lines.append(r"\midrule")

    # Per-sequence
    for seq_id, mode_rates in report.per_sequence_comparison.items():
        short_name = seq_id.replace("_sequence", "").replace("_", "/")
        vals = " & ".join(f"{mode_rates.get(m, 0.0):.1%}" for m in modes)
        lines.append(f"{short_name} & {vals} \\\\")

    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table}")

    return "\n".join(lines)


def _format_csv(
    modes: List[str],
    metrics: Dict[str, ExperimentMetrics],
    report: ComparisonReport,
) -> str:
    """CSV format."""
    lines = []
    header = "metric," + ",".join(modes)
    lines.append(header)

    lines.append("acc," + ",".join(f"{metrics[m].acc:.4f}" for m in modes))
    lines.append("aulc," + ",".join(f"{metrics[m].aulc:.4f}" for m in modes))
    lines.append(
        "total_success," + ",".join(str(metrics[m].total_success) for m in modes)
    )
    lines.append(
        "total_tasks," + ",".join(str(metrics[m].total_tasks) for m in modes)
    )

    if report.forward_transfer:
        lines.append(
            "fwt,"
            + ",".join(
                f"{report.forward_transfer.get(m, 0.0):.4f}" for m in modes
            )
        )

    lines.append("")
    lines.append("sequence," + ",".join(modes))
    for seq_id, mode_rates in report.per_sequence_comparison.items():
        vals = ",".join(f"{mode_rates.get(m, 0.0):.4f}" for m in modes)
        lines.append(f"{seq_id},{vals}")

    return "\n".join(lines)


@app.command()
def compute(
    results_file: Path = typer.Argument(..., help="Path to experiment results JSON"),
):
    """Compute CL metrics for a single experiment result file."""
    results = load_results(results_file)
    metrics = compute_metrics(results)

    typer.echo(f"Mode: {metrics.mode}")
    typer.echo(f"ACC (avg sequence success rate): {metrics.acc:.1%}")
    typer.echo(f"AULC (avg area under learning curve): {metrics.aulc:.3f}")
    typer.echo(f"Total: {metrics.total_success}/{metrics.total_tasks}")
    typer.echo("")
    typer.echo("Per-sequence:")
    for seq in metrics.sequences:
        typer.echo(
            f"  {seq.sequence_id}: ACC={seq.success_rate:.1%}, AULC={seq.aulc:.3f}"
        )
    typer.echo("")
    typer.echo(f"Memory stats: {json.dumps(metrics.memory_stats, indent=2)}")


@app.command()
def compare(
    no_memory: Optional[Path] = typer.Option(
        None, "--no-memory", help="Results file for no_memory baseline"
    ),
    faiss: Optional[Path] = typer.Option(
        None, "--faiss", help="Results file for FAISS memory"
    ),
    contextgraph: Optional[Path] = typer.Option(
        None, "--contextgraph", help="Results file for ContextGraph memory"
    ),
    fmt: str = typer.Option("text", "--format", "-f", help="Output format: text, latex, csv"),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Save output to file"
    ),
):
    """Compare CL metrics across multiple memory modes.

    Requires at least two result files to compare. Computes Forward Transfer
    relative to the no_memory baseline (if provided).
    """
    # Load available results
    mode_files = {}
    if no_memory and no_memory.exists():
        mode_files["no_memory"] = no_memory
    if faiss and faiss.exists():
        mode_files["faiss"] = faiss
    if contextgraph and contextgraph.exists():
        mode_files["contextgraph"] = contextgraph

    if len(mode_files) < 2:
        typer.echo("Need at least 2 result files to compare.", err=True)
        raise typer.Exit(1)

    # Compute metrics for each mode
    all_results = {}
    all_metrics = {}
    for mode_name, path in mode_files.items():
        results = load_results(path)
        all_results[mode_name] = results
        all_metrics[mode_name] = compute_metrics(results)

    # Compute Forward Transfer relative to no_memory baseline
    forward_transfer = {}
    baseline = all_metrics.get("no_memory")
    if baseline:
        for mode_name, m in all_metrics.items():
            if mode_name == "no_memory":
                continue
            forward_transfer[mode_name] = compute_forward_transfer(baseline, m)

    # Per-sequence comparison
    per_sequence: Dict[str, Dict[str, float]] = {}
    for mode_name, m in all_metrics.items():
        for seq in m.sequences:
            if seq.sequence_id not in per_sequence:
                per_sequence[seq.sequence_id] = {}
            per_sequence[seq.sequence_id][mode_name] = seq.success_rate

    # Build report
    modes = list(mode_files.keys())
    report = ComparisonReport(
        modes=modes,
        metrics=all_metrics,
        forward_transfer=forward_transfer,
        per_sequence_comparison=per_sequence,
    )

    # Format and output
    table = format_table(report, fmt=fmt)
    typer.echo(table)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w") as f:
            f.write(table)
        typer.echo(f"\nSaved to {output}")

    # Also save raw metrics as JSON for programmatic use
    if output:
        json_output = output.with_suffix(".json")
        report_data = {
            "modes": modes,
            "metrics": {
                m: {
                    "acc": all_metrics[m].acc,
                    "aulc": all_metrics[m].aulc,
                    "total_tasks": all_metrics[m].total_tasks,
                    "total_success": all_metrics[m].total_success,
                    "memory_stats": all_metrics[m].memory_stats,
                }
                for m in modes
            },
            "forward_transfer": forward_transfer,
            "per_sequence": per_sequence,
        }
        with open(json_output, "w") as f:
            json.dump(report_data, f, indent=2)
        typer.echo(f"Raw metrics JSON: {json_output}")


@app.command()
def learning_curve(
    results_file: Path = typer.Argument(..., help="Path to experiment results JSON"),
    sequence: Optional[str] = typer.Option(
        None, "--sequence", "-s", help="Show curve for specific sequence only"
    ),
):
    """Display the learning curve data for each sequence.

    Shows cumulative success rate at each position in the sequence.
    Useful for visualizing how the agent improves (or degrades) over time.
    """
    results = load_results(results_file)
    metrics = compute_metrics(results)

    for seq in metrics.sequences:
        if sequence and seq.sequence_id != sequence:
            continue

        typer.echo(f"\n{seq.sequence_id} ({seq.repo}):")
        typer.echo(f"  Final ACC: {seq.success_rate:.1%}, AULC: {seq.aulc:.3f}")
        typer.echo("  Position | Cumulative ACC | Result")
        typer.echo("  " + "-" * 45)

        for i, (rate, success) in enumerate(
            zip(seq.learning_curve, seq.success_by_position)
        ):
            marker = "+" if success else "-"
            bar_len = int(rate * 20)
            bar = "#" * bar_len + "." * (20 - bar_len)
            typer.echo(f"  {i+1:>8} | {rate:>13.1%} | [{bar}] {marker}")


if __name__ == "__main__":
    app()
