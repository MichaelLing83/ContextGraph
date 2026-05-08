"""Stage 4: render experiences.md and summary.md from experiences.json.

experiences.md layout (flat — every rule on one row):
    | # | Rule | category | 层级 | SE阶段 | binds_cann | source | success | type |

Plus a per-(层级 × SE阶段) cross-tab at the top for quick scanning.

summary.md gives per-session counts (steps / extracted rules / level / category /
se_phase distributions).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

LEVELS = ["方法级", "项目级", "框架级", "组织级"]
CATEGORY_ORDER = ["error_handling", "debugging", "testing", "code_navigation",
                  "dependency", "configuration", "anti_pattern"]
SE_PHASES = ["设计", "编码", "构建", "测试", "调试", "评审"]

DEFAULT_OUT = Path("results/c1_experiences")


def _esc(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def render_experiences_md(exps: list[dict]) -> str:
    n_sessions = len({e["source_session"] for e in exps})
    lines = [
        "# C1 sessions — extracted experiences",
        "",
        f"Total: **{len(exps)}** experiences from {n_sessions} sessions.",
        "",
        "## 层级 × SE-阶段 cross-tab",
        "",
    ]

    # cross-tab
    cell: dict[tuple[str, str], int] = defaultdict(int)
    for e in exps:
        cell[(e.get("level", "?"), e.get("se_phase", "?"))] += 1

    header = ["层级 \\\\ SE阶段"] + SE_PHASES + ["合计"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    col_total = {p: 0 for p in SE_PHASES}
    for lvl in LEVELS:
        row_total = 0
        row_cells = []
        for ph in SE_PHASES:
            v = cell.get((lvl, ph), 0)
            col_total[ph] += v
            row_total += v
            row_cells.append(str(v) if v else "·")
        if row_total == 0:
            continue
        lines.append("| **" + lvl + "** | " + " | ".join(row_cells) + f" | **{row_total}** |")
    grand = sum(col_total.values())
    lines.append("| **合计** | " + " | ".join(f"**{col_total[p]}**" for p in SE_PHASES) + f" | **{grand}** |")
    lines.append("")

    # Flat table
    lines.append("## All experiences (flat)")
    lines.append("")
    lines.append("| # | Rule | category | 层级 | SE阶段 | binds_cann | source | success | type |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    # Sort: level (per LEVELS), then se_phase (per SE_PHASES), then category
    lvl_order = {l: i for i, l in enumerate(LEVELS)}
    ph_order = {p: i for i, p in enumerate(SE_PHASES)}
    cat_order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    sorted_exps = sorted(
        exps,
        key=lambda e: (
            lvl_order.get(e.get("level", "?"), 99),
            ph_order.get(e.get("se_phase", "?"), 99),
            cat_order.get(e.get("category", "?"), 99),
            e.get("rule_text", ""),
        ),
    )
    succ_glyph = {True: "✅", False: "❌", None: "—"}
    for i, e in enumerate(sorted_exps, 1):
        lines.append(
            f"| {i} | {_esc(e['rule_text'])} | `{e.get('category','?')}` | "
            f"**{e.get('level','?')}** | **{e.get('se_phase','?')}** | "
            f"{'✅' if e.get('binds_cann') else '—'} | "
            f"`…{e.get('source_session','')[-40:]}` | "
            f"{succ_glyph.get(e.get('source_success'),'?')} | "
            f"{e.get('source_session_type','?')} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def render_summary_md(exps: list[dict], index: list[dict]) -> str:
    by_session: dict[str, list[dict]] = defaultdict(list)
    for e in exps:
        by_session[e["source_session"]].append(e)

    lines = ["# C1 experience extraction — per-session summary", ""]
    lines.append("| Session | Type | Success | Steps | Rules | Levels | SE-阶段 | Categories | binds_cann |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for entry in index:
        basename = entry["session_basename"]
        rules = by_session.get(basename, [])
        lvls = Counter(r["level"] for r in rules)
        phs = Counter(r.get("se_phase") for r in rules if r.get("se_phase"))
        cats = Counter(r["category"] for r in rules)
        cann = sum(1 for r in rules if r.get("binds_cann"))
        succ = {True: "✅PASS", False: "❌FAIL", None: "—"}[entry.get("success")]
        lines.append(
            f"| …{basename[-44:]} | {entry.get('session_type','?')} | {succ} | "
            f"{entry['step_count']} | {len(rules)} | "
            f"{', '.join(f'{k}:{v}' for k, v in lvls.most_common()) or '—'} | "
            f"{', '.join(f'{k}:{v}' for k, v in phs.most_common()) or '—'} | "
            f"{', '.join(f'{k}:{v}' for k, v in cats.most_common()) or '—'} | "
            f"{cann} |"
        )
    lines.append("")

    lines.append("## Aggregates")
    lines.append("")
    lines.append(f"- **Total experiences**: {len(exps)}")
    lines.append(f"- **By 层级**: {dict(Counter(e['level'] for e in exps))}")
    lines.append(f"- **By SE-阶段**: {dict(Counter(e.get('se_phase') for e in exps))}")
    lines.append(f"- **By category**: {dict(Counter(e['category'] for e in exps))}")
    lines.append(f"- **binds_cann=true**: {sum(1 for e in exps if e.get('binds_cann'))} / {len(exps)}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in-json", type=Path, default=DEFAULT_OUT / "experiences.json")
    parser.add_argument("--index", type=Path, default=DEFAULT_OUT / "trajectories" / "_index.json")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    exps = json.loads(args.in_json.read_text(encoding="utf-8"))
    index = json.loads(args.index.read_text(encoding="utf-8"))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "experiences.md").write_text(render_experiences_md(exps), encoding="utf-8")
    (args.out_dir / "summary.md").write_text(render_summary_md(exps, index), encoding="utf-8")
    print(f"wrote {args.out_dir / 'experiences.md'} and {args.out_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
