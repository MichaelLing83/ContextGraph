# Demo.py 使用指南

## 概述
`demo.py` 提供了两种演示模式，帮助你理解如何使用 ContextGraph 分析器。

---

## 模式 1：基础演示 - 分析内置示例轨迹

### 运行命令
```bash
python demo.py
```

### 它做了什么？

1. **创建示例轨迹** (`demo_output/sample.traj`)
   - 一个完整的 bug 修复过程
   - 包含 8 个步骤：查找文件 → 查看代码 → 复现问题 → 修复 → 测试 → 提交

2. **解析轨迹数据**
   - 提取动作类型（search, edit, reproduce, run_test 等）
   - 识别访问的文件（fields.py, reproduce.py 等）
   - 检测代码修改和错误

3. **提取实体和关系**
   - 实体：8 个文件节点
   - 关系：1 个 MODIFIES 关系（修改操作）

4. **构建 Context Graph**
   - 三层图结构：Episode → Semantic → Community
   - 26 条边（25个 RELATED_TO + 1个 MODIFIES）

5. **导出结果** 到 `demo_output/`
   - `graph.json` - 节点和边的标准格式
   - `notes.json` - A-MEM 格式（记忆笔记）
   - `context_graph.json` - 完整的三层图结构

6. **统计分析**
   - 成功率：100%（示例是成功案例）
   - 动作序列模式：提取常见的 3-步骤序列

### 查看结果
```bash
# 查看生成的文件
ls -lh demo_output/

# 查看图结构（美化 JSON）
cat demo_output/graph.json | python -m json.tool | less

# 查看完整 Context Graph
cat demo_output/context_graph.json | python -m json.tool | less

# 查看 A-MEM 笔记
cat demo_output/notes.json | python -m json.tool
```

---

## 模式 2：HuggingFace 数据演示

### 运行命令
```bash
python demo.py --huggingface
```

### 它做了什么？

1. **从 HuggingFace 下载数据**
   - 数据集：SWE-bench Lite
   - 300 个测试实例
   - 12 个不同的 GitHub 仓库

2. **显示数据统计**
   - 总实例数
   - 涉及的仓库列表
   - 示例问题描述

3. **数据会缓存到本地**
   - 位置：`~/.cache/swebench_analyzer/`
   - 下次运行更快

### 示例输出
```
============================================================
Loading data from HuggingFace...
============================================================

Loaded 300 instances from SWE-bench Lite
Unique repositories: 12

Example instance:
  ID: astropy__astropy-12907
  Repo: astropy/astropy
  Problem statement (first 200 chars):
  Modeling's `separability_matrix` does not compute...
```

---

## 内置示例轨迹详解

### 问题场景
修复 marshmallow 库中 `TimeDelta` 字段序列化的整数截断 bug。

### Agent 执行的 8 个步骤

| 步骤 | 动作类型 | 操作 | 结果 |
|-----|---------|------|------|
| 1 | **search** | `find . -name 'fields.py'` | 找到目标文件 |
| 2 | **navigate** | `open src/marshmallow/fields.py` | 查看第 1474 行代码 |
| 3 | **edit** | 创建 `reproduce.py` | 写测试脚本 |
| 4 | **reproduce** | `python reproduce.py` | 复现 bug（输出 344 而非 345） |
| 5 | **edit** | 修改第 1474-1475 行 | 将 `int()` 改为 `round()` |
| 6 | **reproduce** | 再次运行测试 | 验证修复（输出 345） |
| 7 | **run_test** | `pytest tests/test_fields.py` | 所有测试通过 |
| 8 | **generate_fix** | `submit` | 提交修复 |

### 提取的模式
```python
# 成功的动作序列模式（3步）
('search', 'navigate', 'edit')       # 先找文件，再查看，然后编辑
('edit', 'reproduce', 'edit')        # 写测试 → 复现 → 修复
('reproduce', 'run_test', 'generate_fix')  # 验证 → 测试 → 提交
```

---

## 输出文件详解

### 1. graph.json
标准图格式，包含：
- **nodes**: 实体列表（文件、函数、类等）
- **edges**: 关系列表（调用、修改、导入等）
- **episodes**: 轨迹元数据

