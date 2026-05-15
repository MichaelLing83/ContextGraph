"""Shared helpers for SWE-bench harness eval_results.json.

Imported by both `run_real_swe_experiment.py` (control + treatment SWE-agent
runs) and `run_real_openhands_experiment.py` so that pass@k attempts agree
on ground-truth resolved status, and so the `resolved_source` /
`resolved_source_counts` schema stays identical across runner backends.
Kept under `scripts/` to avoid pulling the heavyweight `sweagent` /
`openhands` runner modules into a common agent_memory import path.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional

logger = logging.getLogger(__name__)


def load_swebench_eval_results(output_dir: Optional[Path]) -> Optional[Dict[str, bool]]:
    """Load SWE-bench harness eval_results.json for an output dir, if present.

    The swebench harness writes ``eval_results.json`` (or a per-run file) with
    ``{instance_id: resolved_bool}`` once verification has been run. This is
    the only ground-truth signal — ``traj.info.exit_status`` and
    ``AgentState.FINISHED`` only mean the agent stopped, not that the
    submission passed the tests.

    Returns None if no eval file is found, so the caller can fall back to a
    heuristic and emit a warning. Malformed eval files (unparseable JSON,
    unreadable bytes, wrong top-level type) are logged at WARNING with the
    offending path and then skipped — better to surface broken ground-truth
    inputs than silently fall through.
    """
    if not output_dir:
        return None
    candidates = [
        output_dir / "eval_results.json",
        output_dir / "eval_partial" / "eval_results.json",
        output_dir.parent / "eval_results.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeError) as e:
            # UnicodeError (specifically UnicodeDecodeError) covers files
            # that aren't valid UTF-8 — e.g. a partially-written eval file
            # or one mangled by an upstream tool. Logged + skipped just
            # like JSON / OS failures.
            logger.warning("Could not read eval_results from %s: %s", path, e)
            continue
        if not isinstance(data, dict):
            logger.warning(
                "Ignoring eval_results at %s: expected a dict, got %s",
                path, type(data).__name__,
            )
            continue
        # Accept both flat {iid: bool} and {"resolved": [...], "unresolved": [...]}.
        if "resolved" in data and isinstance(data["resolved"], list):
            resolved = set(data["resolved"])
            unresolved = set(data.get("unresolved", []))
            overlap = resolved & unresolved
            if overlap:
                # Treat resolved as authoritative when both lists list the
                # same id — the harness reports pass/fail per instance and a
                # double-listing is almost certainly a bookkeeping bug; we'd
                # rather over-credit than silently mark a passing run as
                # failed. Surface the bad input so it can be fixed upstream.
                logger.warning(
                    "eval_results at %s lists %d instance(s) in both "
                    "resolved and unresolved (%s…); treating as resolved.",
                    path, len(overlap), sorted(overlap)[:3],
                )
            return (
                {i: False for i in unresolved}
                | {i: True for i in resolved}
            )
        # Flat {iid: bool} form. Be strict about the value type: a plain
        # `bool(v)` would silently classify the string "false" or "0" as
        # resolved (truthy), masking upstream bugs. Accept literal
        # True/False or a small set of canonical string literals; log
        # and skip anything else.
        return _parse_flat_resolved_map(data, path)
    return None


_TRUE_LITERALS = {"true", "1", "yes", "pass", "passed", "resolved"}
_FALSE_LITERALS = {"false", "0", "no", "fail", "failed", "unresolved"}


def _parse_flat_resolved_map(data: dict, source_path: Path) -> Dict[str, bool]:
    """Coerce a flat ``{instance_id: value}`` mapping into ``{iid: bool}``.

    Accepts bool, int (0/1), and the canonical pass/fail string literals.
    Unknown values are logged with the instance id + path and dropped so
    callers fall back to the success heuristic for those instances.
    """
    out: Dict[str, bool] = {}
    skipped: List[str] = []
    for k, v in data.items():
        if isinstance(v, bool):
            out[k] = v
        elif isinstance(v, int):
            out[k] = bool(v)
        elif isinstance(v, str):
            norm = v.strip().lower()
            if norm in _TRUE_LITERALS:
                out[k] = True
            elif norm in _FALSE_LITERALS:
                out[k] = False
            else:
                skipped.append(k)
        else:
            skipped.append(k)
    if skipped:
        logger.warning(
            "eval_results at %s has %d entries with unrecognised values "
            "(%s…); dropping so callers fall back to the runner heuristic.",
            source_path, len(skipped), skipped[:3],
        )
    return out


def classify_group_source(
    eval_lookup: Optional[Mapping[str, bool]],
    n_eval: int,
    n_heuristic: int,
    n_mixed: int = 0,
) -> str:
    """Return one of "swebench_eval" | "heuristic" | "mixed".

    "swebench_eval" means every problem in the group was resolved via the
    eval file. "heuristic" means every problem fell back to the runner's
    own success heuristic (either because no eval file exists, or it
    exists but lists no overlapping instance ids). "mixed" means at least
    one of each — including the OpenHands-specific case where a single
    instance had attempts from both sources (counted in ``n_mixed``).
    """
    if n_mixed > 0:
        return "mixed"
    if eval_lookup is None or n_eval == 0:
        return "heuristic"
    if n_heuristic == 0:
        return "swebench_eval"
    return "mixed"


def summarise_group_resolved_source(
    eval_lookup: Optional[Mapping[str, bool]],
    per_instance_sources: "Iterable[str]",
) -> Dict[str, object]:
    """Aggregate per-instance resolved_source labels into the schema both
    runner files emit. Avoids drift between the SWE-agent and OpenHands
    code paths — they both produce the same dict shape from the same
    input list of source labels per instance.

    Input: an iterable of source strings, one per instance ("swebench_eval",
    "heuristic", or "mixed"). Unknown labels are tallied into "heuristic"
    as a safe fallback (callers control what they emit so this should
    never fire in practice).

    Returns ``{"label": <group label>, "counts": {"swebench_eval": int,
    "heuristic": int, "mixed": int}}``. Callers spread this into their
    output JSON.
    """
    n_eval = 0
    n_heur = 0
    n_mixed = 0
    for source in per_instance_sources:
        if source == "swebench_eval":
            n_eval += 1
        elif source == "mixed":
            n_mixed += 1
        else:
            # "heuristic" or anything else falls back to heuristic.
            n_heur += 1
    return {
        "label": classify_group_source(eval_lookup, n_eval, n_heur, n_mixed),
        "counts": {
            "swebench_eval": n_eval,
            "heuristic": n_heur,
            "mixed": n_mixed,
        },
    }
