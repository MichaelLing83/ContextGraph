# SWE-agent 轨迹数据下载指南

## 已完成的下载

### ✅ 主要数据源

#### 1. HuggingFace - nebius/SWE-agent-trajectories
- **数据集地址**: https://huggingface.co/datasets/nebius/SWE-agent-trajectories
- **下载方式**: 流式下载（memory-efficient）
- **保存位置**: `swe_trajectories/trajectories/`
- **状态**: ✅ 下载中/已完成

**特点**:
- SWE-agent 在不同模型上的执行轨迹
- 包含完整的对话历史（thought → action → observation）
- 包含成功和失败案例
- 模型: llama-70b, llama-8b 等

#### 2. SWE-bench/experiments Repository
- **仓库地址**: https://github.com/SWE-bench/experiments
- **克隆位置**: `swe_bench_experiments/`
- **状态**: ✅ 已完成

**包含内容**:
- 多个方法的评估结果
- 评估分类：
  - `lite/` - 300 个测试实例
  - `verified/` - 验证的子集
  - `test/` - 完整测试集
  - `multimodal/` - 多模态任务

**可用方法** (部分):
```
20240402_sweagent_claude3opus
20240402_sweagent_gpt4
20240620_sweagent_claude3.5sonnet
20240523_aider
20240530_autocoderover-v20240408
20240612_MASAI_gpt4o
20240617_moatless_gpt4o
20240621_autocoderover-v20240620
... (更多)
```

---

## 下载脚本使用

### 1. download_trajectories.py

下载 HuggingFace 上的轨迹数据。

#### 基本用法
```bash
# 下载所有可用轨迹
python download_trajectories.py --dataset nebius/SWE-agent-trajectories

# 下载指定数量
python download_trajectories.py --dataset nebius/SWE-agent-trajectories --max 1000

# 指定输出目录
python download_trajectories.py --output my_trajectories --dataset nebius/SWE-agent-trajectories
```

#### 参数说明
- `--output, -o`: 输出目录 (默认: swe_trajectories)
- `--max, -m`: 最大下载数量 (默认: 全部)
- `--dataset, -d`: 数据集名称
- `--method`: 下载方式 (streaming/full)

### 2. analyze_downloaded_data.py

分析下载的轨迹数据。

```bash
# 基本分析
python analyze_downloaded_data.py

# 指定输入输出
python analyze_downloaded_data.py \
  --input swe_trajectories/trajectories \
  --output analysis_report.json \
  --failed-list failed_cases.json
```

**输出**:
- `analysis_report.json` - 完整统计报告
- `failed_cases.json` - 失败案例列表

---

## 数据结构

### 轨迹文件格式 (JSON)

```json
{
  "instance_id": "repo__project-123",
  "model_name": "swe-agent-llama-70b",
  "target": false,  // false = 失败，true = 成功
  "trajectory": [
    {
      "role": "system",
      "text": "System prompt..."
    },
    {
      "role": "user",
      "text": "Issue description..."
    },
    {
      "role": "ai",
      "text": "DISCUSSION\n...\n```\ncommand\n```"
    },
    {
      "role": "user",
      "text": "Command output..."
    }
    // ... 更多步骤
  ]
}
```

### 目录结构

```
ContextGraph/
├── swe_trajectories/              # 主下载目录
│   ├── trajectories/              # 轨迹 JSON 文件
│   │   ├── repo__project-1.json
│   │   ├── repo__project-2.json
│   │   └── ...
│   └── metadata/                  # 元数据
│       └── download_stats.json    # 下载统计
│
├── swe_bench_experiments/         # SWE-bench experiments 仓库
│   └── evaluation/
│       ├── lite/                  # 300 个测试实例
│       ├── verified/              # 验证子集
│       └── test/                  # 完整测试集
│
├── download_trajectories.py       # 下载脚本
├── analyze_downloaded_data.py     # 分析脚本
├── trajectory_analysis.json       # 分析报告
└── failed_trajectories.json       # 失败案例列表
```

---

## 数据统计示例

基于初步下载的数据：

### 模型分布
- `swe-agent-llama-70b`: 主要模型
- `swe-agent-llama-8b`: 较小模型

### 成功率
- 总体成功率: ~10-15%
- 平均步骤数: 40-50 步

