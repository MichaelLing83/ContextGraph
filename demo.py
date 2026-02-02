"""
示例运行脚本
============
展示如何使用轨迹分析器
"""

import json
from pathlib import Path
from datetime import datetime

# 示例轨迹数据 (模拟 SWE-agent 输出格式)
SAMPLE_TRAJECTORY = {
    "environment": "marshmallow-code__marshmallow",
    "trajectory": [
        {
            "response": "Let me first understand the issue. The problem is about integer division in TimeDelta serialization. I should look at the fields.py file.",
            "thought": "Let me first understand the issue. The problem is about integer division in TimeDelta serialization.",
            "action": "find . -name 'fields.py' | head -5\n",
            "observation": "./src/marshmallow/fields.py\n./tests/test_fields.py",
            "state": "{\"open_file\": \"n/a\", \"working_dir\": \"/marshmallow-code__marshmallow\"}"
        },
        {
            "response": "Found the file. Let me open it and look at the TimeDelta class around line 1474.",
            "thought": "Found the file. Let me open it and look at the TimeDelta class around line 1474.",
            "action": "open src/marshmallow/fields.py 1470\n",
            "observation": "[File: /marshmallow-code__marshmallow/src/marshmallow/fields.py (1997 lines total)]\n1471:    def _serialize(self, value, attr, obj, **kwargs):\n1472:        if value is None:\n1473:            return None\n1474:        base_unit = dt.timedelta(**{self.precision: 1})\n1475:        return int(value.total_seconds() / base_unit.total_seconds())",
            "state": "{\"open_file\": \"/marshmallow-code__marshmallow/src/marshmallow/fields.py\", \"working_dir\": \"/marshmallow-code__marshmallow\"}"
        },
        {
            "response": "I see the issue. The division returns a float but we're converting to int which loses precision. Let me create a test to reproduce the issue.",
            "thought": "I see the issue. The division returns a float but we're converting to int which loses precision.",
            "action": "create reproduce.py\nfrom marshmallow.fields import TimeDelta\nfrom datetime import timedelta\n\ntd = TimeDelta(precision='milliseconds')\nprint(td.serialize('x', {'x': timedelta(milliseconds=345)}))\nend_of_edit\n",
            "observation": "[File: /marshmallow-code__marshmallow/reproduce.py (5 lines total)]\n1:from marshmallow.fields import TimeDelta\n2:from datetime import timedelta\n3:\n4:td = TimeDelta(precision='milliseconds')\n5:print(td.serialize('x', {'x': timedelta(milliseconds=345)}))",
            "state": "{\"open_file\": \"/marshmallow-code__marshmallow/reproduce.py\", \"working_dir\": \"/marshmallow-code__marshmallow\"}"
        },
        {
            "response": "Let me run the reproduction script.",
            "thought": "Let me run the reproduction script.",
            "action": "python reproduce.py\n",
            "observation": "344\n",
            "state": "{\"open_file\": \"/marshmallow-code__marshmallow/reproduce.py\", \"working_dir\": \"/marshmallow-code__marshmallow\"}"
        },
        {
            "response": "Confirmed! It returns 344 instead of 345. The issue is that int() truncates instead of rounding. Let me fix this by using round() instead.",
            "thought": "Confirmed! It returns 344 instead of 345. The issue is that int() truncates instead of rounding.",
            "action": "edit 1474:1475\n        base_unit = dt.timedelta(**{self.precision: 1})\n        return round(value.total_seconds() / base_unit.total_seconds())\nend_of_edit\n",
            "observation": "[File: /marshmallow-code__marshmallow/src/marshmallow/fields.py (1997 lines total)]\n(1471 more lines above)\n1472:        if value is None:\n1473:            return None\n1474:        base_unit = dt.timedelta(**{self.precision: 1})\n1475:        return round(value.total_seconds() / base_unit.total_seconds())",
            "state": "{\"open_file\": \"/marshmallow-code__marshmallow/src/marshmallow/fields.py\", \"working_dir\": \"/marshmallow-code__marshmallow\"}"
        },
        {
            "response": "Let me verify the fix.",
            "thought": "Let me verify the fix.",
            "action": "python reproduce.py\n",
            "observation": "345\n",
            "state": "{\"open_file\": \"/marshmallow-code__marshmallow/reproduce.py\", \"working_dir\": \"/marshmallow-code__marshmallow\"}"
        },
        {
            "response": "The fix works. Now let me run the tests to make sure I didn't break anything.",
            "thought": "The fix works. Now let me run the tests to make sure I didn't break anything.",
            "action": "pytest tests/test_fields.py -v -k timedelta\n",
            "observation": "============================= test session starts ==============================\ncollected 15 items / 12 deselected / 3 selected\n\ntests/test_fields.py::TestTimeDelta::test_timedelta_field PASSED\ntests/test_fields.py::TestTimeDelta::test_serialize PASSED\ntests/test_fields.py::TestTimeDelta::test_deserialize PASSED\n\n============================== 3 passed ==============================",
            "state": "{\"open_file\": \"/marshmallow-code__marshmallow/reproduce.py\", \"working_dir\": \"/marshmallow-code__marshmallow\"}"
        },
        {
            "response": "All tests pass. Let me submit the fix.",
            "thought": "All tests pass. Let me submit the fix.",
            "action": "submit\n",
            "observation": "Submission successful.",
            "state": "{\"open_file\": \"/marshmallow-code__marshmallow/reproduce.py\", \"working_dir\": \"/marshmallow-code__marshmallow\"}"
        }
    ],
    "info": {
        "exit_status": "submitted",
        "submission": "diff --git a/src/marshmallow/fields.py b/src/marshmallow/fields.py\n--- a/src/marshmallow/fields.py\n+++ b/src/marshmallow/fields.py\n@@ -1472,7 +1472,7 @@ class TimeDelta(Field):\n         if value is None:\n             return None\n         base_unit = dt.timedelta(**{self.precision: 1})\n-        return int(value.total_seconds() / base_unit.total_seconds())\n+        return round(value.total_seconds() / base_unit.total_seconds())"
    }
}


