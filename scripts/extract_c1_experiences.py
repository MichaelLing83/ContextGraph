"""Stage 3: extract experiences from exported C1 trajectories with dual-axis labels.

Reads trajectories from `results/c1_experiences/trajectories/<basename>.json`
(produced by export_claude_sessions.py) and runs:

  Axis 1 (category) — reuses agent_memory.strategy_extractor.StrategyExtractor.
                      Categories ∈ {error_handling, debugging, testing,
                      code_navigation, dependency, configuration, anti_pattern}.

  Axis 2 (level)    — new LevelClassifier defined here. Categories ∈
                      {方法级, 项目级, 框架级, 组织级} as defined in
                      paper/knowledge_levels.md, plus a `binds_cann` boolean.

Output (under --out, default results/c1_experiences/):
    experiences.json   — flat array of {id, rule_text, category, level,
                          level_reason, binds_cann, source_session, ...}
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Project bootstrap
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from agent_memory.strategy_extractor import StrategyExtractor  # noqa: E402
from agent_memory.writer import MemoryWriter  # noqa: E402


_RICH_PROMPT = """You are extracting reusable, transferable rules from a coding agent's session.

The agent worked on a real software task and produced this trajectory. Read the action transcript carefully — the agent's tool calls, intermediate thoughts, and observed outputs are all real evidence of what worked or didn't.

Repository: {repo}
Task brief: {problem_statement}
Outcome: {outcome} after {n_steps} steps
Error signals seen in observations: {error_types}

Action transcript (truncated):
----
{action_summary}
----

Extract 4–6 reusable rules from this trajectory. Each rule should be:
- Abstract — DO NOT reference specific file names, variable names, or function signatures from this task. Generalize.
- Actionable — tell a future agent what to do or avoid in similar situations.
- Single sentence.
- Grounded in evidence from the transcript above (do not invent rules unrelated to what happened).

Format each rule on its own line as: [category] Rule text
Categories: error_handling, debugging, testing, code_navigation, dependency, configuration, anti_pattern

Output ONLY the rule lines — no preamble, no numbering, no markdown.

