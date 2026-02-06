"""
实验 1：循环检测与量化分析
对 3,591 条 SWE-agent 轨迹进行全量循环模式分析
"""

import json
import re
from pathlib import Path
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
import statistics

# ============== 动作提取 ==============

def extract_command_from_ai_text(text: str) -> Optional[str]:
    """从 AI 响应文本中提取命令"""
    if not text:
        return None
    # SWE-agent 格式：命令在 ``` 代码块中
    match = re.search(r'```\n?([^\n]+)', text)
    if match:
        return match.group(1).strip()
    return None

def classify_action(command: str) -> Tuple[str, Optional[str]]:
    """
    将命令分类为动作类型，并提取目标
    返回: (action_type, target)
    """
    if not command:
        return ("unknown", None)

    command = command.strip()

    # 搜索类动作
    if command.startswith("search_dir"):
        match = re.search(r'search_dir\s+"([^"]+)"', command)
        term = match.group(1) if match else None
        return ("search", term)

    if command.startswith("search_file"):
        match = re.search(r'search_file\s+"([^"]+)"', command)
        term = match.group(1) if match else None
        return ("search", term)

    if command.startswith("find_file"):
        match = re.search(r'find_file\s+"([^"]+)"', command)
        term = match.group(1) if match else None
        return ("search", term)

    if command.startswith("grep") or command.startswith("rg"):
        return ("search", None)

    if command.startswith("find "):
        return ("search", None)

    # 文件打开/导航
    if command.startswith("open"):
        match = re.search(r'open\s+(\S+)', command)
        target = match.group(1) if match else None
        return ("open", target)

    if command.startswith("goto"):
        return ("navigate", None)

    if command.startswith("scroll_"):
        return ("navigate", None)

    # 编辑类动作
    if command.startswith("edit"):
        return ("edit", None)

    # 创建文件
    if command.startswith("create"):
        match = re.search(r'create\s+(\S+)', command)
        target = match.group(1) if match else None
        return ("create", target)

    # 执行/测试
    if command.startswith("python"):
        match = re.search(r'python\s+(\S+)', command)
        target = match.group(1) if match else None
        return ("execute", target)

    if command.startswith("bash"):
        return ("execute", None)

    if "test" in command.lower() or "pytest" in command:
        return ("test", None)

    # 提交
    if command == "submit":
        return ("submit", None)

    # 系统命令
    if command.startswith("ls"):
        return ("list", None)

    if command.startswith("cat"):
        match = re.search(r'cat\s+(\S+)', command)
        target = match.group(1) if match else None
        return ("read", target)

    if command.startswith("cd"):
        return ("navigate", None)

    if command.startswith("pip"):
        return ("install", None)

    if command.startswith("rm"):
        return ("delete", None)

    return ("other", None)


# ============== 循环检测 ==============

@dataclass
class Loop:
    """表示一个检测到的循环"""
    start_step: int           # 循环开始的步骤
    length: int               # 循环长度（重复次数）
    pattern: List[str]        # 循环的动作模式
    pattern_type: str         # 循环类型：exact_command / action_type / action_target
    commands: List[str]       # 实际命令
    recovered: bool = False   # 是否从循环中恢复

@dataclass
class TrajectoryAnalysis:
    """单条轨迹的分析结果"""
    instance_id: str
    success: bool
    total_steps: int
    ai_steps: int
    loops: List[Loop] = field(default_factory=list)
    action_sequence: List[str] = field(default_factory=list)
    command_sequence: List[str] = field(default_factory=list)


