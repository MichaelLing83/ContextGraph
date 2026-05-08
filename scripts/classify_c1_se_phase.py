"""Add a third axis to existing C1 experiences: SE-phase ∈ {设计, 编码, 构建, 测试, 调试, 评审}.

Reads `experiences.json`, batches per source_session, runs one LLM call per
session to classify each rule's `se_phase`, writes the field back into the
same file. Idempotent — re-running keeps existing values.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_FILE = PROJECT_ROOT / "results" / "c1_experiences" / "experiences.json"

SE_PHASES = ["设计", "编码", "构建", "测试", "调试", "评审"]

_PROMPT = """你正在给一组 LLM coding-agent 经验做"软件工程阶段"分类。

六个阶段（经典 SDLC，一条规则只选一个最贴的）：
- **设计**：架构 / 接口 / 数据流 / 抽象层级的决策；事前规划、画依赖、定接口。
- **编码**：实际写代码、做编辑、改函数体；代码导航、定位修改点也算。
- **构建**：编译、链接、依赖管理、打包、环境配置。
- **测试**：单元 / 集成 / 端到端测试的设计和执行；测试用例选择、断言写法。
- **调试**：根因定位、复现、加日志 / 跟踪、错误诊断、loop 摆脱、验证 hypothesis。
- **评审**：补丁卫生（patch hygiene）、覆盖率核对、对比 ground truth、评估其它 agent 的产出、流程合规。

任务上下文：仓库 `{repo}`；任务摘要：{task_brief}

下面是从这条 trajectory 提取出的若干经验，每条带一个序号。请为**每一条**输出一个 JSON 对象：
- `idx` (int)
- `se_phase` (six options: {phases})

只输出一个 JSON array，不要解释，不要 markdown 代码块。

经验列表：
{rules}
"""


def build_prompt(rules: list[str], repo: str, task_brief: str) -> str:
    rules_text = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(rules))
    return _PROMPT.format(
        repo=repo, task_brief=task_brief[:200] or "(none)",
        phases=" / ".join(SE_PHASES), rules=rules_text,
    )


def parse_response(content: str, n: int) -> list[str]:
    s = content.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    start, end = s.find("["), s.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON array in: {content[:200]}")
    arr = json.loads(s[start:end + 1])
    out = {}
    for item in arr:
        if isinstance(item, dict) and isinstance(item.get("idx"), int):
            v = item.get("se_phase")
            if v in SE_PHASES:
                out[item["idx"]] = v
    return [out.get(i + 1, "调试") for i in range(n)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument("--api-base",
                        default=os.environ.get("YUNWU_API_BASE_V1", "https://yunwu.ai/v1"))
    parser.add_argument("--api-key", default=os.environ.get("YUNWU_API_KEY"))
    parser.add_argument("--model", default="claude-sonnet-4-20250514")
    parser.add_argument("--force", action="store_true",
                        help="re-classify even if se_phase already set")
    parser.add_argument("--task-brief-from-traj", action="store_true", default=True,
                        help="(default) pull task_brief per session from trajectories/<basename>.json")
    args = parser.parse_args()

    if not args.api_key:
        logger.error("missing YUNWU_API_KEY (in .env)")
        return 1

    from openai import OpenAI
    client = OpenAI(base_url=args.api_base.rstrip("/"), api_key=args.api_key)

    exps = json.loads(args.file.read_text(encoding="utf-8"))
    by_session: dict[str, list[dict]] = defaultdict(list)
    for e in exps:
        by_session[e["source_session"]].append(e)

    traj_dir = args.file.parent / "trajectories"

    for sess, rules_in_sess in by_session.items():
        # Skip if already classified and not --force
        if not args.force and all(e.get("se_phase") in SE_PHASES for e in rules_in_sess):
            logger.info("[skip] %s (already classified)", sess[-40:])
            continue

        # Load task brief from trajectory file
        repo = rules_in_sess[0].get("repo") or "huawei-eval/C1"
        task_brief = ""
        traj_path = traj_dir / f"{sess}.json"
        if traj_path.exists():
            t = json.loads(traj_path.read_text(encoding="utf-8"))
            repo = t.get("repo", repo)
            task_brief = t.get("problem_statement", "")

        prompt = build_prompt(
            [e["rule_text"] for e in rules_in_sess],
            repo=repo, task_brief=task_brief,
        )
        resp = client.chat.completions.create(
            model=args.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500, temperature=0.2,
        )
        content = resp.choices[0].message.content or ""
        try:
            phases = parse_response(content, len(rules_in_sess))
        except Exception as e:
            logger.warning("[parse-fail] %s: %s", sess[-40:], e)
            phases = ["调试"] * len(rules_in_sess)
        for rule, phase in zip(rules_in_sess, phases):
            rule["se_phase"] = phase
        logger.info("[%s] %d rules → %s", sess[-30:], len(rules_in_sess),
                    ", ".join(set(phases)))

    args.file.write_text(json.dumps(exps, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("updated %s", args.file)

    from collections import Counter
    print(f"\nSE phases: {dict(Counter(e.get('se_phase') for e in exps))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
