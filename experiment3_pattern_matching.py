#!/usr/bin/env python3
"""
实验 3：跨轨迹的模式匹配
从成功轨迹中提取"修复策略子图"，匹配失败轨迹的早期阶段
"""

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Set, Tuple, Optional
from pathlib import Path

# ============ 数据结构 ============

@dataclass
class ActionStep:
    """单个动作步骤"""
    step_num: int
    action_type: str  # search, open, edit, execute, test, submit
    command: str
    target: Optional[str] = None  # 目标文件或搜索词

@dataclass
class FixStrategy:
    """修复策略子图"""
    strategy_id: str
    task_category: str  # bug_fix, type_error, logic_error, api_misuse, etc.
    steps: List[ActionStep]
    success_rate: float = 0.0
    example_cases: List[str] = field(default_factory=list)
    key_pattern: str = ""  # 策略的关键特征描述

@dataclass
class MatchResult:
    """匹配结果"""
    failed_case: str
    matched_strategy: str
    match_point: int  # 在第几步开始匹配
    diverge_point: int  # 在第几步开始分歧
    intervention_potential: str  # high, medium, low
    reason: str

# ============ 解析轨迹 ============

def parse_trajectory(traj: dict) -> Tuple[List[ActionStep], bool]:
    """解析单条轨迹为动作序列"""
    # 使用 trajectory 字段
    trajectory = traj.get('trajectory', [])
    steps = []

    for i, item in enumerate(trajectory):
        if isinstance(item, dict):
            role = item.get('role', '')
            # content 在 text 字段中
            content = item.get('text', '') or item.get('content', '')
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            role, content = item[0], item[1]
        else:
            continue

        # role 是 'ai' 而不是 'assistant'
        if role not in ('assistant', 'ai'):
            continue

        # 提取命令
        action_type, command, target = extract_action(content)
        if action_type:
            steps.append(ActionStep(
                step_num=len(steps),
                action_type=action_type,
                command=command,
                target=target
            ))

    # 判断是否成功 - 使用 target 字段
    is_success = traj.get('target', False) == True

    return steps, is_success

def extract_action(content: str) -> Tuple[Optional[str], str, Optional[str]]:
    """从 assistant 内容中提取动作"""
    content = str(content).strip()

    patterns = [
        # 搜索类
        (r'find_file\s+["\']?([^"\']+)["\']?', 'search', 1),
        (r'search_file\s+["\']?([^"\']+)["\']?', 'search', 1),
        (r'search_dir\s+["\']?([^"\']+)["\']?', 'search', 1),
        (r'find\s+.*-name\s+["\']?([^"\']+)["\']?', 'search', 1),
        (r'grep\s+.*["\']?([^"\']+)["\']?', 'search', 1),

        # 打开/查看类
        (r'open\s+([^\s]+)', 'open', 1),
        (r'goto\s+(\d+)', 'navigate', 1),
        (r'scroll_(up|down)', 'navigate', None),

        # 编辑类
        (r'edit\s+(\d+:\d+)', 'edit', 1),
        (r'edit\s+(\d+)', 'edit', 1),

        # 执行类
        (r'python\s+([^\s]+)', 'execute', 1),
        (r'pytest\s+([^\s]+)?', 'test', 1),
        (r'pip\s+install', 'execute', None),

        # 创建类
        (r'create\s+([^\s]+)', 'create', 1),

        # 提交类
        (r'submit', 'submit', None),
    ]

    for pattern, action_type, target_group in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            command = match.group(0)
            target = match.group(target_group) if target_group and match.lastindex >= target_group else None
            return action_type, command, target

    return None, content[:100], None

# ============ 策略提取 ============

def categorize_task(traj: dict) -> str:
    """对任务进行分类"""
    problem = str(traj.get('problem_statement', '')).lower()

    # 根据问题描述分类
    if 'typeerror' in problem or 'type error' in problem:
        return 'type_error'
    elif 'attributeerror' in problem or 'attribute error' in problem:
        return 'attribute_error'
    elif 'keyerror' in problem or 'key error' in problem:
        return 'key_error'
    elif 'indexerror' in problem or 'index error' in problem:
        return 'index_error'
    elif 'valueerror' in problem or 'value error' in problem:
        return 'value_error'
    elif 'importerror' in problem or 'import error' in problem:
        return 'import_error'
    elif 'fix' in problem or 'bug' in problem or 'error' in problem:
        return 'bug_fix'
    elif 'add' in problem or 'implement' in problem or 'feature' in problem:
        return 'feature_request'
    elif 'refactor' in problem or 'rename' in problem:
        return 'refactoring'
    else:
        return 'general'

