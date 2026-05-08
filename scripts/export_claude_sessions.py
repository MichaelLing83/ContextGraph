"""Export Claude Code session JSONLs into RawTrajectory-shaped JSON.

Walks ~/.claude/projects/<project_dir> and converts each session JSONL into
a flat RawTrajectory dict that ContextGraph's MemoryWriter / StrategyExtractor
can consume.

Output layout (under --out, default results/c1_experiences/trajectories/):
    <session_dir_basename>.json   # one per session
    _index.json                   # summary across all sessions

Designed for the C1 huawei-eval sessions but works on any Claude Code project
matching --pattern.

Usage:
    uv run python scripts/export_claude_sessions.py --pattern '*huawei-eval-experiment-C1*'
    uv run python scripts/export_claude_sessions.py --session <basename> --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROJECTS_ROOT = Path.home() / ".claude" / "projects"
DEFAULT_OUT = Path("results/c1_experiences/trajectories")

# Per the existing pipeline (see agent_memory/writer.py, parse_chat_trajectory)
OBSERVATION_TRUNCATE = 2000
INPUT_SUMMARY_TRUNCATE = 200


# ---------------------------------------------------------------------------
# Session-name parsing
# ---------------------------------------------------------------------------

_INSTANCE_RE = re.compile(
    r"-Users-zihanwu-Public-codes-(?P<repo>.+?)"
    r"(?:-(?P<task>C\d|M\d|K\d))?"
    r"-(?P<model>claude-opus(?:-max)?|codex-gpt-5-4|cursor-composer2|opencode-glm51)"
    r"-(?P<length>long|short)"
    r"-(?P<date>\d{4}-?\d{2}-?\d{2})$"
)


def parse_session_name(basename: str) -> dict[str, str]:
    """Best-effort decode of the project-dir basename into model / length / date."""
    m = _INSTANCE_RE.match(basename)
    if not m:
        return {"model": "unknown", "length": "unknown", "date": "unknown",
                "repo": basename, "task": ""}
    g = m.groupdict()
    return {
        "model": g["model"],
        "length": g["length"],
        "date": g["date"],
        "task": g.get("task") or "",
        "repo": g.get("repo") or "",
    }


def make_instance_id(basename: str) -> str:
    """Stable instance_id from a project-dir basename."""
    info = parse_session_name(basename)
    parts = [p for p in [info["task"], info["model"], info["length"], info["date"]] if p]
    return "-".join(parts) if parts else basename


# ---------------------------------------------------------------------------
# JSONL → steps
# ---------------------------------------------------------------------------

@dataclass
class Step:
    action: str
    observation: str
    thought: str = ""

    def to_dict(self) -> dict:
        return {"action": self.action, "observation": self.observation, "thought": self.thought}


@dataclass
class TrajectoryExport:
    instance_id: str
    repo: str
    success: Any                 # None until Stage 2 fills it
    problem_statement: str
    steps: list[Step] = field(default_factory=list)
    # bookkeeping
    session_basename: str = ""
    session_id: str = ""
    cwd: str = ""
    model: str = ""
    length: str = ""
    date: str = ""
    raw_record_count: int = 0
    session_type: str = "coding"   # "coding" | "evaluator" | "unknown"
    parse_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["steps"] = [s.to_dict() if isinstance(s, Step) else s for s in self.steps]
        return d


def _truncate(s: str, n: int) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[:n] + "...[truncated]"


def _summarize_input(name: str, inp: dict) -> str:
    """Render `tool_use.input` into a one-line action signature."""
    if not isinstance(inp, dict):
        return f"{name}()"
    # Pick a few salient keys for readability
    parts = []
    salient = ["file_path", "command", "pattern", "path", "old_string",
               "new_string", "query", "url", "description", "name"]
    for k in salient:
        if k in inp and inp[k] is not None:
            v = str(inp[k]).replace("\n", " ")
            parts.append(f"{k}={v[:120]}")
            if len("; ".join(parts)) > INPUT_SUMMARY_TRUNCATE:
                break
    if not parts:
        keys = list(inp.keys())[:4]
        parts = [f"{k}=…" for k in keys]
    return f"{name}(" + "; ".join(parts) + ")"


def _flatten_tool_result_content(c: Any) -> str:
    """tool_result.content can be str or list[{type,text}]."""
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        out = []
        for item in c:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    out.append(str(item.get("text", "")))
                elif item.get("type") == "image":
                    out.append("[image]")
                else:
                    out.append(json.dumps(item, ensure_ascii=False)[:300])
            else:
                out.append(str(item))
        return "\n".join(out)
    return str(c)


def _flatten_user_content(c: Any) -> str:
    """user.message.content can be string OR list of typed blocks."""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        out = []
        for item in c:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    out.append(str(item.get("text", "")))
                elif item.get("type") == "tool_result":
                    out.append(_flatten_tool_result_content(item.get("content")))
        return "\n".join(out)
    return str(c)


def parse_session_jsonl(jsonl_path: Path, basename: str) -> TrajectoryExport:
    """Read a single session JSONL and produce a TrajectoryExport."""
    info = parse_session_name(basename)
    traj = TrajectoryExport(
        instance_id=make_instance_id(basename),
        repo=f"huawei-eval/{info['task']}" if info["task"] else "claude-session",
        success=None,
        problem_statement="",
        session_basename=basename,
        model=info["model"],
        length=info["length"],
        date=info["date"],
    )

    if not jsonl_path.exists():
        traj.parse_warnings.append(f"missing JSONL: {jsonl_path}")
        return traj

    records = []
    for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as e:
            traj.parse_warnings.append(f"bad JSONL line: {e}")
    traj.raw_record_count = len(records)
    if records:
        # cwd, sessionId from the first user record
        for r in records:
            if r.get("type") == "user":
                traj.session_id = r.get("sessionId", "")
                traj.cwd = r.get("cwd", "")
                break

    # 1) problem_statement: first queue-operation enqueue whose content is a real
    # task brief (not a `<task-notification>` ack from a background tool).
    enqueues = [r for r in records if r.get("type") == "queue-operation"
                and r.get("operation") == "enqueue"]
    real_briefs = [r for r in enqueues
                   if not (r.get("content") or "").lstrip().startswith("<task-notification>")]
    if real_briefs:
        traj.problem_statement = (real_briefs[0].get("content") or "").strip()
    elif enqueues:
        traj.problem_statement = (enqueues[0].get("content") or "").strip()
        traj.parse_warnings.append("only task-notification enqueues found; using first")
    else:
        traj.parse_warnings.append("no queue-operation enqueue found")

    # 2) Walk records in order; pair tool_use → tool_result by id
    # First, index tool_results by tool_use_id from user messages
    tool_results: dict[str, str] = {}
    for r in records:
        if r.get("type") != "user":
            continue
        c = r.get("message", {}).get("content")
        if not isinstance(c, list):
            continue
        for item in c:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                tid = item.get("tool_use_id")
                if tid:
                    tool_results[tid] = _flatten_tool_result_content(item.get("content"))
                    if item.get("is_error"):
                        # Tag error inline so MemoryWriter._has_error fires
                        tool_results[tid] = "[ERROR] " + tool_results[tid]

    # Also harvest hook attachments for tool_use_id when stdout/stderr present
    for r in records:
        if r.get("type") != "attachment":
            continue
        att = r.get("attachment") or r.get("message", {}).get("attachment") or {}
        tid = att.get("toolUseID")
        if not tid:
            continue
        # If we already have a tool_result, skip — it's the higher-fidelity source.
        if tid in tool_results and tool_results[tid]:
            continue
        chunks = []
        if att.get("stdout"):
            chunks.append(f"[stdout]\n{att['stdout']}")
        if att.get("stderr"):
            chunks.append(f"[stderr]\n{att['stderr']}")
        if att.get("exitCode") not in (None, 0):
            chunks.append(f"[exitCode={att['exitCode']}]")
        if chunks:
            tool_results[tid] = "\n".join(chunks)

    # Now walk assistant messages → emit Step per tool_use
    for r in records:
        if r.get("type") != "assistant":
            continue
        msg = r.get("message", {})
        content = msg.get("content") or []
        # Collect free-form text/thinking before each tool_use as `thought`
        pending_thought_parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            t = item.get("type")
            if t in ("text", "thinking"):
                txt = item.get("text") if t == "text" else item.get("thinking", "")
                if txt:
                    pending_thought_parts.append(str(txt))
            elif t == "tool_use":
                action = _summarize_input(item.get("name", "tool"), item.get("input") or {})
                obs = tool_results.get(item.get("id", ""), "")
                thought = "\n".join(pending_thought_parts).strip()
                pending_thought_parts = []
                traj.steps.append(Step(
                    action=action,
                    observation=_truncate(obs, OBSERVATION_TRUNCATE),
                    thought=_truncate(thought, OBSERVATION_TRUNCATE),
                ))
        # If there were free-form text blocks but no tool_use in this message,
        # emit a "speak" step so the chain isn't lost
        if pending_thought_parts and (not traj.steps or traj.steps[-1].thought == ""):
            txt = "\n".join(pending_thought_parts).strip()
            if txt:
                traj.steps.append(Step(
                    action="speak()",
                    observation="",
                    thought=_truncate(txt, OBSERVATION_TRUNCATE),
                ))

    if not traj.steps:
        traj.parse_warnings.append("no steps extracted")

    # Classify session type from problem statement
    ps = traj.problem_statement.lstrip()
    lower = ps.lower()
    if not ps:
        traj.session_type = "unknown"
    elif lower.startswith("you are an evaluation agent") or "evaluation agent" in lower[:200]:
        traj.session_type = "evaluator"
    else:
        traj.session_type = "coding"

    return traj


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_sessions(pattern: str, only: str | None = None) -> list[Path]:
    """Find session project dirs matching the glob."""
    matches = sorted(p for p in PROJECTS_ROOT.glob(pattern) if p.is_dir())
    if only:
        matches = [p for p in matches if p.name == only or p.name.endswith(only)]
    return matches


def find_main_jsonl(session_dir: Path) -> Path | None:
    """Return the session-level JSONL if present.

    Claude Code lays out a session as:
        <project_dir>/<sessionUuid>.jsonl   ← the main trajectory we want
        <project_dir>/<sessionUuid>/        ← subagents/, tool-results/
    """
    candidates = [p for p in session_dir.glob("*.jsonl") if p.is_file()]
    if not candidates:
        return None
    # If multiple, pick the one whose stem matches a sibling directory (= main session)
    for p in candidates:
        if (session_dir / p.stem).is_dir():
            return p
    return candidates[0]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default="*huawei-eval-experiment-C1*",
                        help="glob under ~/.claude/projects to match (default: C1 only)")
    parser.add_argument("--session", default=None,
                        help="restrict to a single project-dir basename (suffix match)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="output dir for per-session JSONs and _index.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse only, print summary, don't write files")
    args = parser.parse_args()

    sessions = discover_sessions(args.pattern, args.session)
    if not sessions:
        logger.error("no sessions matched pattern=%s", args.pattern)
        return 1
    logger.info("found %d session dirs", len(sessions))

    args.out.mkdir(parents=True, exist_ok=True)

    index = []
    for session_dir in sessions:
        basename = session_dir.name
        jsonl = find_main_jsonl(session_dir)
        if not jsonl:
            logger.warning("[skip] %s: no main JSONL", basename)
            index.append({
                "session_basename": basename,
                "instance_id": make_instance_id(basename),
                "main_jsonl": None,
                "step_count": 0,
                "raw_record_count": 0,
                "warnings": ["no main JSONL"],
            })
            continue

        traj = parse_session_jsonl(jsonl, basename)
        logger.info("[ok ] %s: steps=%d records=%d warnings=%d",
                    basename, len(traj.steps), traj.raw_record_count,
                    len(traj.parse_warnings))

        if not args.dry_run:
            out_path = args.out / f"{basename}.json"
            out_path.write_text(json.dumps(traj.to_dict(), ensure_ascii=False, indent=2),
                                encoding="utf-8")
        index.append({
            "session_basename": basename,
            "instance_id": traj.instance_id,
            "main_jsonl": str(jsonl.relative_to(PROJECTS_ROOT)),
            "step_count": len(traj.steps),
            "raw_record_count": traj.raw_record_count,
            "model": traj.model,
            "length": traj.length,
            "date": traj.date,
            "cwd": traj.cwd,
            "session_id": traj.session_id,
            "session_type": traj.session_type,
            "success": None,                       # filled by Stage 2
            "warnings": traj.parse_warnings,
        })

    if not args.dry_run:
        (args.out / "_index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        logger.info("wrote %s and %d session JSONs", args.out / "_index.json", len(index))
    else:
        # Pretty-print index for dry-run
        print(json.dumps(index, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
