# Agent Memory (ContextGraph V2) 设计文档

## 概述

Agent 的长期记忆系统。每条轨迹都会更新记忆，每次 Agent 运行都可以查询记忆获取指导。

## 设计决策

| 决策项 | 选择 |
|--------|------|
| 用途 | Agent 长期记忆（持续学习 + 实时查询） |
| 记忆粒度 | 分层：轨迹摘要 + 关键片段 |
| 检索维度 | 多维：错误 / 任务 / 状态 / 语义 |
| 存储方式 | Neo4j 图数据库 |
| 语义匹配 | API embedding（当前 OpenAI；Anthropic 作为后续扩展） |
| 更新策略 | 增量写入 + 每 16 条轨迹定期整理 |
| 方法论格式 | 自然语言（可插拔接口，方便 ablation） |

---

## 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    AgentMemory (ContextGraph V2)                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    Neo4j Graph Store                      │   │
│  │                                                           │   │
│  │   [Trajectory]──HAS_FRAGMENT──>[Fragment]                │   │
│  │                                  │                       │   │
│  │                                  └──CAUSED_ERROR──>[ErrorPattern] │   │
│  │                                                           │   │
│  │                      [Methodology]<──RESOLVED_BY──────────┘   │   │
│  │                                                           │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│              ┌───────────────┼───────────────┐                  │
│              ↓               ↓               ↓                  │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐       │
│  │  Writer       │  │  Retriever    │  │  Consolidator │       │
│  │  (增量写入)    │  │  (多维检索)    │  │  (定期整理)    │       │
│  └───────────────┘  └───────────────┘  └───────────────┘       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**三个核心组件**：
1. **Writer** - 轨迹结束后立即写入原始记录（Trajectory + Fragments + ErrorPatterns；State 仅运行时构建）
2. **Retriever** - 运行时多维检索（错误/任务/状态/语义）
3. **Consolidator** - 每 16 条轨迹执行整理（抽象方法论、合并相似、更新统计、清理低质量）

---

## 图节点定义

### Trajectory（轨迹摘要）

```python
@dataclass
class Trajectory:
    id: str
    instance_id: str          # SWE-bench instance
    repo: str                 # 代码仓名
    task_type: str            # bug_fix / feature / refactor
    success: bool
    total_steps: int
    summary: str              # 自然语言摘要
    embedding: List[float]    # 语义向量
```

### Fragment（关键片段）

```python
@dataclass
class Fragment:
    id: str
    step_range: Tuple[int, int]   # 起止步骤
    fragment_type: str            # error_recovery / exploration /
                                  # successful_fix / failed_attempt / loop
    description: str              # 自然语言描述
    action_sequence: List[str]    # 动作序列
    outcome: str                  # 结果
    embedding: List[float]
```

### State（状态快照）

```python
@dataclass
class State:
    tools: List[str]          # a. 可用工具
    repo_summary: str         # b. 仓库概述
    task_description: str     # c. 任务描述
    current_error: str        # d. 当前错误
    phase: str                # understanding / locating / fixing
    embedding: List[float]
```

> 注：当前实现中 `State` 主要作为检索输入的运行时快照，不在 Neo4j 中持久化为节点。

### Methodology（方法论）

由 Consolidator 从成功的 Fragments 抽象生成。

```python
@dataclass
class Methodology:
    id: str
    situation: str            # 适用情境 (自然语言)
    strategy: str             # 策略建议 (自然语言)
    confidence: float         # 置信度
    success_count: int
    failure_count: int
    embedding: List[float]
```

---

## 图关系定义

```
Trajectory ──HAS_FRAGMENT──> Fragment
Fragment ──CAUSED_ERROR──> ErrorPattern
ErrorPattern ──RESOLVED_BY──> Methodology
```

> 当前已实现的持久化关系为以上三类。`State` 仍用于检索输入，但暂未持久化为图节点关系。

### 关系属性

| 关系 | 属性 |
|------|------|
| `HAS_FRAGMENT` | order (顺序) |
| `CAUSED_ERROR` | （当前无额外属性） |
| `RESOLVED_BY` | success_rate, avg_steps |

---

## 组件详细设计

### Writer（增量写入）

```python
class MemoryWriter:
    """轨迹结束后立即写入原始记录"""

    def write_trajectory(self, trajectory: RawTrajectory) -> str:
        """
        写入流程：
        1. 解析轨迹 → 提取 Fragments
        2. 为每个 Fragment 构建 State 快照
        3. 提取 ErrorPatterns
        4. 生成 embeddings (批量 API 调用)
        5. 写入 Neo4j
        """

    def _segment_into_fragments(self, trajectory) -> List[Fragment]:
        """
        切分策略：
        - 遇到错误 → 开始新片段
        - 错误恢复成功 → 结束片段
        - 切换任务阶段 → 新片段
        - 连续相同动作 3+ 次 → 标记为 loop
        """

    def _extract_state_at_step(self, trajectory, step: int) -> State:
        """
        从轨迹中提取该步骤的状态：
        - tools: 从 system prompt 提取
        - repo_summary: 从早期探索动作推断
        - task_description: 从 problem_statement
        - current_error: 从最近的错误输出
        """

    def _identify_error_patterns(self, fragments) -> List[ErrorPattern]:
        """
        错误模式识别：
        - 提取错误类型 (ImportError, AttributeError, etc.)
        - 提取错误上下文关键词
        - 记录错误发生位置
        """
```