def extract_strategy_signature(steps: List[ActionStep], max_steps: int = 10) -> str:
    """提取策略签名（用于相似性匹配）"""
    # 只取前 max_steps 步的动作类型序列
    action_sequence = [s.action_type for s in steps[:max_steps]]
    return '->'.join(action_sequence)

def extract_fix_strategies(trajectories: List[dict]) -> Dict[str, List[FixStrategy]]:
    """从成功轨迹中提取修复策略"""
    strategies_by_category = defaultdict(list)

    # 按任务类型和策略签名分组
    signature_groups = defaultdict(lambda: {'steps_list': [], 'cases': []})

    for traj in trajectories:
        steps, is_success = parse_trajectory(traj)
        if not is_success or len(steps) < 3:
            continue

        category = categorize_task(traj)
        signature = extract_strategy_signature(steps)
        instance_id = traj.get('instance_id', 'unknown')

        key = (category, signature)
        signature_groups[key]['steps_list'].append(steps)
        signature_groups[key]['cases'].append(instance_id)

    # 合并相似策略
    strategy_id = 0
    for (category, signature), group in signature_groups.items():
        if len(group['cases']) < 1:  # 至少出现 1 次
            continue

        # 取第一个作为代表
        representative_steps = group['steps_list'][0]

        strategy = FixStrategy(
            strategy_id=f"S{strategy_id:03d}",
            task_category=category,
            steps=representative_steps[:15],  # 最多保留 15 步
            success_rate=1.0,  # 都是成功案例
            example_cases=group['cases'][:5],  # 最多保留 5 个例子
            key_pattern=signature
        )
        strategies_by_category[category].append(strategy)
        strategy_id += 1

    return dict(strategies_by_category)

# ============ 模式匹配 ============

def compute_action_similarity(a1: ActionStep, a2: ActionStep) -> float:
    """计算两个动作的相似度"""
    score = 0.0

    # 动作类型相同
    if a1.action_type == a2.action_type:
        score += 0.6

        # 目标相似
        if a1.target and a2.target:
            if a1.target == a2.target:
                score += 0.4
            elif a1.target in a2.target or a2.target in a1.target:
                score += 0.2

    return score

def match_strategy_to_trajectory(strategy: FixStrategy,
                                  failed_steps: List[ActionStep],
                                  min_match_length: int = 3) -> Optional[Tuple[int, int, float]]:
    """
    尝试将策略匹配到失败轨迹
    返回: (匹配开始点, 分歧点, 匹配分数) 或 None
    """
    strategy_steps = strategy.steps

    # 滑动窗口匹配
    best_match = None
    best_score = 0

    for start in range(len(failed_steps) - min_match_length + 1):
        match_length = 0
        total_similarity = 0

        for i, s_step in enumerate(strategy_steps):
            if start + i >= len(failed_steps):
                break

            f_step = failed_steps[start + i]
            sim = compute_action_similarity(s_step, f_step)

            if sim >= 0.5:  # 阈值
                match_length += 1
                total_similarity += sim
            else:
                break

        if match_length >= min_match_length:
            score = total_similarity / len(strategy_steps)
            if score > best_score:
                best_score = score
                diverge_point = start + match_length
                best_match = (start, diverge_point, score)

    return best_match

def analyze_intervention_potential(failed_steps: List[ActionStep],
                                    strategy: FixStrategy,
                                    diverge_point: int) -> Tuple[str, str]:
    """分析干预潜力"""
    # 看分歧后发生了什么
    post_diverge = failed_steps[diverge_point:diverge_point + 5]

    # 检查是否进入循环
    if len(post_diverge) >= 3:
        actions = [s.action_type for s in post_diverge]
        if len(set(actions)) == 1:  # 全是相同动作
            return 'high', f'Agent 在第 {diverge_point} 步后进入循环（重复 {actions[0]}）'

    # 检查是否有错误
    for s in post_diverge:
        if 'error' in s.command.lower():
            return 'high', f'Agent 在第 {diverge_point} 步后遇到错误'

    # 如果分歧较早
    if diverge_point < 5:
        return 'high', f'早期分歧（第 {diverge_point} 步），干预可阻止后续错误'
    elif diverge_point < 10:
        return 'medium', f'中期分歧（第 {diverge_point} 步），有机会纠正'
    else:
        return 'low', f'后期分歧（第 {diverge_point} 步），可能已错过最佳干预点'

