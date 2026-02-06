# 实验 2：失败轨迹的图结构化报告

## 1. 实验目标

将失败轨迹转换为 Context Graph 格式，验证 schema 的表达能力。

## 2. Schema 设计

### 2.1 节点类型

| 节点类型 | 描述 | 关键属性 |
|---------|------|---------|
| ActionNode | Agent 的每个动作 | action_type, command, target, is_repeated |
| CodeEntityNode | 代码实体 | node_type (file/function/class), was_modified |
| ErrorPatternNode | 错误模式 | error_type, caused_by_action |
| FailurePatternNode | 高层失败模式 | pattern_type, severity |

### 2.2 边类型

| 边类型 | 描述 |
|-------|------|
| NEXT_ACTION | 动作序列 |
| TARGETS | 动作 -> 实体 |
| CAUSES | 动作 -> 错误 |
| MODIFIES | 动作 -> 实体 |

## 3. 转换结果

| 指标 | 值 |
|------|-----|
| 转换轨迹数 | 100 |
| 平均动作节点数 | 31.1 |
| 平均实体节点数 | 4.5 |
| 平均错误节点数 | 15.6 |
| 平均失败模式数 | 2.9 |

## 4. 失败类型分布

| 失败类型 | 数量 | 占比 |
|---------|------|------|
| loop_trap | 34 | 34.0% |
| error_accumulation | 34 | 34.0% |
| technical_error | 30 | 30.0% |
| unknown | 2 | 2.0% |

## 5. Schema 表达能力评估

### 5.1 能够表达的模式

- ✅ **循环陷阱**: 通过 ActionNode.is_repeated 和 FailurePatternNode.pattern_type='loop_trap'
- ✅ **技术错误**: 通过 ErrorPatternNode 和 CAUSES 边关联到具体动作
- ✅ **动作序列**: 通过 NEXT_ACTION 边构建完整执行路径

### 5.2 需要改进的方面

- ⚠️ **2 个案例**未能捕获具体错误（可能是任务理解层面的问题）
- ⚠️ **不完整重构**难以自动检测（需要知道'应该修改什么'）
- ⚠️ **任务理解错误**需要额外的语义分析

### 5.3 建议增强的节点/边类型

```
# 建议新增
TaskUnderstandingNode:
  - task_type: bug_fix | feature_request | refactoring
  - agent_interpretation: str
  - correct_interpretation: str

MissingEntityNode:
  - expected_entity: str
  - reason: str  # 为什么应该被处理

SHOULD_HAVE_MODIFIED 边:
  - 表达'agent 应该修改但没有修改'的关系
```

## 6. 典型案例展示

### F5Networks__f5-icontrol-rest-python-124

- 失败类型: loop_trap
- 动作数: 28
- 错误数: 20
- 失败模式: 13
  - loop_trap: Repeated command 'edit 278:292' 13 times...
  - technical_error: Edit caused type_error...

### netromdk__vermin-173

- 失败类型: loop_trap
- 动作数: 52
- 错误数: 43
- 失败模式: 1
  - loop_trap: Repeated command 'edit 8:8' 43 times...

### iterative__dvc-6933

- 失败类型: loop_trap
- 动作数: 226
- 错误数: 108
- 失败模式: 1
  - loop_trap: Repeated command 'edit 5:5' 6 times...