### Retriever（多维检索）

```python
class MemoryRetriever:
    """运行时多维检索"""

    def retrieve(self, current_state: State, top_k: int = 5) -> RetrievalResult:
        """
        综合检索入口，返回：
        - methodologies: 适用的方法论
        - similar_fragments: 相似的历史片段
        - error_solutions: 错误的已知解决方案
        - warnings: 可能的失败模式警告
        """

    def by_error(self, error_type: str, error_msg: str) -> List[Methodology]:
        """
        错误驱动检索：
        MATCH (e:ErrorPattern)-[:RESOLVED_BY]->(m:Methodology)
        WHERE e.type = $error_type
        RETURN m ORDER BY m.success_rate DESC
        """

    def by_task(self, task_type: str, repo_context: str) -> List[Fragment]:
        """
        任务驱动检索：
        MATCH (t:Trajectory)-[:HAS_FRAGMENT]->(f:Fragment)
        WHERE t.task_type = $task_type AND t.success = true
        RETURN f ORDER BY f.outcome
        """

    def by_state(self, state: State) -> List[Methodology]:
        """
        状态驱动检索：
        MATCH (m:Methodology)
        WHERE m.situation CONTAINS $phase
        RETURN m ORDER BY m.confidence DESC LIMIT 5
        """

    def by_semantic(self, query_embedding: List[float], node_type: str) -> List[Node]:
        """
        语义驱动检索：
        基于 embedding 余弦相似度
        支持查 Fragment / Methodology / State
        """

    def _fuse_results(self, results: Dict[str, List]) -> List[RankedItem]:
        """
        多路结果融合：
        - 出现在多个维度 → 加分
        - 高置信度 → 加分
        - 最近使用 → 轻微加分
        """
```

### Consolidator（定期整理）

每 16 条轨迹执行一次。

```python
class MemoryConsolidator:
    """定期批量整理：抽象、合并、清理"""

    def consolidate(self, batch_size: int = 16):
        """
        整理流程：
        1. 抽象方法论
        2. 合并相似节点
        3. 更新统计信息
        4. 清理低质量数据
        """

    def _abstract_methodologies(self):
        """
        从成功的 Fragments 抽象出 Methodology：

        1. 找到未被抽象的成功片段
        2. 按 (error_type, task_type) 分组
        3. 用 LLM 生成自然语言方法论：

           Prompt:
           "以下是 {n} 个成功解决 {error_type} 的案例：
            {fragment_descriptions}

            请总结出一条通用的方法论，格式：
            - 适用情境：...
            - 建议策略：..."

        4. 创建 Methodology 节点，并通过 ErrorPattern 建立 `RESOLVED_BY` 关联
        """

    def _merge_similar_nodes(self, similarity_threshold: float = 0.9):
        """
        合并高度相似的节点：
        - 找 SIMILAR_TO 边权重 > threshold 的 Fragment 对
        - 保留信息更丰富的一个
        - 更新指向关系
        """

    def _update_statistics(self):
        """
        更新关系上的统计属性：
        - RESOLVED_BY.success_rate = 成功次数 / 总次数
        - ErrorPattern.frequency = 与 Fragment 的 `CAUSED_ERROR` 关联计数
        - Methodology.confidence = 基于来源数量和成功率
        """

    def _cleanup(self):
        """
        清理低质量数据：
        - 删除孤立节点（无边连接）
        - 删除过期的失败片段（保留最近 N 个）
        - 删除低置信度的 Methodology（< 0.3 且来源 < 3）
        """
```

### LoopDetector（循环检测）

基于失败原因一致性，而非仅看动作相同。

```python
class LoopDetector:
    """基于失败原因一致性的循环检测"""

    def detect(self, state_history: List[State]) -> Optional[LoopInfo]:
        """
        核心逻辑：
        真正的循环 = 相同动作 + 相同错误模式
        """

    def _build_signatures(self, states: List[State]) -> List[LoopSignature]:
        """
        为每个状态构建签名：
        - action_type: 当前执行的动作类型
        - error_category: 错误大类 (ImportError, SyntaxError...)
        - error_keywords: 错误消息关键词 (top 3)
        """

    def _find_repeating_signatures(self, signatures: List[LoopSignature],
                                    min_repeat: int = 3) -> List[LoopMatch]:
        """
        查找重复的签名序列：

        不只是看 action 相同，还要看：
        1. error_category 相同
        2. error_keywords 有交集 (>= 1 个相同)

        返回匹配到的循环位置和长度
        """

    def is_same_predicament(self, state1: State, state2: State) -> bool:
        """
        判断是否陷入同样困境：

        return (
            state1.action_type == state2.action_type AND
            state1.error_category == state2.error_category AND
            keyword_overlap(state1.error_keywords, state2.error_keywords) >= 1
        )
        """

    def get_escape_suggestions(self, loop_info: LoopInfo) -> List[Methodology]:
        """
        检测到循环后，从记忆中检索逃脱建议：

        1. 找相似的历史循环片段
        2. 看哪些成功逃脱了
        3. 返回对应的方法论
        """
```