def detect_loops(commands: List[str], actions: List[Tuple[str, Optional[str]]],
                 min_repeat: int = 3) -> List[Loop]:
    """
    检测轨迹中的循环模式

    检测三种类型的循环：
    1. exact_command: 完全相同的命令重复
    2. action_type: 相同动作类型重复（如 search → search → search）
    3. action_target: 相同动作类型+相同目标重复
    """
    loops = []
    n = len(commands)

    if n < min_repeat:
        return loops

    # 1. 检测完全相同命令的循环
    i = 0
    while i < n:
        count = 1
        while i + count < n and commands[i] == commands[i + count]:
            count += 1
        if count >= min_repeat:
            loops.append(Loop(
                start_step=i,
                length=count,
                pattern=[commands[i]],
                pattern_type="exact_command",
                commands=commands[i:i+count]
            ))
        i += count

    # 2. 检测动作类型循环
    action_types = [a[0] for a in actions]
    i = 0
    while i < len(action_types):
        count = 1
        while i + count < len(action_types) and action_types[i] == action_types[i + count]:
            count += 1
        if count >= min_repeat:
            # 避免与 exact_command 重复计数
            is_exact = any(l.start_step == i and l.pattern_type == "exact_command"
                          for l in loops)
            if not is_exact:
                loops.append(Loop(
                    start_step=i,
                    length=count,
                    pattern=[action_types[i]],
                    pattern_type="action_type",
                    commands=commands[i:i+count] if i+count <= len(commands) else []
                ))
        i += count

    # 3. 检测动作+目标循环（更严格）
    action_targets = [(a[0], a[1]) for a in actions]
    i = 0
    while i < len(action_targets):
        if action_targets[i][1] is None:  # 没有目标的跳过
            i += 1
            continue
        count = 1
        while (i + count < len(action_targets) and
               action_targets[i] == action_targets[i + count]):
            count += 1
        if count >= min_repeat:
            loops.append(Loop(
                start_step=i,
                length=count,
                pattern=[f"{action_targets[i][0]}:{action_targets[i][1]}"],
                pattern_type="action_target",
                commands=commands[i:i+count] if i+count <= len(commands) else []
            ))
        i += count

    return loops


def check_recovery(loop: Loop, subsequent_actions: List[str],
                   subsequent_commands: List[str]) -> bool:
    """
    检查 agent 是否从循环中恢复
    恢复定义：循环后执行了不同类型的动作，且该动作不是立即失败
    """
    if not subsequent_actions:
        return False

    loop_action = loop.pattern[0].split(":")[0] if ":" in loop.pattern[0] else loop.pattern[0]

    # 检查后续是否有不同的动作
    for i, action in enumerate(subsequent_actions[:5]):  # 看后续 5 步
        if action != loop_action:
            return True

    return False


def analyze_trajectory(filepath: Path) -> Optional[TrajectoryAnalysis]:
    """分析单条轨迹"""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except:
        return None

    instance_id = data.get("instance_id", filepath.stem)
    success = data.get("target", False)
    trajectory = data.get("trajectory", [])

    # 提取 AI 的动作
    commands = []
    actions = []

    for turn in trajectory:
        if turn.get("role") == "ai":
            text = turn.get("text", "")
            cmd = extract_command_from_ai_text(text)
            if cmd:
                commands.append(cmd)
                actions.append(classify_action(cmd))

    # 检测循环
    loops = detect_loops(commands, actions)

    # 检查每个循环是否恢复
    for loop in loops:
        end_idx = loop.start_step + loop.length
        subsequent_actions = [a[0] for a in actions[end_idx:end_idx+10]]
        subsequent_commands = commands[end_idx:end_idx+10]
        loop.recovered = check_recovery(loop, subsequent_actions, subsequent_commands)

    return TrajectoryAnalysis(
        instance_id=instance_id,
        success=success,
        total_steps=len(trajectory),
        ai_steps=len(commands),
        loops=loops,
        action_sequence=[a[0] for a in actions],
        command_sequence=commands
    )


# ============== 全量分析 ==============