def run_analysis_demo():
    """运行分析演示"""
    from analyzer import TrajectoryParser, TrajectoryAnalyzer, GraphExporter
    from context_graph import ContextGraph, GraphBuilder, SemanticNode, Edge
    
    print("=" * 60)
    print("SWE-bench Trajectory Analyzer Demo")
    print("=" * 60)
    
    # 1. 保存示例轨迹
    output_dir = Path('demo_output')
    output_dir.mkdir(exist_ok=True)
    
    sample_traj_path = output_dir / 'sample.traj'
    with open(sample_traj_path, 'w') as f:
        json.dump(SAMPLE_TRAJECTORY, f, indent=2)
    
    print(f"\n1. Saved sample trajectory to {sample_traj_path}")
    
    # 2. 解析轨迹
    parser = TrajectoryParser()
    trajectory = parser.parse_swe_agent_trajectory(sample_traj_path)
    trajectory.is_resolved = True  # 假设这个案例成功了
    trajectory.final_patch = SAMPLE_TRAJECTORY['info']['submission']
    
    print(f"\n2. Parsed trajectory:")
    print(f"   Instance ID: {trajectory.instance_id}")
    print(f"   Total steps: {trajectory.total_steps}")
    print(f"   Action distribution: {trajectory.action_distribution}")
    print(f"   Entities extracted: {len(trajectory.entities)}")
    print(f"   Relations extracted: {len(trajectory.relations)}")
    
    # 3. 打印提取的实体
    print(f"\n3. Extracted entities:")
    for entity in trajectory.entities[:10]:
        print(f"   [{entity.entity_type}] {entity.name}")
    
    # 4. 打印提取的关系
    print(f"\n4. Extracted relations:")
    for relation in trajectory.relations[:5]:
        print(f"   {relation.source_id} --[{relation.relation_type}]--> {relation.target_id}")
    
    # 5. 构建 Context Graph
    print(f"\n5. Building Context Graph...")
    builder = GraphBuilder()
    graph = builder.build_from_trajectory(trajectory)
    
    stats = graph.stats()
    print(f"   Graph statistics:")
    for k, v in stats.items():
        print(f"   - {k}: {v}")
    
    # 6. 导出
    print(f"\n6. Exporting...")
    exporter = GraphExporter()
    
    # JSON 格式
    exporter.export_to_json([trajectory], output_dir / 'graph.json')
    print(f"   Exported to {output_dir / 'graph.json'}")
    
    # A-MEM 格式
    exporter.export_for_amem([trajectory], output_dir / 'notes.json')
    print(f"   Exported to {output_dir / 'notes.json'}")
    
    # 保存图
    graph.save(output_dir / 'context_graph.json')
    print(f"   Saved context graph to {output_dir / 'context_graph.json'}")
    
    # 7. 统计分析
    print(f"\n7. Running statistical analysis...")
    analyzer = TrajectoryAnalyzer()
    analyzer.add_trajectory(trajectory)
    
    stats = analyzer.compute_statistics()
    print(f"   Overall statistics:")
    print(f"   - Total trajectories: {stats.get('total_trajectories', 0)}")
    print(f"   - Resolution rate: {stats.get('resolution_rate', 0):.2%}")
    
    patterns = analyzer.extract_success_patterns()
    print(f"   Success patterns: {len(patterns)}")
    for p in patterns:
        print(f"   - {p['type']}: {p.get('pattern', p.get('entities', [])[:5])}")
    
    print("\n" + "=" * 60)
    print("Demo complete! Check demo_output/ for results.")
    print("=" * 60)


def run_huggingface_demo():
    """从 HuggingFace 加载数据的演示"""
    print("\n" + "=" * 60)
    print("Loading data from HuggingFace...")
    print("=" * 60)
    
    try:
        from data_loader import load_swebench_lite
        
        instances = load_swebench_lite()
        print(f"\nLoaded {len(instances)} instances from SWE-bench Lite")
        
        # 打印一些统计
        repos = set(inst.repo for inst in instances)
        print(f"Unique repositories: {len(repos)}")
        
        # 打印示例
        if instances:
            inst = instances[0]
            print(f"\nExample instance:")
            print(f"  ID: {inst.instance_id}")
            print(f"  Repo: {inst.repo}")
            print(f"  Problem statement (first 200 chars):")
            print(f"  {inst.problem_statement[:200]}...")
            
    except ImportError as e:
        print(f"Error: {e}")
        print("Please install datasets: pip install datasets")


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == '--huggingface':
        run_huggingface_demo()
    else:
        run_analysis_demo()
