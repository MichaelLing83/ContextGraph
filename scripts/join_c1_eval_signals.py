"""Stage 2: join huawei-eval verdict (PASS/FAIL) to each exported C1 trajectory.

For each session in `_index.json` (produced by export_claude_sessions.py), find
the best-matching `huawei-eval/experiment/C1-*` dir by (model_family, length,
date), then take a majority vote across `eval_report-*.md` verdicts. Writes the
result back to `_index.json` and emits `_eval_join.md`.

Mismatch policy:
- Sessions whose (model_family, length) cannot be matched leave success=None.
- Sessions matched with date offset > 0 days are flagged in the report.
- Tied votes break toward FAIL (conservative).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

EVAL_ROOT = Path("/Users/zihanwu/Public/codes/huawei-eval/experiment")
DEFAULT_INDEX = Path("results/c1_experiences/trajectories/_index.json")
DEFAULT_REPORT = Path("results/c1_experiences/trajectories/_eval_join.md")

VERDICT_RE = re.compile(r"Verdict[^A-Za-z]*?(PASS|FAIL)\b", re.IGNORECASE)

# Eval dirs with .OLD suffix or unrelated tasks are skipped
EVAL_DIR_RE = re.compile(
    r"^C1-"
    r"(?P<model>claude-opus(?:-max)?|codex-gpt-5[._]4|cursor-composer2|opencode-glm51)"
    r"-(?P<length>long|short)"
    r"-(?P<date>\d{4}-?\d{2}-?\d{2})$"
)


def normalize_model(m: str) -> str:
    """Collapse codex-gpt-5_4 / codex-gpt-5-4 to a common key."""
    return m.replace("_", "-")


def parse_date(s: str) -> datetime | None:
    s = s.replace("-", "")
    if len(s) != 8:
        return None
    try:
        return datetime.strptime(s, "%Y%m%d")
    except ValueError:
        return None


def discover_eval_dirs() -> list[dict]:
    out = []
    for d in sorted(EVAL_ROOT.glob("C1-*")):
        if not d.is_dir():
            continue
        m = EVAL_DIR_RE.match(d.name)
        if not m:
            logger.debug("skip unrecognized eval dir: %s", d.name)
            continue
        info = m.groupdict()
        out.append({
            "dir": d,
            "name": d.name,
            "model": normalize_model(info["model"]),
            "length": info["length"],
            "date": parse_date(info["date"]),
            "date_str": info["date"],
        })
    return out


def majority_verdict(eval_dir: Path) -> tuple[str | None, dict]:
    """Read all eval_report-*.md and majority-vote PASS/FAIL.

    Returns (verdict, detail) where verdict ∈ {"PASS", "FAIL", None}.
    Tie breaks toward FAIL.
    """
    reports = sorted(eval_dir.glob("eval_report-*.md"))
    votes: list[str] = []
    per_judge: dict[str, str | None] = {}
    for r in reports:
        # judge name from filename
        m = re.match(r"eval_report-(.+)\.md", r.name)
        judge = m.group(1) if m else r.stem
        text = r.read_text(encoding="utf-8", errors="replace")
        m2 = VERDICT_RE.search(text)
        v = m2.group(1).upper() if m2 else None
        per_judge[judge] = v
        if v:
            votes.append(v)
    if not votes:
        return None, {"per_judge": per_judge, "n_pass": 0, "n_fail": 0}
    counts = Counter(votes)
    n_pass = counts.get("PASS", 0)
    n_fail = counts.get("FAIL", 0)
    if n_pass > n_fail:
        verdict = "PASS"
    else:
        verdict = "FAIL"  # ties → FAIL
    return verdict, {"per_judge": per_judge, "n_pass": n_pass, "n_fail": n_fail}


def best_match(session_model: str, session_length: str, session_date: datetime | None,
               evals: list[dict]) -> tuple[dict | None, int]:
    """Pick the eval dir with same (model_family, length) and smallest date diff."""
    candidates = [
        e for e in evals
        if e["model"] == session_model and e["length"] == session_length
    ]
    if not candidates:
        return None, -1
    if session_date is None:
        return candidates[0], -1
    candidates_with_diff = []
    for e in candidates:
        if e["date"] is None:
            candidates_with_diff.append((e, 9999))
        else:
            candidates_with_diff.append((e, abs((e["date"] - session_date).days)))
    candidates_with_diff.sort(key=lambda kv: kv[1])
    return candidates_with_diff[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--max-date-diff", type=int, default=7)
    args = parser.parse_args()

    if not args.index.exists():
        logger.error("index not found: %s — run export_claude_sessions.py first", args.index)
        return 1

    index = json.loads(args.index.read_text(encoding="utf-8"))
    evals = discover_eval_dirs()
    logger.info("loaded %d sessions, %d eval dirs", len(index), len(evals))

    report_lines = ["# C1 eval-signal join report", ""]
    report_lines.append(
        f"Source index: `{args.index}` · eval root: `{EVAL_ROOT}`"
    )
    report_lines.append("")
    report_lines.append("| Session | Eval dir | Date Δ | Majority | n_pass / n_fail | Per-judge |")
    report_lines.append("|---|---|---|---|---|---|")

    for entry in index:
        # session model/length/date come from Stage 1
        model = normalize_model(entry.get("model") or "")
        length = entry.get("length") or ""
        date = parse_date(entry.get("date") or "")

        if not model or not length:
            entry["success"] = None
            entry["eval_join"] = {"status": "no_session_meta"}
            report_lines.append(f"| {entry['session_basename'][-50:]} | — | — | — | — | (no session meta) |")
            continue

        match, diff = best_match(model, length, date, evals)
        if match is None:
            entry["success"] = None
            entry["eval_join"] = {"status": "no_match", "reason": f"no eval dir with model={model} length={length}"}
            report_lines.append(f"| {entry['session_basename'][-50:]} | — | — | — | — | (no match) |")
            continue

        if diff > args.max_date_diff:
            entry["success"] = None
            entry["eval_join"] = {"status": "match_too_far",
                                  "match": match["name"], "diff_days": diff}
            report_lines.append(f"| {entry['session_basename'][-50:]} | {match['name']} | {diff}d | (skip) | — | — |")
            continue

        verdict, detail = majority_verdict(match["dir"])
        success = None if verdict is None else (verdict == "PASS")
        entry["success"] = success
        entry["eval_join"] = {
            "status": "ok",
            "match": match["name"],
            "diff_days": diff,
            "verdict": verdict,
            "n_pass": detail["n_pass"],
            "n_fail": detail["n_fail"],
            "per_judge": detail["per_judge"],
        }

        per_judge_str = ", ".join(
            f"{k}={v or '?'}" for k, v in detail["per_judge"].items()
        )
        report_lines.append(
            f"| {entry['session_basename'][-50:]} | {match['name']} | {diff}d | "
            f"**{verdict}** | {detail['n_pass']} / {detail['n_fail']} | {per_judge_str} |"
        )
        logger.info("[%s] %s ← %s (Δ%dd, n_pass=%d n_fail=%d)",
                    verdict or "?", entry["session_basename"][-40:], match["name"],
                    diff, detail["n_pass"], detail["n_fail"])

    args.index.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("updated %s", args.index)

    # Append a per-session JSON file update with success
    traj_dir = args.index.parent
    for entry in index:
        path = traj_dir / f"{entry['session_basename']}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        data["success"] = entry["success"]
        data["eval_join"] = entry.get("eval_join")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Summary count
    succ = Counter()
    for e in index:
        succ[e["success"]] += 1
    report_lines.append("")
    report_lines.append(f"**Summary**: PASS={succ[True]}, FAIL={succ[False]}, unknown={succ[None]}")
    args.report.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    logger.info("wrote %s", args.report)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