def run_full_analysis(traj_dir: Path) -> Dict:
    """对所有轨迹进行分析"""
    results = {
        "total": 0,
        "success": 0,
        "failure": 0,
        "with_loops": {"success": 0, "failure": 0},
        "loop_stats": {
            "exact_command": {"count": 0, "lengths": [], "positions": [], "recovered": 0},
            "action_type": {"count": 0, "lengths": [], "positions": [], "recovered": 0},
            "action_target": {"count": 0, "lengths": [], "positions": [], "recovered": 0},
        },
        "trajectories": [],
        "loop_examples": defaultdict(list),  # 每种类型保存示例
    }

    traj_files = list(traj_dir.glob("*.json"))
    print(f"分析 {len(traj_files)} 条轨迹...")

    for i, filepath in enumerate(traj_files):
        if i % 500 == 0:
            print(f"  进度: {i}/{len(traj_files)}")

        analysis = analyze_trajectory(filepath)
        if analysis is None:
            continue

        results["total"] += 1

        if analysis.success:
            results["success"] += 1
            category = "success"
        else:
            results["failure"] += 1
            category = "failure"

        # 统计循环
        has_loop = len(analysis.loops) > 0
        if has_loop:
            results["with_loops"][category] += 1

        for loop in analysis.loops:
            stats = results["loop_stats"][loop.pattern_type]
            stats["count"] += 1
            stats["lengths"].append(loop.length)
            stats["positions"].append(loop.start_step)
            if loop.recovered:
                stats["recovered"] += 1

            # 保存示例（每种类型最多 10 个）
            if len(results["loop_examples"][loop.pattern_type]) < 10:
                results["loop_examples"][loop.pattern_type].append({
                    "instance_id": analysis.instance_id,
                    "success": analysis.success,
                    "start_step": loop.start_step,
                    "length": loop.length,
                    "pattern": loop.pattern,
                    "commands": loop.commands[:5]  # 只保存前 5 个
                })

        # 保存轨迹摘要
        results["trajectories"].append({
            "instance_id": analysis.instance_id,
            "success": analysis.success,
            "ai_steps": analysis.ai_steps,
            "loop_count": len(analysis.loops),
            "loop_types": [l.pattern_type for l in analysis.loops]
        })

    return results


def generate_report(results: Dict) -> str:
    """生成分析报告"""
    report = []
    report.append("# 实验 1：循环检测与量化分析报告\n")
    report.append("## 1. 总体统计\n")

    total = results["total"]
    success = results["success"]
    failure = results["failure"]

    report.append(f"| 指标 | 值 |")
    report.append(f"|------|-----|")
    report.append(f"| 总轨迹数 | {total} |")
    report.append(f"| 成功 | {success} ({success/total*100:.1f}%) |")
    report.append(f"| 失败 | {failure} ({failure/total*100:.1f}%) |")
    report.append("")

    # 循环率
    report.append("## 2. 循环检测结果\n")
    report.append("### 2.1 循环出现率\n")

    success_with_loop = results["with_loops"]["success"]
    failure_with_loop = results["with_loops"]["failure"]

    success_loop_rate = success_with_loop / success * 100 if success > 0 else 0
    failure_loop_rate = failure_with_loop / failure * 100 if failure > 0 else 0

    report.append(f"| 类别 | 有循环 | 无循环 | 循环率 |")
    report.append(f"|------|--------|--------|--------|")
    report.append(f"| 成功案例 | {success_with_loop} | {success - success_with_loop} | **{success_loop_rate:.1f}%** |")
    report.append(f"| 失败案例 | {failure_with_loop} | {failure - failure_with_loop} | **{failure_loop_rate:.1f}%** |")
    report.append("")

    # 按循环类型统计
    report.append("### 2.2 按循环类型统计\n")
    report.append("| 循环类型 | 出现次数 | 平均长度 | 平均位置 | 恢复率 |")
    report.append("|---------|---------|---------|---------|--------|")

    for loop_type, stats in results["loop_stats"].items():
        count = stats["count"]
        if count == 0:
            continue
        avg_len = statistics.mean(stats["lengths"]) if stats["lengths"] else 0
        avg_pos = statistics.mean(stats["positions"]) if stats["positions"] else 0
        recovery_rate = stats["recovered"] / count * 100 if count > 0 else 0

        type_name = {
            "exact_command": "完全相同命令",
            "action_type": "相同动作类型",
            "action_target": "相同动作+目标"
        }.get(loop_type, loop_type)

        report.append(f"| {type_name} | {count} | {avg_len:.1f} | 第 {avg_pos:.1f} 步 | {recovery_rate:.1f}% |")

    report.append("")

    # 循环长度分布
    report.append("### 2.3 循环长度分布\n")
    all_lengths = []
    for stats in results["loop_stats"].values():
        all_lengths.extend(stats["lengths"])

    if all_lengths:
        length_counts = Counter(all_lengths)
        report.append("| 循环长度 | 出现次数 | 占比 |")
        report.append("|---------|---------|------|")
        for length in sorted(length_counts.keys())[:10]:  # Top 10
            count = length_counts[length]
            pct = count / len(all_lengths) * 100
            report.append(f"| {length} 次重复 | {count} | {pct:.1f}% |")

        report.append("")
        report.append(f"**最长循环**: {max(all_lengths)} 次重复")
        report.append(f"**中位数**: {statistics.median(all_lengths):.0f} 次")

    report.append("")

    # 循环开始位置分布
    report.append("### 2.4 循环开始位置分析\n")
    all_positions = []
    for stats in results["loop_stats"].values():
        all_positions.extend(stats["positions"])

    if all_positions:
        early = sum(1 for p in all_positions if p <= 10)
        mid = sum(1 for p in all_positions if 10 < p <= 30)
        late = sum(1 for p in all_positions if p > 30)
        total_loops = len(all_positions)

        report.append("| 阶段 | 步骤范围 | 循环数 | 占比 |")
        report.append("|------|---------|--------|------|")
        report.append(f"| 早期 | 0-10 步 | {early} | {early/total_loops*100:.1f}% |")
        report.append(f"| 中期 | 10-30 步 | {mid} | {mid/total_loops*100:.1f}% |")
        report.append(f"| 后期 | 30+ 步 | {late} | {late/total_loops*100:.1f}% |")

    report.append("")

    # 示例
    report.append("## 3. 循环示例\n")
    for loop_type, examples in results["loop_examples"].items():
        if not examples:
            continue
        type_name = {
            "exact_command": "完全相同命令循环",
            "action_type": "相同动作类型循环",
            "action_target": "相同动作+目标循环"
        }.get(loop_type, loop_type)

        report.append(f"### {type_name}\n")
        for ex in examples[:3]:  # 每种类型展示 3 个
            status = "✅ 成功" if ex["success"] else "❌ 失败"
            report.append(f"**{ex['instance_id']}** ({status})")
            report.append(f"- 开始于第 {ex['start_step']} 步，重复 {ex['length']} 次")
            report.append(f"- 模式: `{ex['pattern']}`")
            if ex['commands']:
                report.append(f"- 示例命令: `{ex['commands'][0]}`")
            report.append("")

    # 关键发现
    report.append("## 4. 关键发现\n")

    if failure_loop_rate > success_loop_rate:
        diff = failure_loop_rate - success_loop_rate
        report.append(f"1. **失败案例的循环率 ({failure_loop_rate:.1f}%) 高于成功案例 ({success_loop_rate:.1f}%)**，差距 {diff:.1f}%")

    # 计算恢复率
    total_loops = sum(s["count"] for s in results["loop_stats"].values())
    total_recovered = sum(s["recovered"] for s in results["loop_stats"].values())
    overall_recovery = total_recovered / total_loops * 100 if total_loops > 0 else 0
    report.append(f"2. **整体恢复率: {overall_recovery:.1f}%** - Agent 从循环中自行恢复的比例")

    if all_positions:
        avg_start = statistics.mean(all_positions)
        report.append(f"3. **平均循环开始位置: 第 {avg_start:.1f} 步** - 循环通常在何时开始")

    return "\n".join(report)


