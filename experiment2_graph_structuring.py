"""
实验 2：失败轨迹的图结构化
目标：将 50-100 条失败轨迹转换为 Context Graph 格式，验证 schema 的表达能力
"""

import json
import re
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple, Any
from collections import defaultdict
from datetime import datetime
import hashlib

# ============== Schema 定义 ==============

@dataclass
class ActionNode:
    """动作节点 - 记录 agent 的每个动作"""
    node_id: str
    node_type: str = "action"

    # 动作信息
    action_type: str = ""  # search, edit, open, navigate, execute, test, create, submit
    command: str = ""      # 原始命令
    target: Optional[str] = None  # 目标文件/搜索词

    # 上下文
    step_index: int = 0
    instance_id: str = ""
    reasoning: str = ""    # agent 的推理过程

    # 结果
    success: bool = True
    error_type: Optional[str] = None
    error_message: str = ""

    # 循环检测
    is_repeated: bool = False
    repeat_count: int = 0


@dataclass
class CodeEntityNode:
    """代码实体节点"""
    node_id: str
    node_type: str  # file, function, class, method, variable

    name: str = ""
    file_path: Optional[str] = None
    line_range: Optional[Tuple[int, int]] = None

    # 修改状态
    was_read: bool = False
    was_modified: bool = False
    modification_correct: Optional[bool] = None  # 修改是否正确

    # 遗漏检测
    was_expected: bool = False  # 是否应该被处理
    was_handled: bool = False   # 是否实际被处理


@dataclass
class ErrorPatternNode:
    """错误模式节点"""
    node_id: str
    node_type: str = "error_pattern"

    error_type: str = ""  # syntax_error, runtime_error, test_failure, etc.
    error_message: str = ""

    # 关联
    caused_by_action: Optional[str] = None
    affected_entity: Optional[str] = None

    # 分析
    is_recoverable: bool = True
    recovery_attempted: bool = False
    recovery_successful: bool = False


@dataclass
class FailurePatternNode:
    """失败模式节点 - 高层抽象"""
    node_id: str
    node_type: str = "failure_pattern"

    pattern_type: str = ""  # loop_trap, task_misunderstanding, technical_error, incomplete_refactoring
    description: str = ""

    # 相关节点
    involved_actions: List[str] = field(default_factory=list)
    involved_entities: List[str] = field(default_factory=list)
    involved_errors: List[str] = field(default_factory=list)

    # 元数据
    start_step: int = 0
    end_step: int = 0
    severity: str = "medium"  # low, medium, high, critical


@dataclass
class Edge:
    """边"""
    edge_id: str
    edge_type: str  # NEXT_ACTION, TARGETS, CAUSES, MODIFIES, READS, PART_OF, RECOVERS_FROM
    source_id: str
    target_id: str

    fact: str = ""
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TrajectoryGraph:
    """单条轨迹的图表示"""
    instance_id: str
    success: bool

    # 节点
    action_nodes: List[ActionNode] = field(default_factory=list)
    entity_nodes: List[CodeEntityNode] = field(default_factory=list)
    error_nodes: List[ErrorPatternNode] = field(default_factory=list)
    failure_patterns: List[FailurePatternNode] = field(default_factory=list)

    # 边
    edges: List[Edge] = field(default_factory=list)

    # 元数据
    total_steps: int = 0
    failure_type: str = ""

    def to_dict(self) -> Dict:
        return {
            "instance_id": self.instance_id,
            "success": self.success,
            "total_steps": self.total_steps,
            "failure_type": self.failure_type,
            "nodes": {
                "actions": [asdict(n) for n in self.action_nodes],
                "entities": [asdict(n) for n in self.entity_nodes],
                "errors": [asdict(n) for n in self.error_nodes],
                "failure_patterns": [asdict(n) for n in self.failure_patterns]
            },
            "edges": [asdict(e) for e in self.edges],
            "stats": {
                "action_count": len(self.action_nodes),
                "entity_count": len(self.entity_nodes),
                "error_count": len(self.error_nodes),
                "edge_count": len(self.edges),
                "failure_pattern_count": len(self.failure_patterns)
            }
        }