Rules:"""


_LINE_FORMATS = [
    re.compile(r"^\[(\w+)\]\s+(.+)$"),       # [category] text
    re.compile(r"^(\w+):\s+(.+)$"),           # category: text
    re.compile(r"^(\w+)\s+(.+)$"),            # category text  (fallback)
]


class RichStrategyExtractor(StrategyExtractor):
    """StrategyExtractor with a transcript-aware prompt suitable for Claude Code sessions."""

    def _build_prompt(self, data: dict) -> str:
        return _RICH_PROMPT.format(
            repo=data.get("repo", "unknown"),
            problem_statement=(data.get("problem_statement") or "")[:600] or "no description",
            outcome="succeeded" if data.get("success") else "failed/unknown",
            n_steps=data.get("total_steps", 0),
            error_types=", ".join(data.get("error_types") or []) or "none",
            action_summary=data.get("action_summary") or "(transcript missing)",
        )

    def _parse_response(self, content, data):
        """Override: accept `[cat] text`, `cat: text`, and `cat text` forms."""
        from agent_memory.strategy_extractor import _VALID_CATEGORIES
        from agent_memory.models import Strategy
        strategies = []
        trajectory_id = data.get("trajectory_id", "")
        repo = data.get("repo", "")
        for line in content.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            line = re.sub(r"^[\d]+[.)]\s*", "", line)
            line = re.sub(r"^[-*]\s*", "", line)
            cat, rule_text = None, None
            for rx in _LINE_FORMATS:
                m = rx.match(line)
                if not m:
                    continue
                candidate = m.group(1).lower()
                if candidate in _VALID_CATEGORIES:
                    cat, rule_text = candidate, m.group(2).strip()
                    break
            if not cat or not rule_text or len(rule_text) < 15:
                continue
            strategies.append(Strategy(
                id=f"strat_{uuid.uuid4().hex[:12]}",
                rule_text=rule_text,
                category=cat,
                source_trajectory_id=trajectory_id,
                source_repo=repo,
                confidence=0.8 if data.get("success") else 0.5,
            ))
        return strategies

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_TRAJ_DIR = PROJECT_ROOT / "results" / "c1_experiences" / "trajectories"
DEFAULT_OUT = PROJECT_ROOT / "results" / "c1_experiences"

# Allowed levels — must match knowledge_levels.md
LEVELS = ["方法级", "项目级", "框架级", "组织级"]


# ---------------------------------------------------------------------------
# Helpers — derive (error_types, action_summary) from steps
# ---------------------------------------------------------------------------

# Reuse MemoryWriter's pure helpers without instantiating Neo4j store
_helper = MemoryWriter(store=None, embedder=None)


def derive_error_types(steps: list[dict]) -> list[str]:
    """Run MemoryWriter._extract_error_patterns on observations and return types."""
    observations = [s.get("observation", "") for s in steps]
    patterns = _helper._extract_error_patterns(observations)
    # de-dupe in observation order
    seen = set()
    out = []
    for p in patterns:
        if p.error_type and p.error_type not in seen:
            seen.add(p.error_type)
            out.append(p.error_type)
    return out


def derive_action_summary(steps: list[dict], max_chars: int = 3500) -> str:
    """Build a rich-but-bounded transcript: each step's action + a short
    observation/thought excerpt. The default 3500-char budget is enough for the
    LLM to reason about what actually happened, while still leaving room for
    the rest of the prompt template.
    """
    out: list[str] = []
    used = 0
    for i, s in enumerate(steps):
        action = (s.get("action") or "").strip()
        thought = (s.get("thought") or "").strip().replace("\n", " ")[:160]
        obs = (s.get("observation") or "").strip().replace("\n", " ")[:240]
        line_parts = [f"step {i + 1}: {action}"]
        if thought:
            line_parts.append(f"  thought: {thought}")
        if obs:
            line_parts.append(f"  obs: {obs}")
        line = "\n".join(line_parts)
        if used + len(line) > max_chars:
            out.append("... [more steps truncated]")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def derive_fragments(steps: list[dict]) -> list[dict]:
    """Use MemoryWriter._segment_into_fragments — but it needs a RawTrajectory."""
    from agent_memory.writer import RawTrajectory
    raw = RawTrajectory(
        instance_id="_tmp", repo="_tmp", success=True,
        steps=steps, problem_statement="",
    )
    fragments = _helper._segment_into_fragments(raw)
    return [
        {"description": f.description, "fragment_type": f.fragment_type,
         "outcome": f.outcome, "n_actions": len(f.action_sequence)}
        for f in fragments
    ]


# ---------------------------------------------------------------------------
# LevelClassifier — Axis 2 abstraction-level classifier
# ---------------------------------------------------------------------------

_LEVEL_PROMPT = """你正在给一组 LLM coding-agent 经验做"抽象层级"分类。

四个层级的定义和示例（来自 paper/knowledge_levels.md）：

- **方法级**：通用的 debug / test / 工程纪律规则；与具体项目、具体框架无关。
  例：「Run targeted test files rather than full test suites」「Fix root cause, not symptom」。
- **项目级**：与某个具体仓库 / 算子 / 模块绑定的修复或导航模式。
  例：「In Django ORM combined query, clone sub-query before set_values()」、「DVC CLI help text 在 dvc/commands/<sub>.py」。
- **框架级**：和某个生态 / 框架 / 平台绑定的操作知识，但不绑死单个 repo。
  例：「In Django projects, set DJANGO_SETTINGS_MODULE before executing scripts」、Ascend / CANN / CUDA 通用约定。