if __name__ == "__main__":
    traj_dir = Path("swe_trajectories/trajectories")

    print("=" * 50)
    print("实验 1：循环检测与量化分析")
    print("=" * 50)

    # 运行分析
    results = run_full_analysis(traj_dir)

    # 生成报告
    report = generate_report(results)

    # 保存报告
    with open("EXPERIMENT1_LOOP_ANALYSIS.md", "w", encoding="utf-8") as f:
        f.write(report)
    print("\n报告已保存到 EXPERIMENT1_LOOP_ANALYSIS.md")

    # 保存详细数据
    # 转换为可序列化格式
    json_results = {
        "total": results["total"],
        "success": results["success"],
        "failure": results["failure"],
        "with_loops": results["with_loops"],
        "loop_stats": {
            k: {
                "count": v["count"],
                "avg_length": statistics.mean(v["lengths"]) if v["lengths"] else 0,
                "avg_position": statistics.mean(v["positions"]) if v["positions"] else 0,
                "recovery_rate": v["recovered"] / v["count"] if v["count"] > 0 else 0
            }
            for k, v in results["loop_stats"].items()
        },
        "loop_examples": dict(results["loop_examples"]),
        "trajectories": results["trajectories"]
    }

    with open("experiment1_loop_data.json", "w", encoding="utf-8") as f:
        json.dump(json_results, f, indent=2, ensure_ascii=False)
    print("详细数据已保存到 experiment1_loop_data.json")

    print("\n" + "=" * 50)
    print("分析完成！")