### 仓库分布
热门仓库包括：
- asottile__pyupgrade
- iterative__dvc
- dask__dask
- fairlearn__fairlearn
- 等等

---

## 其他可用数据集

### HuggingFace 数据集

1. **princeton-nlp/SWE-bench**
   - 原始 SWE-bench 数据集
   - 包含问题描述和 gold patches
   - 不含轨迹数据

2. **SWE-bench/SWE-bench_Lite**
   - 300 个测试实例
   - 更快速的评估子集

3. **SWE-bench/SWE-bench_Verified**
   - 经过验证的高质量子集

### 下载方式

```python
from data_loader import HuggingFaceLoader

loader = HuggingFaceLoader()

# 加载 SWE-bench Lite
instances = loader.load_dataset('swe-bench-lite', 'test')

# 加载 SWE-bench Verified
instances = loader.load_dataset('swe-bench-verified', 'test')
```

---

## 数据使用示例

### 1. 加载单个轨迹

```python
import json
from pathlib import Path

# 读取轨迹文件
with open('swe_trajectories/trajectories/repo__project-123.json') as f:
    trajectory = json.load(f)

print(f"Instance: {trajectory['instance_id']}")
print(f"Model: {trajectory['model_name']}")
print(f"Success: {trajectory['target']}")
print(f"Steps: {len(trajectory['trajectory'])}")
```

### 2. 批量分析轨迹

```python
from analyzer import TrajectoryParser, TrajectoryAnalyzer
from pathlib import Path

parser = TrajectoryParser()
analyzer = TrajectoryAnalyzer()

# 加载所有轨迹
for traj_file in Path('swe_trajectories/trajectories').glob('*.json'):
    # 注意：nebius 格式需要转换为 SWE-agent 格式
    # 或者直接用 JSON 处理
    pass

# 统计分析
stats = analyzer.compute_statistics()
```

### 3. 筛选失败案例

```python
import json
from pathlib import Path

failed_trajectories = []

for traj_file in Path('swe_trajectories/trajectories').glob('*.json'):
    with open(traj_file) as f:
        traj = json.load(f)
        if not traj.get('target', False):
            failed_trajectories.append(traj)

print(f"Found {len(failed_trajectories)} failed cases")
```

---

## 注意事项

### 1. 数据去重
- 同一个 instance_id 可能有多次运行
- 文件名基于 instance_id，后下载的会覆盖先下载的
- 如果需要保留所有运行，修改保存逻辑添加时间戳

### 2. 存储空间
- 每个轨迹文件: 20-200 KB
- 1000 个轨迹约: 50-150 MB
- 建议预留: 500 MB - 1 GB

### 3. 下载速度
- 流式下载: 10-100 条/秒 (波动较大)
- 取决于网络和 HuggingFace API
- 下载 1000 条约需: 30-60 秒

### 4. 数据格式差异
- nebius/SWE-agent-trajectories 使用对话格式
- 原始 SWE-agent 格式略有不同
- 需要适配 parser 或转换格式

---

## 下一步

1. **完成数据下载**
   - 等待当前下载完成
   - 验证数据完整性

2. **数据分析**
   - 运行 analyze_downloaded_data.py
   - 生成统计报告

3. **失败路径分析**
   - 创建专门的失败分析模块
   - 提取失败模式和原因

4. **构建知识图谱**
   - 使用现有的 Context Graph 工具
   - 分析成功与失败的差异

---

## 问题排查

### Q: 下载速度很慢？
A: 使用流式下载 (`--method streaming`)，这是默认选项

### Q: 重复的 instance_id？
A: 正常现象，数据集中包含多次运行，文件会被覆盖

### Q: 如何下载更多轨迹？
A:
1. 搜索其他数据集: `python search_more_datasets.py`
2. 查看 SWE-bench 官方资源
3. 联系数据集作者

### Q: 格式不兼容？
A: 需要编写适配器，将 nebius 格式转换为标准 SWE-agent 格式

---

## 相关资源

- [SWE-bench 官网](https://www.swebench.com/)
- [SWE-agent 文档](https://swe-agent.com/)
- [HuggingFace Datasets](https://huggingface.co/docs/datasets/)
- [SWE-bench GitHub](https://github.com/princeton-nlp/SWE-bench)
- [SWE-bench Experiments](https://github.com/SWE-bench/experiments)