# ============== 转换逻辑 ==============

def generate_id(prefix: str, content: str) -> str:
    """生成唯一 ID"""
    hash_val = hashlib.md5(content.encode()).hexdigest()[:8]
    return f"{prefix}_{hash_val}"


def extract_command(text: str) -> Optional[str]:
    """从 AI 文本中提取命令"""
    if not text:
        return None
    match = re.search(r'```\n?([^\n]+)', text)
    return match.group(1).strip() if match else None


def classify_action(command: str) -> Tuple[str, Optional[str]]:
    """分类动作类型"""
    if not command:
        return ("unknown", None)

    cmd = command.strip()

    # 搜索
    if any(cmd.startswith(p) for p in ["search_dir", "search_file", "find_file", "grep", "find "]):
        match = re.search(r'"([^"]+)"', cmd)
        return ("search", match.group(1) if match else None)

    # 打开文件
    if cmd.startswith("open"):
        match = re.search(r'open\s+(\S+)', cmd)
        return ("open", match.group(1) if match else None)

    # 导航
    if cmd.startswith("goto") or cmd.startswith("scroll_"):
        return ("navigate", None)

    # 编辑
    if cmd.startswith("edit"):
        return ("edit", None)

    # 创建
    if cmd.startswith("create"):
        match = re.search(r'create\s+(\S+)', cmd)
        return ("create", match.group(1) if match else None)

    # 执行
    if cmd.startswith("python") or cmd.startswith("bash"):
        match = re.search(r'\s+(\S+\.py)', cmd)
        return ("execute", match.group(1) if match else None)

    # 测试
    if "test" in cmd.lower() or "pytest" in cmd:
        return ("test", None)

    # 提交
    if cmd == "submit":
        return ("submit", None)

    # 安装
    if cmd.startswith("pip"):
        return ("install", None)

    # 列出
    if cmd.startswith("ls"):
        return ("list", None)

    return ("other", None)


def detect_error_in_observation(observation: str) -> Tuple[Optional[str], str]:
    """从观察结果中检测错误"""
    if not observation:
        return (None, "")

    obs_lower = observation.lower()

    # 各种错误类型
    error_patterns = [
        ("syntax_error", ["syntaxerror", "indentationerror", "invalid syntax"]),
        ("import_error", ["importerror", "modulenotfounderror", "cannot import"]),
        ("type_error", ["typeerror"]),
        ("value_error", ["valueerror"]),
        ("attribute_error", ["attributeerror"]),
        ("name_error", ["nameerror"]),
        ("key_error", ["keyerror"]),
        ("file_not_found", ["filenotfounderror", "no such file"]),
        ("timeout", ["timed out", "timeout", "execution timed out"]),
        ("test_failure", ["failed", "failures=", "error", "assertion"]),
        ("connection_error", ["connectionerror", "connection refused"]),
    ]

    for error_type, patterns in error_patterns:
        if any(p in obs_lower for p in patterns):
            # 提取错误消息
            lines = observation.split('\n')
            error_lines = [l for l in lines if any(p in l.lower() for p in patterns)]
            return (error_type, error_lines[0] if error_lines else "")

    return (None, "")