def match_all_failed_trajectories(failed_trajectories: List[dict],
                                   strategies: Dict[str, List[FixStrategy]]) -> List[MatchResult]:
    """对所有失败轨迹进行模式匹配"""
    results = []

    for traj in failed_trajectories:
        steps, _ = parse_trajectory(traj)
        if len(steps) < 5:
            continue

        category = categorize_task(traj)
        instance_id = traj.get('instance_id', 'unknown')

        # 尝试匹配同类别的策略
        best_match = None
        best_strategy = None

        # 优先匹配同类别
        for cat in [category, 'general', 'bug_fix']:
            if cat not in strategies:
                continue
            for strategy in strategies[cat]:
                match = match_strategy_to_trajectory(strategy, steps)
                if match:
                    start, diverge, score = match
                    if best_match is None or score > best_match[2]:
                        best_match = match
                        best_strategy = strategy

        if best_match and best_strategy:
            start, diverge, score = best_match
            potential, reason = analyze_intervention_potential(steps, best_strategy, diverge)

            results.append(MatchResult(
                failed_case=instance_id,
                matched_strategy=best_strategy.strategy_id,
                match_point=start,
                diverge_point=diverge,
                intervention_potential=potential,
                reason=reason
            ))

    return results

# ============ 主分析 ============

def main():
    # 加载数据
    traj_dir = Path("swe_trajectories/trajectories")

    print("加载轨迹数据...")
    all_trajectories = []

    for traj_file in traj_dir.glob('*.json'):
        try:
            with open(traj_file, 'r') as f:
                traj = json.load(f)
                all_trajectories.append(traj)
        except Exception as e:
            continue

    print(f"共加载 {len(all_trajectories)} 条轨迹")

    # 分离成功和失败轨迹
    successful = []
    failed = []

    for traj in all_trajectories:
        steps, is_success = parse_trajectory(traj)
        if len(steps) >= 3:
            if is_success:
                successful.append(traj)
            else:
                failed.append(traj)

    print(f"成功轨迹: {len(successful)}, 失败轨迹: {len(failed)}")

    # ============ 实验 3.1: 提取修复策略 ============
    print("\n" + "="*50)
    print("实验 3.1: 从成功轨迹中提取修复策略")
    print("="*50)

    strategies = extract_fix_strategies(successful)

    total_strategies = sum(len(v) for v in strategies.values())
    print(f"\n提取到 {total_strategies} 个修复策略模式:")

    for category, strats in sorted(strategies.items(), key=lambda x: -len(x[1])):
        print(f"  {category}: {len(strats)} 个策略")
        for s in strats[:3]:
            print(f"    - {s.strategy_id}: {s.key_pattern[:50]}... (来自 {len(s.example_cases)} 个案例)")

    # ============ 实验 3.2: 模式匹配 ============
    print("\n" + "="*50)
    print("实验 3.2: 跨轨迹模式匹配")
    print("="*50)

    # 只取前 200 条失败轨迹进行详细分析
    sample_failed = failed[:200]
    match_results = match_all_failed_trajectories(sample_failed, strategies)

    print(f"\n对 {len(sample_failed)} 条失败轨迹进行匹配:")
    match_rate = 100*len(match_results)/len(sample_failed) if sample_failed else 0
    print(f"  成功匹配: {len(match_results)} 条 ({match_rate:.1f}%)")

    # 统计干预潜力
    potential_counts = defaultdict(int)
    for r in match_results:
        potential_counts[r.intervention_potential] += 1

    print(f"\n干预潜力分布:")
    for pot in ['high', 'medium', 'low']:
        count = potential_counts[pot]
        pct = 100 * count / len(match_results) if match_results else 0
        print(f"  {pot}: {count} ({pct:.1f}%)")

    # 分歧点分布
    diverge_points = [r.diverge_point for r in match_results]
    if diverge_points:
        avg_diverge = sum(diverge_points) / len(diverge_points)
        print(f"\n平均分歧点: 第 {avg_diverge:.1f} 步")

        early = sum(1 for d in diverge_points if d < 5)
        mid = sum(1 for d in diverge_points if 5 <= d < 10)
        late = sum(1 for d in diverge_points if d >= 10)
        print(f"分歧阶段分布:")
        print(f"  早期 (< 5 步): {early} ({100*early/len(diverge_points):.1f}%)")
        print(f"  中期 (5-10 步): {mid} ({100*mid/len(diverge_points):.1f}%)")
        print(f"  后期 (>= 10 步): {late} ({100*late/len(diverge_points):.1f}%)")

    # ============ 实验 3.3: 典型案例分析 ============
    print("\n" + "="*50)
    print("实验 3.3: 典型匹配案例")
    print("="*50)

    # 展示高干预潜力的案例
    high_potential = [r for r in match_results if r.intervention_potential == 'high'][:5]

    for i, result in enumerate(high_potential):
        print(f"\n案例 {i+1}: {result.failed_case}")
        print(f"  匹配策略: {result.matched_strategy}")
        print(f"  匹配开始: 第 {result.match_point} 步")
        print(f"  分歧点: 第 {result.diverge_point} 步")
        print(f"  干预潜力: {result.intervention_potential}")
        print(f"  原因: {result.reason}")

    # ============ 保存结果 ============

    # 保存策略
    strategies_data = {}
    for cat, strats in strategies.items():
        strategies_data[cat] = [
            {
                'strategy_id': s.strategy_id,
                'task_category': s.task_category,
                'steps': [asdict(step) for step in s.steps],
                'success_rate': s.success_rate,
                'example_cases': s.example_cases,
                'key_pattern': s.key_pattern
            }
            for s in strats
        ]

    with open('experiment3_strategies.json', 'w') as f:
        json.dump(strategies_data, f, indent=2)

    # 保存匹配结果
    match_data = [asdict(r) for r in match_results]
    with open('experiment3_matches.json', 'w') as f:
        json.dump(match_data, f, indent=2)

    # 生成报告
    report = generate_report(strategies, match_results, len(successful), len(failed))
    with open('EXPERIMENT3_PATTERN_MATCHING.md', 'w') as f:
        f.write(report)

    print("\n结果已保存到:")
    print("  - experiment3_strategies.json")
    print("  - experiment3_matches.json")
    print("  - EXPERIMENT3_PATTERN_MATCHING.md")