- **组织级**：偏团队规范 / 代码审查纪律 / 流程约束（不只是技术规则）。
  例：「Do NOT modify files unrelated to reported bug」「Patch hygiene: 不要混入兼容性 fix」。

任务上下文：仓库 `{repo}`；任务摘要：{task_brief}

下面是从这条 trajectory 提取出的若干经验，每条带一个序号。请为**每一条**输出一个 JSON 对象，字段：
- `idx` (int)
- `level` (one of: {levels})
- `level_reason` (一句话理由，中文，<= 40 字)
- `binds_cann` (bool；是否依赖 Huawei CANN / Ascend / __aicore__ / FreeTensor 等专有概念)

只输出一个 JSON array，不要解释，不要 markdown 代码块。

经验列表：
{rules}
"""


def build_level_prompt(rules: list[str], repo: str, task_brief: str) -> str:
    rules_text = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(rules))
    return _LEVEL_PROMPT.format(
        repo=repo,
        task_brief=task_brief[:200] or "(none)",
        levels=" / ".join(LEVELS),
        rules=rules_text,
    )


def parse_level_response(content: str, n: int) -> list[dict]:
    """Parse the LLM JSON-array reply. Robust to ```json fences."""
    # Strip code fences if any
    s = content.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    # Sometimes the LLM prefixes "Here is the result:" — find first '['
    start = s.find("[")
    end = s.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"no JSON array found in: {content[:200]}")
    s = s[start:end + 1]
    arr = json.loads(s)

    out: dict[int, dict] = {}
    for item in arr:
        if not isinstance(item, dict):
            continue
        idx = item.get("idx")
        level = item.get("level")
        if not isinstance(idx, int) or level not in LEVELS:
            continue
        out[idx] = {
            "level": level,
            "level_reason": str(item.get("level_reason", ""))[:120],
            "binds_cann": bool(item.get("binds_cann", False)),
        }
    # Fill any missing rules with defaults
    result = []
    for i in range(1, n + 1):
        if i in out:
            result.append(out[i])
        else:
            result.append({"level": "方法级", "level_reason": "(missing — fallback)", "binds_cann": False})
    return result


class LevelClassifier:
    """Calls the same LiteLLM-or-OpenAI-compatible endpoint as StrategyExtractor."""

    def __init__(self, api_base: str, api_key: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(base_url=api_base, api_key=api_key)
        self.model = model

    def classify(self, rules: list[str], repo: str, task_brief: str) -> list[dict]:
        if not rules:
            return []
        prompt = build_level_prompt(rules, repo, task_brief)
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.2,
        )
        content = resp.choices[0].message.content or ""
        try:
            return parse_level_response(content, len(rules))
        except Exception as e:
            logger.warning("level parse failed: %s; raw[:200]=%s", e, content[:200])
            return [{"level": "方法级",
                     "level_reason": f"(parse failure: {e})",
                     "binds_cann": False} for _ in rules]


# ---------------------------------------------------------------------------
# Main per-trajectory pipeline
# ---------------------------------------------------------------------------

def process_trajectory(traj: dict, extractor: StrategyExtractor,
                        classifier: LevelClassifier) -> list[dict]:
    steps = traj.get("steps", []) or []
    repo = traj.get("repo") or ""
    instance_id = traj.get("instance_id") or "?"
    session_basename = traj.get("session_basename") or ""
    success = traj.get("success")  # may be None
    problem = traj.get("problem_statement") or ""

    error_types = derive_error_types(steps)
    action_summary = derive_action_summary(steps)
    fragments = derive_fragments(steps)

    trajectory_data = {
        "trajectory_id": instance_id,
        "repo": repo,
        "problem_statement": problem,
        "success": bool(success) if success is not None else False,  # extractor uses for confidence
        "total_steps": len(steps),
        "error_types": error_types,
        "action_summary": action_summary,
        "fragments": fragments,
    }

    strategies = extractor.extract(trajectory_data)
    if not strategies:
        logger.warning("[%s] no strategies extracted", instance_id)
        return []

    # Axis 2: level classification (batch)
    rule_texts = [s.rule_text for s in strategies]
    levels = classifier.classify(rule_texts, repo=repo, task_brief=problem)

    session_type = traj.get("session_type", "coding")
    out = []
    for i, (strat, lvl) in enumerate(zip(strategies, levels)):
        out.append({
            "id": f"c1-{session_basename[-30:]}-{i:02d}",
            "rule_text": strat.rule_text,
            "category": strat.category,
            "level": lvl["level"],
            "level_reason": lvl["level_reason"],
            "binds_cann": lvl["binds_cann"],
            "source_session": session_basename,
            "source_instance_id": instance_id,
            "source_success": success,                # may be None
            "source_session_type": session_type,      # "coding" | "evaluator"
            "confidence": strat.confidence,
            "model": traj.get("model"),
            "length": traj.get("length"),
            "date": traj.get("date"),
        })
    logger.info("[%s] %d rules (success=%s)", instance_id, len(out), success)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--traj-dir", type=Path, default=DEFAULT_TRAJ_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT / "experiences.json")
    parser.add_argument("--session", default=None,
                        help="restrict to one session basename (suffix match)")
    parser.add_argument("--model",
                        default=os.environ.get("CG_EXTRACT_MODEL", "claude-sonnet-4-20250514"))
    parser.add_argument("--api-base",
                        default=os.environ.get("OPENAI_API_BASE", "https://api.chatanywhere.org/v1"))
    parser.add_argument("--api-key",
                        default=os.environ.get("OPENAI_API_KEY"))
    parser.add_argument("--limit-rules", type=int, default=None,
                        help="(debug) keep only first N strategies per session")
    parser.add_argument("--delay", type=float, default=0.4,
                        help="sleep seconds between LLM calls")
    args = parser.parse_args()

    if not args.api_key:
        logger.error("missing OPENAI_API_KEY (in .env or env)")
        return 1
    # Normalize base: chatanywhere's docs say /v1 path
    api_base = args.api_base.rstrip("/")
    if not api_base.endswith("/v1"):
        api_base = api_base + "/v1"

    extractor = RichStrategyExtractor(api_base=api_base, api_key=args.api_key, model=args.model)
    classifier = LevelClassifier(api_base=api_base, api_key=args.api_key, model=args.model)

    # Load index
    index_path = args.traj_dir / "_index.json"
    if not index_path.exists():
        logger.error("no _index.json under %s — run export_claude_sessions.py first", args.traj_dir)
        return 1
    index = json.loads(index_path.read_text(encoding="utf-8"))

    all_experiences: list[dict] = []
    for entry in index:
        basename = entry["session_basename"]
        if args.session and not basename.endswith(args.session):
            continue
        path = args.traj_dir / f"{basename}.json"
        if not path.exists():
            logger.warning("skip %s (no per-session JSON)", basename)
            continue
        traj = json.loads(path.read_text(encoding="utf-8"))
        if not traj.get("steps"):
            logger.warning("skip %s (no steps)", basename)
            continue

        try:
            exps = process_trajectory(traj, extractor, classifier)
        except Exception as e:
            logger.exception("[%s] processing failed: %s", basename, e)
            exps = []

        if args.limit_rules is not None:
            exps = exps[:args.limit_rules]
        all_experiences.extend(exps)
        time.sleep(args.delay)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(all_experiences, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("wrote %d experiences to %s", len(all_experiences), args.out)

    # Summary table
    from collections import Counter
    cats = Counter(e["category"] for e in all_experiences)
    lvls = Counter(e["level"] for e in all_experiences)
    cann = Counter(e["binds_cann"] for e in all_experiences)
    print(f"\nCategories: {dict(cats)}")
    print(f"Levels:     {dict(lvls)}")
    print(f"binds_cann: {dict(cann)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