def detect_loop_pattern(actions: List[ActionNode]) -> List[FailurePatternNode]:
    """检测循环模式"""
    patterns = []

    if len(actions) < 3:
        return patterns

    # 检测连续相同命令
    i = 0
    while i < len(actions):
        count = 1
        while i + count < len(actions) and actions[i].command == actions[i + count].command:
            actions[i + count].is_repeated = True
            count += 1

        if count >= 3:
            pattern = FailurePatternNode(
                node_id=generate_id("loop", f"{actions[i].instance_id}_{i}"),
                pattern_type="loop_trap",
                description=f"Repeated command '{actions[i].command}' {count} times",
                involved_actions=[actions[j].node_id for j in range(i, i + count)],
                start_step=actions[i].step_index,
                end_step=actions[i + count - 1].step_index if i + count - 1 < len(actions) else actions[-1].step_index,
                severity="high" if count >= 5 else "medium"
            )
            actions[i].repeat_count = count
            patterns.append(pattern)

        i += count

    return patterns


def detect_technical_error(actions: List[ActionNode], errors: List[ErrorPatternNode]) -> List[FailurePatternNode]:
    """检测技术知识错误"""
    patterns = []

    # 查找编辑后立即导致错误的情况
    for i, action in enumerate(actions):
        if action.action_type == "edit" and i + 1 < len(actions):
            # 检查下一步是否有相关错误
            next_errors = [e for e in errors if e.caused_by_action == action.node_id]
            if next_errors and next_errors[0].error_type in ["syntax_error", "type_error", "value_error"]:
                pattern = FailurePatternNode(
                    node_id=generate_id("tech_err", f"{action.instance_id}_{i}"),
                    pattern_type="technical_error",
                    description=f"Edit caused {next_errors[0].error_type}",
                    involved_actions=[action.node_id],
                    involved_errors=[next_errors[0].node_id],
                    start_step=action.step_index,
                    end_step=action.step_index + 1,
                    severity="high"
                )
                patterns.append(pattern)

    return patterns


def convert_trajectory_to_graph(filepath: Path) -> Optional[TrajectoryGraph]:
    """将轨迹转换为图结构"""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except:
        return None

    instance_id = data.get("instance_id", filepath.stem)
    success = data.get("target", False)
    trajectory = data.get("trajectory", [])

    graph = TrajectoryGraph(
        instance_id=instance_id,
        success=success,
        total_steps=len(trajectory)
    )

    # 提取动作节点
    step_idx = 0
    prev_action_id = None
    entities_seen = {}

    for i, turn in enumerate(trajectory):
        if turn.get("role") == "ai":
            text = turn.get("text", "")
            command = extract_command(text)

            if command:
                action_type, target = classify_action(command)

                action = ActionNode(
                    node_id=generate_id("action", f"{instance_id}_{step_idx}"),
                    action_type=action_type,
                    command=command,
                    target=target,
                    step_index=step_idx,
                    instance_id=instance_id,
                    reasoning=text[:500] if text else ""  # 截取推理部分
                )

                graph.action_nodes.append(action)

                # 添加 NEXT_ACTION 边
                if prev_action_id:
                    edge = Edge(
                        edge_id=generate_id("edge", f"{prev_action_id}_{action.node_id}"),
                        edge_type="NEXT_ACTION",
                        source_id=prev_action_id,
                        target_id=action.node_id,
                        fact=f"Step {step_idx - 1} -> Step {step_idx}"
                    )
                    graph.edges.append(edge)

                prev_action_id = action.node_id

                # 如果有目标，创建实体节点
                if target and target not in entities_seen:
                    entity = CodeEntityNode(
                        node_id=generate_id("entity", target),
                        node_type="file" if "." in target else "search_term",
                        name=target,
                        file_path=target if "." in target else None
                    )
                    if action_type == "open":
                        entity.was_read = True
                    elif action_type == "edit":
                        entity.was_modified = True

                    graph.entity_nodes.append(entity)
                    entities_seen[target] = entity.node_id

                    # 添加 TARGETS 边
                    edge = Edge(
                        edge_id=generate_id("edge", f"{action.node_id}_{entity.node_id}"),
                        edge_type="TARGETS",
                        source_id=action.node_id,
                        target_id=entity.node_id,
                        fact=f"{action_type} targets {target}"
                    )
                    graph.edges.append(edge)

                step_idx += 1

        elif turn.get("role") == "user" and prev_action_id:
            # 检查观察结果中的错误
            observation = turn.get("text", "")
            error_type, error_msg = detect_error_in_observation(observation)

            if error_type:
                error = ErrorPatternNode(
                    node_id=generate_id("error", f"{instance_id}_{step_idx}_{error_type}"),
                    error_type=error_type,
                    error_message=error_msg,
                    caused_by_action=prev_action_id
                )
                graph.error_nodes.append(error)

                # 更新动作节点
                for action in graph.action_nodes:
                    if action.node_id == prev_action_id:
                        action.success = False
                        action.error_type = error_type
                        action.error_message = error_msg

                # 添加 CAUSES 边
                edge = Edge(
                    edge_id=generate_id("edge", f"{prev_action_id}_{error.node_id}"),
                    edge_type="CAUSES",
                    source_id=prev_action_id,
                    target_id=error.node_id,
                    fact=f"Action caused {error_type}"
                )
                graph.edges.append(edge)

    # 检测失败模式
    loop_patterns = detect_loop_pattern(graph.action_nodes)
    graph.failure_patterns.extend(loop_patterns)

    tech_error_patterns = detect_technical_error(graph.action_nodes, graph.error_nodes)
    graph.failure_patterns.extend(tech_error_patterns)

    # 确定主要失败类型
    if loop_patterns:
        graph.failure_type = "loop_trap"
    elif tech_error_patterns:
        graph.failure_type = "technical_error"
    elif graph.error_nodes:
        graph.failure_type = "error_accumulation"
    else:
        graph.failure_type = "unknown"

    return graph


