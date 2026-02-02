# SWE-bench Trajectory Analyzer

分析 SWE-bench agent 轨迹，为 Coding 场景构建 Context Graph。

## 设计参考

本工具的设计参考了以下论文：

1. **Zep: A Temporal Knowledge Graph Architecture for Agent Memory** (Rasmussen et al., 2025)
   - 三层图架构：Episode → Semantic → Community
   - Bi-temporal 时间建模
   - Entity Resolution 和 Edge Invalidation

2. **A-MEM: Agentic Memory for LLM Agents** (Xu et al., 2025)
   - 基于 Zettelkasten 的动态链接
   - Memory Evolution 机制
   - Note 结构设计

3. **Context Graph** (Xu et al., 2024)
   - Entity Context 和 Relation Context
   - 丰富的边上下文信息

4. **ExpeL: LLM Agents are Experiential Learners** (Zhao et al., 2024)
   - 从成功/失败轨迹中提取经验模式

## 安装

```bash
# 基础依赖
pip install datasets  # 用于从 HuggingFace 加载数据

# 可选依赖
pip install boto3     # 用于从 S3 加载实验数据
pip install sentence-transformers  # 用于生成 embedding
```

## 文件结构

```
swebench_trajectory_analyzer/
├── analyzer.py         # 主分析模块：轨迹解析、实体提取、统计分析
├── data_loader.py      # 数据加载：HuggingFace、本地文件、S3
├── context_graph.py    # Context Graph 实现
├── demo.py             # 演示脚本
└── README.md           # 本文档
```

## 快速开始

### 1. 运行演示

```bash
cd swebench_trajectory_analyzer
python demo.py
```

这会使用一个内置的示例轨迹进行分析，输出到 `demo_output/` 目录。

### 2. 从 HuggingFace 加载数据

```bash
python demo.py --huggingface
```

### 3. 分析本地轨迹文件

```python
from analyzer import TrajectoryParser, TrajectoryAnalyzer, GraphExporter
from pathlib import Path

# 解析轨迹
parser = TrajectoryParser()
traj = parser.parse_swe_agent_trajectory(Path('path/to/trajectory.traj'))

# 添加到分析器
analyzer = TrajectoryAnalyzer()
analyzer.add_trajectory(traj)

# 获取统计
stats = analyzer.compute_statistics()
print(stats)

# 对比成功/失败案例
comparison = analyzer.compare_resolved_vs_unresolved()

# 提取成功模式
patterns = analyzer.extract_success_patterns()
```

### 4. 构建 Context Graph

```python
from context_graph import ContextGraph, GraphBuilder, SemanticNode, Edge

# 从轨迹构建图
builder = GraphBuilder()
graph = builder.build_from_trajectory(trajectory)

# 或手动构建
graph = ContextGraph()

# 添加节点
file_node = SemanticNode(
    node_id='file_001',
    node_type='file',
    name='fields.py',
    file_path='src/marshmallow/fields.py',
)
graph.add_semantic_node(file_node)

# 添加边
edge = Edge(
    edge_id='edge_001',
    edge_type='DEFINED_IN',
    source_id='func_001',
    target_id='file_001',
    fact='_serialize is defined in fields.py',
)
graph.add_edge(edge)

# 生成链接 (A-MEM 风格)
new_links = graph.generate_links(new_node)

# 查询子图 (Zep 风格 BFS)
subgraph = graph.bfs_subgraph('func_001', max_hops=2)

# 保存
graph.save(Path('output/context_graph.json'))
```

### 5. 导出格式

```python
from analyzer import GraphExporter

exporter = GraphExporter()

# JSON 格式 (通用)
exporter.export_to_json(trajectories, Path('output/graph.json'))

# Graphiti 格式 (Cypher)
exporter.export_for_graphiti(trajectories, Path('output/graph.cypher'))

# A-MEM 格式 (Note 结构)
exporter.export_for_amem(trajectories, Path('output/notes.json'))
```

## Context Graph Schema

### 节点类型

| 层级 | 类型 | 描述 |
|------|------|------|
| Episode | `episode` | 原始交互记录（thought, action, observation） |
| Semantic | `file` | 代码文件 |
| Semantic | `function` | 函数/方法 |
| Semantic | `class` | 类定义 |
| Semantic | `variable` | 变量 |
| Semantic | `error_pattern` | 错误模式 |
| Semantic | `module` | 模块/包 |
| Community | `community` | 高阶概念（功能模块、bug 类型等） |

### 边类型

| 类型 | 源 → 目标 | 描述 |
|------|-----------|------|
| `CALLS` | function → function | 函数调用 |
| `IMPORTS` | file → module | 导入关系 |
| `DEFINED_IN` | function/class → file | 定义位置 |
| `MODIFIES` | action → entity | 修改操作 |
| `CAUSED_BY` | error → entity | 错误来源 |
| `RAISED_BY` | error → function | 错误抛出 |
| `SIMILAR_TO` | entity → entity | 相似关系 |
| `EVOLVED_FROM` | entity → entity | 演化关系 |
| `RELATED_TO` | any → any | 通用关联 |

### 时间属性 (Bi-temporal)

参考 Zep 的设计：

- `t_created`: 系统记录时间（何时加入图）
- `t_valid`: 事实有效时间（何时开始为真）
- `t_invalid`: 事实失效时间（用于 Edge Invalidation）

## 分析功能

### 统计分析

- 轨迹数量、解决率
- 步骤数分布
- 动作类型分布
- 实体/关系类型分布

### 成功/失败对比

- 步骤数差异
- 动作模式差异
- 实体覆盖差异
- 错误处理差异

### 模式提取 (ExpeL 风格)

- 常见动作序列
- 关键实体类型
- 成功案例特征

## 下一步

1. **添加 Embedding 支持**：使用 sentence-transformers 生成节点 embedding
2. **实现 LLM 辅助**：用 LLM 做更精确的 entity resolution 和 link generation
3. **集成 Neo4j**：支持图数据库存储和查询
4. **可视化**：添加图可视化功能

## 相关资源

- [SWE-bench 官网](https://www.swebench.com/)
- [SWE-agent 文档](https://swe-agent.com/)
- [SWE-bench/experiments](https://github.com/SWE-bench/experiments)
- [Nebius SWE-agent-trajectories](https://huggingface.co/datasets/nebius/SWE-agent-trajectories)