### StatisticsEngine（统计引擎）

```python
class StatisticsEngine:
    """错误频率、失败模式、转移概率统计"""

    def get_error_frequency(self) -> Dict[str, int]:
        """
        什么错误最常见：
        MATCH (e:ErrorPattern)
        RETURN e.error_type, count(*) ORDER BY count DESC
        """

    def get_error_transitions(self) -> Dict[str, Dict[str, float]]:
        """
        当前实现先支持错误频次统计（转移关系可作为后续扩展）：
        MATCH (f:Fragment)-[:CAUSED_ERROR]->(e:ErrorPattern)
        RETURN e.error_type, count(f) AS frequency

        返回: {"ImportError": 12, "TypeError": 7}
        """

    def get_common_mistakes(self, situation: str) -> List[MistakePattern]:
        """
        某情境下的常见错误：
        MATCH (t:Trajectory)-[:HAS_FRAGMENT]->(f:Fragment)
        WHERE f.outcome = 'failed'
          AND toLower(coalesce(t.summary, '')) CONTAINS toLower($situation)
        RETURN f.description, count(*) as freq
        ORDER BY freq DESC LIMIT 10
        """

    def get_failure_patterns(self) -> List[FailurePattern]:
        """
        高频失败模式：
        - 什么条件容易失败
        - 经常犯什么错
        - 如何预防

        由 Consolidator 定期生成并缓存
        """

    def update_from_batch(self, trajectories: List[Trajectory]):
        """
        批量更新统计：
        1. 更新 ErrorPattern 节点的 count
        2. 基于 `CAUSED_ERROR` 关系更新 ErrorPattern.frequency
        3. 重新计算 RESOLVED_BY.success_rate
        """
```

---

## 统一 API

```python
class AgentMemory:
    """Agent 长期记忆 - 统一入口"""

    def __init__(self, neo4j_uri: str, embedding_api_key: str):
        self.graph = Neo4jConnection(neo4j_uri)
        self.embedder = EmbeddingClient(embedding_api_key)

        self.writer = MemoryWriter(self.graph, self.embedder)
        self.retriever = MemoryRetriever(self.graph, self.embedder)
        self.consolidator = MemoryConsolidator(self.graph, self.embedder)
        self.loop_detector = LoopDetector()
        self.stats = StatisticsEngine(self.graph)

        self._trajectory_count = 0
        self._consolidate_every = 16

    # === Agent 运行时调用 ===

    def query(self, current_state: State) -> MemoryContext:
        """
        每步执行前调用，返回记忆上下文：
        - suggestions: 推荐的方法论
        - warnings: 失败模式警告
        - similar_cases: 相似历史案例
        """
        return self.retriever.retrieve(current_state)

    def check_loop(self, state_history: List[State]) -> Optional[LoopInfo]:
        """
        检测是否陷入循环（基于错误一致性）
        """
        return self.loop_detector.detect(state_history)

    # === 轨迹结束后调用 ===

    def learn(self, trajectory: RawTrajectory):
        """
        学习一条新轨迹：
        1. 立即写入原始数据
        2. 每 16 条触发整理
        """
        self.writer.write_trajectory(trajectory)

        self._trajectory_count += 1
        if self._trajectory_count % self._consolidate_every == 0:
            self.consolidator.consolidate()

    # === 统计查询 ===

    def get_stats(self) -> MemoryStats:
        """获取统计信息"""
        return MemoryStats(
            error_frequency=self.stats.get_error_frequency(),
            common_mistakes=self.stats.get_common_mistakes,
            failure_patterns=self.stats.get_failure_patterns()
        )
```

---

## 使用示例

```python
memory = AgentMemory(
    neo4j_uri="bolt://localhost:7687",
    embedding_api_key="sk-..."
)

# Agent 运行循环
for step in agent.run():
    # 1. 查询记忆获取指导
    context = memory.query(step.state)
    agent.inject_context(context)

    # 2. 检测循环
    loop = memory.check_loop(step.state_history)
    if loop:
        agent.handle_loop(loop.escape_suggestions)

# 轨迹结束后学习
memory.learn(agent.trajectory)
```

---

## 实现计划

1. **阶段 1**：基础设施
   - Neo4j 连接和 schema 初始化
   - Embedding API 客户端
   - 基础数据结构定义

2. **阶段 2**：Writer
   - 轨迹解析和片段切分
   - State 提取
   - ErrorPattern 识别
   - 写入 Neo4j

3. **阶段 3**：Retriever
   - 四种检索维度实现
   - 结果融合排序

4. **阶段 4**：Consolidator
   - 方法论抽象（LLM 调用）
   - 相似节点合并
   - 统计更新
   - 数据清理

5. **阶段 5**：LoopDetector
   - 签名构建
   - 基于错误一致性的循环检测
   - 逃脱建议检索

6. **阶段 6**：集成测试
   - 端到端测试
   - 性能优化
