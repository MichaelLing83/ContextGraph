"""Estimate the 方法级/项目级/框架级/组织级 distribution of PlaybookEntry nodes.

Stratified sample by `prefix`, classify each rule with the same LevelClassifier
used in extract_c1_experiences.py, then extrapolate population proportions to
the full graph.

Output: results/c1_experiences/graph_level_estimate.md (+ .json) summarizing:
  - per-prefix sample counts and level breakdown
  - extrapolated counts for the whole PlaybookEntry population
  - 95% Wilson-score CI on the proportion
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from agent_memory.neo4j_store import Neo4jStore  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LEVELS = ["方法级", "项目级", "框架级", "组织级"]

_LEVEL_PROMPT = """你正在给一组 LLM coding-agent 经验做"抽象层级"分类。

四个层级（一条规则只能选一个最贴的）：
- **方法级**：通用的 debug / test / 工程纪律规则；与具体项目、具体框架无关。
- **项目级**：与某个具体仓库 / 模块 / 算子绑定的修复或导航模式（例：「In Django ORM 中 clone sub-query before set_values()」）。
- **框架级**：和某个生态 / 框架 / 平台绑定的操作知识，但不绑死单个 repo（例：「In Django projects, set DJANGO_SETTINGS_MODULE before scripts」、Ascend / CANN 通用约定）。
- **组织级**：偏团队规范 / 代码审查纪律 / 流程约束（不只是技术规则；例：「Do NOT modify files unrelated to reported bug」）。

下面是一组规则，每条带一个序号。请为每一条输出 `idx` 和 `level`。
只输出一个 JSON array，不要解释。