def generate_report(strategies: Dict[str, List[FixStrategy]],
                    matches: List[MatchResult],
                    n_success: int, n_failed: int) -> str:
    """生成实验报告"""

    # 统计
    total_strategies = sum(len(v) for v in strategies.values())
    n_matched = len(matches)

    potential_counts = defaultdict(int)
    for m in matches:
        potential_counts[m.intervention_potential] += 1

    diverge_points = [m.diverge_point for m in matches]
    avg_diverge = sum(diverge_points) / len(diverge_points) if diverge_points else 0

    report = f"""# 实验 3：跨轨迹的模式匹配报告

## 1. 实验目标

从成功轨迹中提取"修复策略子图"，验证其是否能匹配失败轨迹的早期阶段，
从而为 Agent 提供早期干预提示。

## 2. 数据概览

| 指标 | 值 |
|------|-----|
| 成功轨迹数 | {n_success} |
| 失败轨迹数 | {n_failed} |
| 提取的策略数 | {total_strategies} |
| 匹配的失败轨迹数 | {n_matched} |

## 3. 策略提取结果

### 3.1 按任务类别分布

| 任务类别 | 策略数 |
|---------|--------|
"""

    for cat, strats in sorted(strategies.items(), key=lambda x: -len(x[1])):
        report += f"| {cat} | {len(strats)} |\n"

    report += f"""
### 3.2 典型策略示例

"""

    # 展示一些典型策略
    shown = 0
    for cat, strats in strategies.items():
        if shown >= 5:
            break
        for s in strats[:1]:
            report += f"**{s.strategy_id}** ({cat})\n"
            report += f"- 模式: `{s.key_pattern[:60]}...`\n"
            report += f"- 来源案例: {', '.join(s.example_cases[:3])}\n\n"
            shown += 1

    report += f"""
## 4. 模式匹配结果

### 4.1 匹配率

- 尝试匹配: 200 条失败轨迹
- 成功匹配: {n_matched} 条 ({100*n_matched/200:.1f}%)

### 4.2 干预潜力分布

| 潜力等级 | 数量 | 占比 |
|---------|------|------|
| high (高) | {potential_counts['high']} | {100*potential_counts['high']/n_matched if n_matched else 0:.1f}% |
| medium (中) | {potential_counts['medium']} | {100*potential_counts['medium']/n_matched if n_matched else 0:.1f}% |
| low (低) | {potential_counts['low']} | {100*potential_counts['low']/n_matched if n_matched else 0:.1f}% |

### 4.3 分歧点分析

- **平均分歧点**: 第 {avg_diverge:.1f} 步
"""

    if diverge_points:
        early = sum(1 for d in diverge_points if d < 5)
        mid = sum(1 for d in diverge_points if 5 <= d < 10)
        late = sum(1 for d in diverge_points if d >= 10)

        report += f"""
| 分歧阶段 | 数量 | 占比 | 干预难度 |
|---------|------|------|---------|
| 早期 (< 5 步) | {early} | {100*early/len(diverge_points):.1f}% | 容易 |
| 中期 (5-10 步) | {mid} | {100*mid/len(diverge_points):.1f}% | 中等 |
| 后期 (>= 10 步) | {late} | {100*late/len(diverge_points):.1f}% | 困难 |
"""

    report += """
## 5. 典型匹配案例

"""

    # 高干预潜力案例
    high_potential = [m for m in matches if m.intervention_potential == 'high'][:5]
    for i, m in enumerate(high_potential):
        report += f"""### 案例 {i+1}: `{m.failed_case}`

- **匹配策略**: {m.matched_strategy}
- **匹配开始**: 第 {m.match_point} 步
- **分歧点**: 第 {m.diverge_point} 步
- **干预潜力**: {m.intervention_potential}
- **原因**: {m.reason}

"""

    report += """
## 6. 核心发现

### 6.1 模式匹配的可行性

"""

    if n_matched > 0:
        match_rate = 100 * n_matched / 200
        high_rate = 100 * potential_counts['high'] / n_matched

        if match_rate > 50:
            report += f"✅ **{match_rate:.1f}%** 的失败轨迹可以匹配到成功策略\n\n"
        else:
            report += f"⚠️ 仅 **{match_rate:.1f}%** 的失败轨迹可以匹配，需要更多策略模式\n\n"

        if high_rate > 30:
            report += f"✅ **{high_rate:.1f}%** 的匹配具有高干预潜力\n\n"
        else:
            report += f"⚠️ 仅 **{high_rate:.1f}%** 具有高干预潜力，早期检测能力有限\n\n"

    report += """
### 6.2 干预时机的关键性

"""

    if avg_diverge > 0:
        if avg_diverge < 5:
            report += f"✅ 平均分歧点在第 {avg_diverge:.1f} 步，**早期干预可行**\n"
        elif avg_diverge < 10:
            report += f"⚠️ 平均分歧点在第 {avg_diverge:.1f} 步，需要**快速检测机制**\n"
        else:
            report += f"⚠️ 平均分歧点在第 {avg_diverge:.1f} 步，**干预可能太晚**\n"

    report += """
### 6.3 Context Graph 的价值

基于匹配实验，Context Graph 可以：

1. **存储修复策略模板**: 将成功轨迹的关键步骤抽象为可重用模式
2. **提供早期预警**: 在 Agent 开始偏离成功模式时发出警告
3. **推荐下一步动作**: 根据匹配的策略模板，建议最优动作

### 6.4 建议的 Schema 增强

```
StrategyNode:
  - strategy_id: str
  - task_category: str
  - success_rate: float
  - key_steps: List[ActionNode]

MATCHES_STRATEGY 边:
  - 连接当前轨迹到匹配的策略
  - 属性: match_score, diverge_point

SUGGESTS_NEXT 边:
  - 从策略节点到建议的下一步动作
  - 属性: confidence, reason
```

## 7. 结论

"""

    if n_matched > 100:
        report += """本实验验证了 **跨轨迹模式匹配** 的可行性：

1. 可以从成功轨迹中提取有意义的修复策略
2. 这些策略可以匹配到失败轨迹的早期阶段
3. 存在显著的 **干预窗口期**，可以在 Agent 出错前提供帮助

这为 Context Graph 的核心价值提供了实验依据：
> **通过存储和匹配历史成功经验，可以为 Agent 提供实时的策略建议，
> 避免重复已知的失败模式。**
"""
    else:
        report += """实验结果表明模式匹配存在一定挑战：

1. 成功轨迹的多样性导致策略难以泛化
2. 需要更细粒度的动作表示来提高匹配精度
3. 考虑引入语义相似度（如代码实体相似性）来增强匹配

后续工作建议：
- 使用 embedding 而非精确匹配
- 引入任务描述的语义相似度
- 考虑代码结构的图匹配
"""

    report += f"""
---

**分析完成时间**: 2026-02-06
**数据来源**: {n_success + n_failed} 条 SWE-agent 轨迹
**方法**: 策略提取 + 序列匹配 + 干预潜力分析
"""

    return report

if __name__ == '__main__':
    main()