# ============== 主程序 ==============

def select_representative_failures(traj_dir: Path, n: int = 100) -> List[Path]:
    """选择有代表性的失败轨迹"""
    # 读取实验1的数据来选择
    with open("experiment1_loop_data.json", "r") as f:
        loop_data = json.load(f)

    trajectories = loop_data["trajectories"]

    # 分类
    severe_loops = []      # 有严重循环的
    mild_loops = []        # 有轻微循环的
    no_loops = []          # 无循环的

    for t in trajectories:
        if t["success"]:
            continue

        filepath = traj_dir / f"{t['instance_id']}.json"
        if not filepath.exists():
            continue

        if "exact_command" in t["loop_types"]:
            severe_loops.append(filepath)
        elif t["loop_count"] > 0:
            mild_loops.append(filepath)
        else:
            no_loops.append(filepath)

    # 按比例选择
    selected = []
    selected.extend(severe_loops[:n//3])       # 1/3 严重循环
    selected.extend(mild_loops[:n//3])         # 1/3 轻微循环
    selected.extend(no_loops[:n//3])           # 1/3 无循环

    # 补足
    remaining = n - len(selected)
    all_failures = severe_loops + mild_loops + no_loops
    for f in all_failures:
        if f not in selected and len(selected) < n:
            selected.append(f)

    return selected[:n]


def run_experiment():
    """运行实验"""
    traj_dir = Path("swe_trajectories/trajectories")

    print("=" * 60)
    print("实验 2：失败轨迹的图结构化")
    print("=" * 60)

    # 选择代表性轨迹
    print("\n选择代表性失败轨迹...")
    selected = select_representative_failures(traj_dir, n=100)
    print(f"已选择 {len(selected)} 条轨迹")

    # 转换为图
    print("\n转换轨迹为图结构...")
    graphs = []
    schema_issues = defaultdict(list)

    for i, filepath in enumerate(selected):
        if i % 20 == 0:
            print(f"  进度: {i}/{len(selected)}")

        graph = convert_trajectory_to_graph(filepath)
        if graph:
            graphs.append(graph)

            # 检查 schema 表达能力问题
            # 1. 是否能捕捉循环模式？
            has_loop = any(p.pattern_type == "loop_trap" for p in graph.failure_patterns)
            if not has_loop and graph.failure_type == "loop_trap":
                schema_issues["missed_loop"].append(graph.instance_id)

            # 2. 是否能表达实体遗漏？
            # (在当前 schema 中只能通过 was_handled 字段近似)

            # 3. 错误节点是否足够？
            if not graph.error_nodes and not graph.success:
                schema_issues["no_error_captured"].append(graph.instance_id)

    # 统计
    print("\n" + "=" * 60)
    print("统计结果")
    print("=" * 60)

    total_actions = sum(len(g.action_nodes) for g in graphs)
    total_entities = sum(len(g.entity_nodes) for g in graphs)
    total_errors = sum(len(g.error_nodes) for g in graphs)
    total_patterns = sum(len(g.failure_patterns) for g in graphs)
    total_edges = sum(len(g.edges) for g in graphs)

    print(f"\n图统计 ({len(graphs)} 条轨迹):")
    print(f"  - 动作节点: {total_actions} (平均 {total_actions/len(graphs):.1f}/轨迹)")
    print(f"  - 实体节点: {total_entities} (平均 {total_entities/len(graphs):.1f}/轨迹)")
    print(f"  - 错误节点: {total_errors} (平均 {total_errors/len(graphs):.1f}/轨迹)")
    print(f"  - 失败模式: {total_patterns} (平均 {total_patterns/len(graphs):.1f}/轨迹)")
    print(f"  - 边: {total_edges} (平均 {total_edges/len(graphs):.1f}/轨迹)")

    # 失败类型分布
    failure_types = defaultdict(int)
    for g in graphs:
        failure_types[g.failure_type] += 1

    print(f"\n失败类型分布:")
    for ft, count in sorted(failure_types.items(), key=lambda x: -x[1]):
        print(f"  - {ft}: {count} ({count/len(graphs)*100:.1f}%)")

    # Schema 问题
    print(f"\nSchema 表达能力问题:")
    for issue, instances in schema_issues.items():
        print(f"  - {issue}: {len(instances)} 个案例")

    # 保存结果
    output = {
        "experiment": "experiment2_graph_structuring",
        "trajectory_count": len(graphs),
        "stats": {
            "total_actions": total_actions,
            "total_entities": total_entities,
            "total_errors": total_errors,
            "total_patterns": total_patterns,
            "total_edges": total_edges
        },
        "failure_types": dict(failure_types),
        "schema_issues": {k: v for k, v in schema_issues.items()},
        "graphs": [g.to_dict() for g in graphs]
    }

    with open("experiment2_graph_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print("\n数据已保存到 experiment2_graph_data.json")

    # 生成报告
    generate_report(graphs, schema_issues, failure_types)


def generate_report(graphs, schema_issues, failure_types):
    """生成报告"""
    report = []
    report.append("# 实验 2：失败轨迹的图结构化报告\n")

    report.append("## 1. 实验目标\n")
    report.append("将失败轨迹转换为 Context Graph 格式，验证 schema 的表达能力。\n")

    report.append("## 2. Schema 设计\n")
    report.append("### 2.1 节点类型\n")
    report.append("| 节点类型 | 描述 | 关键属性 |")
    report.append("|---------|------|---------|")
    report.append("| ActionNode | Agent 的每个动作 | action_type, command, target, is_repeated |")
    report.append("| CodeEntityNode | 代码实体 | node_type (file/function/class), was_modified |")
    report.append("| ErrorPatternNode | 错误模式 | error_type, caused_by_action |")
    report.append("| FailurePatternNode | 高层失败模式 | pattern_type, severity |")
    report.append("")

    report.append("### 2.2 边类型\n")
    report.append("| 边类型 | 描述 |")
    report.append("|-------|------|")
    report.append("| NEXT_ACTION | 动作序列 |")
    report.append("| TARGETS | 动作 -> 实体 |")
    report.append("| CAUSES | 动作 -> 错误 |")
    report.append("| MODIFIES | 动作 -> 实体 |")
    report.append("")

    report.append("## 3. 转换结果\n")
    total = len(graphs)
    total_actions = sum(len(g.action_nodes) for g in graphs)
    total_entities = sum(len(g.entity_nodes) for g in graphs)
    total_errors = sum(len(g.error_nodes) for g in graphs)
    total_patterns = sum(len(g.failure_patterns) for g in graphs)

    report.append(f"| 指标 | 值 |")
    report.append(f"|------|-----|")
    report.append(f"| 转换轨迹数 | {total} |")
    report.append(f"| 平均动作节点数 | {total_actions/total:.1f} |")
    report.append(f"| 平均实体节点数 | {total_entities/total:.1f} |")
    report.append(f"| 平均错误节点数 | {total_errors/total:.1f} |")
    report.append(f"| 平均失败模式数 | {total_patterns/total:.1f} |")
    report.append("")

    report.append("## 4. 失败类型分布\n")
    report.append("| 失败类型 | 数量 | 占比 |")
    report.append("|---------|------|------|")
    for ft, count in sorted(failure_types.items(), key=lambda x: -x[1]):
        report.append(f"| {ft} | {count} | {count/total*100:.1f}% |")
    report.append("")

    report.append("## 5. Schema 表达能力评估\n")
    report.append("### 5.1 能够表达的模式\n")
    report.append("- ✅ **循环陷阱**: 通过 ActionNode.is_repeated 和 FailurePatternNode.pattern_type='loop_trap'")
    report.append("- ✅ **技术错误**: 通过 ErrorPatternNode 和 CAUSES 边关联到具体动作")
    report.append("- ✅ **动作序列**: 通过 NEXT_ACTION 边构建完整执行路径")
    report.append("")

    report.append("### 5.2 需要改进的方面\n")

    no_error = len(schema_issues.get("no_error_captured", []))
    report.append(f"- ⚠️ **{no_error} 个案例**未能捕获具体错误（可能是任务理解层面的问题）")
    report.append("- ⚠️ **不完整重构**难以自动检测（需要知道'应该修改什么'）")
    report.append("- ⚠️ **任务理解错误**需要额外的语义分析")
    report.append("")

    report.append("### 5.3 建议增强的节点/边类型\n")
    report.append("```")
    report.append("# 建议新增")
    report.append("TaskUnderstandingNode:")
    report.append("  - task_type: bug_fix | feature_request | refactoring")
    report.append("  - agent_interpretation: str")
    report.append("  - correct_interpretation: str")
    report.append("")
    report.append("MissingEntityNode:")
    report.append("  - expected_entity: str")
    report.append("  - reason: str  # 为什么应该被处理")
    report.append("")
    report.append("SHOULD_HAVE_MODIFIED 边:")
    report.append("  - 表达'agent 应该修改但没有修改'的关系")
    report.append("```")
    report.append("")

    report.append("## 6. 典型案例展示\n")

    # 展示几个有代表性的图
    for g in graphs[:3]:
        report.append(f"### {g.instance_id}\n")
        report.append(f"- 失败类型: {g.failure_type}")
        report.append(f"- 动作数: {len(g.action_nodes)}")
        report.append(f"- 错误数: {len(g.error_nodes)}")
        report.append(f"- 失败模式: {len(g.failure_patterns)}")

        if g.failure_patterns:
            for p in g.failure_patterns[:2]:
                report.append(f"  - {p.pattern_type}: {p.description[:80]}...")
        report.append("")

    with open("EXPERIMENT2_GRAPH_STRUCTURING.md", "w", encoding="utf-8") as f:
        f.write("\n".join(report))

    print("报告已保存到 EXPERIMENT2_GRAPH_STRUCTURING.md")


if __name__ == "__main__":
    run_experiment()