```json
{
  "nodes": [
    {
      "entity_id": "465381cc8a1b",
      "entity_type": "file",
      "name": "fields.py",
      "file_path": "fields.py",
      "episode_id": "sample"
    }
  ],
  "edges": [
    {
      "relation_type": "MODIFIES",
      "source_id": "action_4",
      "target_id": "0cccd0c5a388"
    }
  ]
}
```

### 2. notes.json (A-MEM 格式)
记忆笔记结构，用于 agent 记忆系统：
```json
{
  "note_id": "sample",
  "content": "问题描述...",
  "keywords": ["fields.py", "TimeDelta", ...],
  "tags": ["marshmallow-code__marshmallow", "resolved"],
  "linked_entities": [...],
  "linked_relations": [...]
}
```

### 3. context_graph.json
完整的三层知识图谱：
- **Episode Layer**: 原始交互记录
- **Semantic Layer**: 代码实体（文件、函数等）
- **Community Layer**: 高层概念（功能模块等）

---

## 如何扩展 Demo

### 1. 分析你自己的轨迹文件
```python
from analyzer import TrajectoryParser, TrajectoryAnalyzer
from pathlib import Path

# 解析自定义轨迹
parser = TrajectoryParser()
trajectory = parser.parse_swe_agent_trajectory(Path('your_trajectory.traj'))

# 分析
analyzer = TrajectoryAnalyzer()
analyzer.add_trajectory(trajectory)
stats = analyzer.compute_statistics()
print(stats)
```

### 2. 批量分析多个轨迹
```python
from pathlib import Path

# 分析目录中的所有轨迹
for traj_file in Path('trajectories/').glob('*.traj'):
    traj = parser.parse_swe_agent_trajectory(traj_file)
    analyzer.add_trajectory(traj)

# 对比成功 vs 失败
comparison = analyzer.compare_resolved_vs_unresolved()
print(comparison)
```

### 3. 筛选失败案例
```python
# 只分析失败的轨迹
failed_trajectories = [
    t for t in analyzer.trajectories
    if not t.is_resolved
]

print(f"Found {len(failed_trajectories)} failed cases")
for traj in failed_trajectories:
    print(f"  {traj.instance_id}: {traj.total_steps} steps")
```

### 4. 自定义导出格式
```python
from analyzer import GraphExporter

exporter = GraphExporter()

# 导出为不同格式
exporter.export_to_json(trajectories, Path('custom_output.json'))
exporter.export_for_graphiti(trajectories, Path('neo4j_import.cypher'))
exporter.export_for_amem(trajectories, Path('agent_memory.json'))
```

---

## 常见问题

### Q1: 如何获取真实的轨迹数据？
```bash
# 方法 1: 从 HuggingFace 加载
python -c "from data_loader import HuggingFaceLoader; \
           loader = HuggingFaceLoader(); \
           list(loader.load_trajectories('nebius/SWE-agent-trajectories'))"

# 方法 2: 克隆 experiments 仓库
git clone https://github.com/SWE-bench/experiments
cd experiments/evaluation/lite/
ls -d */  # 查看所有提交的方法
```

### Q2: demo 生成的文件可以删除吗？
可以，`demo_output/` 是临时输出目录：
```bash
rm -rf demo_output/
```

### Q3: 如何只分析失败案例？
修改 demo.py 或创建自定义脚本：
```python
# 标记为失败案例
trajectory.is_resolved = False

# 或在分析时筛选
failed = [t for t in analyzer.trajectories if not t.is_resolved]
```

### Q4: 支持哪些轨迹格式？
- ✅ SWE-agent 格式（`.traj` 文件）
- ✅ OpenHands 格式（tool_calls 结构）
- 🔜 其他格式（可扩展 `TrajectoryParser`）

---

## 下一步

完成 demo 后，你可以：

1. **分析真实数据**
   - 下载 SWE-bench 实验结果
   - 分析不同 agent 的表现差异

2. **添加失败分析功能**
   - 提取失败模式
   - 对比成功/失败的关键差异

3. **构建知识图谱**
   - 使用 Context Graph 存储经验
   - 实现基于图的检索和推理

4. **可视化结果**
   - 使用 Cypher 导入 Neo4j
   - 绘制动作序列图

---

## 参考资料

- [SWE-bench 官网](https://www.swebench.com/)
- [SWE-bench/experiments 仓库](https://github.com/SWE-bench/experiments)
- [Nebius SWE-agent 轨迹数据集](https://huggingface.co/datasets/nebius/SWE-agent-trajectories)