规则列表：
{rules}
"""


def build_prompt(rules: list[str]) -> str:
    rules_text = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(rules))
    return _LEVEL_PROMPT.format(rules=rules_text)


def parse_response(content: str, n: int) -> list[str]:
    import re
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
            v = item.get("level")
            if v in LEVELS:
                out[item["idx"]] = v
    return [out.get(i + 1, "方法级") for i in range(n)]


def classify_batch(client, model: str, rules: list[str], batch: int = 10) -> list[str]:
    """Classify rules in batches of ``batch``; one LLM call per batch."""
    out = []
    for i in range(0, len(rules), batch):
        chunk = rules[i:i + batch]
        prompt = build_prompt(chunk)
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800, temperature=0.2,
            )
            content = resp.choices[0].message.content or ""
            out.extend(parse_response(content, len(chunk)))
        except Exception as e:
            logger.warning("batch failed: %s", e)
            out.extend(["方法级"] * len(chunk))
    return out


def wilson_ci(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson-score CI on a proportion."""
    if n == 0:
        return 0.0, 1.0
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bolt", default="bolt://localhost:7690",
                        help="default: online instance (has the repo-prefix superset)")
    parser.add_argument("--per-prefix-sample", type=int, default=50)
    parser.add_argument("--batch", type=int, default=10)
    parser.add_argument("--api-base", default=os.environ.get("YUNWU_API_BASE", "https://yunwu.ai") + "/v1")
    parser.add_argument("--api-key", default=os.environ.get("YUNWU_API_KEY"))
    parser.add_argument("--model", default="claude-sonnet-4-20250514")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path,
                        default=PROJECT_ROOT / "results" / "c1_experiences" / "graph_level_estimate")
    args = parser.parse_args()

    if not args.api_key:
        logger.error("missing YUNWU_API_KEY")
        return 1
    random.seed(args.seed)

    pwd = (PROJECT_ROOT / ".env").read_text().split("NEO4J_PASSWORD=")[1].split("\n")[0]
    store = Neo4jStore(uri=args.bolt, auth=("neo4j", pwd))

    # 1) Population counts per prefix
    pop = {r["p"]: r["c"] for r in store.execute_query(
        "MATCH (n:PlaybookEntry) RETURN n.prefix AS p, count(n) AS c ORDER BY c DESC")}
    logger.info("population by prefix: %s (total=%d)", pop, sum(pop.values()))

    # 2) Stratified random sample
    sample_per_prefix: dict[str, list[dict]] = {}
    for prefix, count in pop.items():
        n_sample = min(args.per_prefix_sample, count)
        rows = store.execute_query(
            """
            MATCH (n:PlaybookEntry {prefix: $prefix})
            RETURN n.id AS id, n.text AS text
            """,
            {"prefix": prefix},
        )
        sampled = random.sample(rows, n_sample) if len(rows) > n_sample else rows
        sample_per_prefix[prefix] = sampled
        logger.info("[%s] population=%d, sampled=%d", prefix, count, n_sample)

    store.close()

    # 3) Classify
    from openai import OpenAI
    client = OpenAI(base_url=args.api_base.rstrip("/"), api_key=args.api_key)
    by_prefix_levels: dict[str, list[str]] = {}
    detail_records = []
    for prefix, sampled in sample_per_prefix.items():
        rules = [r["text"] for r in sampled]
        levels = classify_batch(client, args.model, rules, batch=args.batch)
        by_prefix_levels[prefix] = levels
        for r, lvl in zip(sampled, levels):
            detail_records.append({
                "prefix": prefix, "id": r["id"], "level": lvl,
                "text": (r["text"] or "")[:200],
            })
        logger.info("[%s] level dist: %s", prefix, dict(Counter(levels)))

    # 4) Extrapolate
    total_pop = sum(pop.values())
    extrapolation = []
    overall_counts = Counter()
    for prefix, count in pop.items():
        sample_levels = by_prefix_levels.get(prefix, [])
        n_sample = len(sample_levels)
        c = Counter(sample_levels)
        row = {"prefix": prefix, "population": count, "sample_size": n_sample}
        for lvl in LEVELS:
            p_hat = c.get(lvl, 0) / n_sample if n_sample else 0.0
            lo, hi = wilson_ci(p_hat, n_sample)
            est = round(p_hat * count)
            est_lo = round(lo * count)
            est_hi = round(hi * count)
            row[f"sample_{lvl}"] = c.get(lvl, 0)
            row[f"prop_{lvl}"] = round(p_hat, 3)
            row[f"est_{lvl}"] = est
            row[f"est_{lvl}_ci"] = [est_lo, est_hi]
            overall_counts[lvl] += est
        extrapolation.append(row)

    # 5) Render
    args.out.parent.mkdir(parents=True, exist_ok=True)

    full = {
        "bolt": args.bolt, "seed": args.seed,
        "population_by_prefix": pop,
        "total_population": total_pop,
        "per_prefix": extrapolation,
        "extrapolated_total": dict(overall_counts),
        "sample_details": detail_records,
    }
    args.out.with_suffix(".json").write_text(
        json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    md = ["# PlaybookEntry 层级分布估计（Neo4j context graph）", "",
          f"- Bolt: `{args.bolt}`",
          f"- 总数 PlaybookEntry: **{total_pop}**",
          f"- 采样规则: 每 prefix 最多 {args.per_prefix_sample} 条 (seed={args.seed})",
          f"- 分类器: LLM (`{args.model}`)，与 C1 经验同一套 4 层级 prompt",
          "",
          "## 每 prefix 采样分布", "",
          "| prefix | 总量 | 样本 | 方法级 | 项目级 | 框架级 | 组织级 |",
          "|---|---|---|---|---|---|---|"]
    for row in extrapolation:
        md.append(
            f"| `{row['prefix']}` | {row['population']} | {row['sample_size']} | "
            + " | ".join(
                f"{row[f'sample_{l}']} ({row[f'prop_{l}']*100:.0f}%)"
                for l in LEVELS
            )
            + " |"
        )

    md += ["", "## 推断到全图的计数（含 95% Wilson CI）", "",
           "| prefix | 方法级 | 项目级 | 框架级 | 组织级 |",
           "|---|---|---|---|---|"]
    for row in extrapolation:
        md.append(
            f"| `{row['prefix']}` | "
            + " | ".join(
                f"{row[f'est_{l}']} ([{row[f'est_{l}_ci'][0]}, {row[f'est_{l}_ci'][1]}])"
                for l in LEVELS
            )
            + " |"
        )

    md += ["", "## 全图合计（点估计）", "",
           f"- **方法级**: {overall_counts['方法级']}",
           f"- **项目级**: {overall_counts['项目级']}",
           f"- **框架级**: {overall_counts['框架级']}",
           f"- **组织级**: {overall_counts['组织级']}",
           f"- 合计: {sum(overall_counts.values())} (population: {total_pop})"]
    args.out.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    logger.info("wrote %s.md and %s.json", args.out, args.out)

    # Stdout summary
    print(f"\nPopulation: {total_pop}")
    print(f"Estimated by level (extrapolated):")
    for l in LEVELS:
        print(f"  {l}: {overall_counts[l]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
