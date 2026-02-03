"""
失败路径分析器
==============
深入分析 SWE-agent 的失败案例，提取失败模式和原因
"""

import json
import re
from pathlib import Path
from collections import defaultdict, Counter
from typing import List, Dict, Tuple, Set
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FailureAnalyzer:
    """失败路径分析器"""

    # 失败原因分类
    FAILURE_CATEGORIES = {
        'syntax_error': ['SyntaxError', 'IndentationError', 'invalid syntax'],
        'import_error': ['ImportError', 'ModuleNotFoundError', 'No module named'],
        'name_error': ['NameError', 'not defined'],
        'type_error': ['TypeError', 'type object'],
        'attribute_error': ['AttributeError', 'has no attribute'],
        'test_failure': ['FAILED', 'AssertionError', 'test failed', 'pytest'],
        'file_not_found': ['FileNotFoundError', 'No such file', 'cannot find'],
        'timeout': ['timeout', 'timed out', 'exceeded'],
        'permission_error': ['PermissionError', 'Permission denied'],
        'connection_error': ['ConnectionError', 'Network', 'connection'],
        'runtime_error': ['RuntimeError', 'Error:'],
        'value_error': ['ValueError', 'invalid value'],
        'key_error': ['KeyError', 'key not found'],
        'index_error': ['IndexError', 'out of range'],
    }

    # 失败阶段
    FAILURE_STAGES = {
        'early': (0, 10),      # 前 10 步
        'middle': (10, 30),    # 10-30 步
        'late': (30, 100),     # 30+ 步
    }

    def __init__(self, trajectories_dir: Path):
        self.trajectories_dir = Path(trajectories_dir)
        self.trajectories: List[Dict] = []
        self.failed_trajs: List[Dict] = []
        self.success_trajs: List[Dict] = []

    def load_trajectories(self, max_load: int = None):
        """加载轨迹数据"""
        logger.info(f"Loading trajectories from {self.trajectories_dir}...")

        traj_files = list(self.trajectories_dir.glob('*.json'))
        if max_load:
            traj_files = traj_files[:max_load]

        logger.info(f"Found {len(traj_files)} trajectory files")

        for traj_file in traj_files:
            try:
                with open(traj_file, 'r') as f:
                    traj_data = json.load(f)
                    self.trajectories.append(traj_data)

                    # 分类
                    is_resolved = traj_data.get('target', False)
                    if is_resolved:
                        self.success_trajs.append(traj_data)
                    else:
                        self.failed_trajs.append(traj_data)

            except Exception as e:
                logger.warning(f"Failed to load {traj_file}: {e}")

        logger.info(f"Loaded {len(self.trajectories)} trajectories")
        logger.info(f"  Success: {len(self.success_trajs)}")
        logger.info(f"  Failed: {len(self.failed_trajs)}")

    def analyze_failure_reasons(self) -> Dict:
        """分析失败原因"""
        logger.info("Analyzing failure reasons...")

        failure_reasons = defaultdict(list)
        failure_counts = Counter()

        for traj in self.failed_trajs:
            instance_id = traj.get('instance_id', 'unknown')
            trajectory = traj.get('trajectory', [])

            # 提取所有错误消息
            errors = []
            for step in trajectory:
                text = step.get('text', '')
                if step.get('role') == 'user':  # 用户消息通常包含观察结果
                    # 检查每种失败类型
                    for category, patterns in self.FAILURE_CATEGORIES.items():
                        if any(pattern.lower() in text.lower() for pattern in patterns):
                            errors.append(category)
                            failure_counts[category] += 1

            # 如果没有明确的错误，标记为 'unknown'
            if not errors:
                errors.append('unknown')
                failure_counts['unknown'] += 1

            failure_reasons[instance_id] = list(set(errors))

        return {
            'failure_reasons': dict(failure_reasons),
            'failure_counts': dict(failure_counts),
        }

    def analyze_failure_stages(self) -> Dict:
        """分析失败发生在哪个阶段"""
        logger.info("Analyzing failure stages...")

        stage_counts = Counter()
        first_error_steps = []

        for traj in self.failed_trajs:
            trajectory = traj.get('trajectory', [])
            total_steps = len([s for s in trajectory if s.get('role') == 'ai'])

            # 找到第一个错误出现的步骤
            first_error_step = None
            for i, step in enumerate(trajectory):
                if step.get('role') == 'user':
                    text = step.get('text', '')
                    # 检查是否包含错误
                    has_error = any(
                        pattern.lower() in text.lower()
                        for patterns in self.FAILURE_CATEGORIES.values()
                        for pattern in patterns
                    )
                    if has_error:
                        first_error_step = i
                        break

            if first_error_step is not None:
                first_error_steps.append(first_error_step)

                # 确定阶段
                if first_error_step < 10:
                    stage_counts['early'] += 1
                elif first_error_step < 30:
                    stage_counts['middle'] += 1
                else:
                    stage_counts['late'] += 1
            else:
                stage_counts['no_error_detected'] += 1

        return {
            'stage_counts': dict(stage_counts),
            'avg_first_error_step': sum(first_error_steps) / len(first_error_steps) if first_error_steps else 0,
            'first_error_steps': first_error_steps,
        }

    def extract_action_sequences(self, trajectories: List[Dict], length: int = 3) -> Counter:
        """提取动作序列"""
        logger.info(f"Extracting action sequences of length {length}...")

        sequences = Counter()

        for traj in trajectories:
            trajectory = traj.get('trajectory', [])

            # 提取 AI 动作序列
            actions = []
            for step in trajectory:
                if step.get('role') == 'ai':
                    text = step.get('text', '')
                    # 简单分类动作类型
                    action_type = self._classify_action(text)
                    actions.append(action_type)

            # 提取 n-gram 序列
            for i in range(len(actions) - length + 1):
                seq = tuple(actions[i:i+length])
                sequences[seq] += 1

        return sequences

    def _classify_action(self, text: str) -> str:
        """分类动作类型"""
        text_lower = text.lower()

        if 'search' in text_lower or 'find' in text_lower or 'grep' in text_lower:
            return 'search'
        elif 'open' in text_lower or 'read' in text_lower or 'cat' in text_lower:
            return 'read'
        elif 'edit' in text_lower or 'modify' in text_lower or 'change' in text_lower:
            return 'edit'
        elif 'create' in text_lower or 'write' in text_lower:
            return 'create'
        elif 'test' in text_lower or 'pytest' in text_lower:
            return 'test'
        elif 'submit' in text_lower:
            return 'submit'
        elif 'ls' in text_lower or 'pwd' in text_lower or 'cd' in text_lower:
            return 'navigate'
        elif 'python' in text_lower or 'run' in text_lower or 'execute' in text_lower:
            return 'execute'
        else:
            return 'other'

    def compare_success_vs_failure(self) -> Dict:
        """对比成功和失败案例"""
        logger.info("Comparing success vs failure patterns...")

        # 提取动作序列
        success_sequences = self.extract_action_sequences(self.success_trajs, length=3)
        failure_sequences = self.extract_action_sequences(self.failed_trajs, length=3)

        # 计算步骤数
        success_steps = [len([s for s in t.get('trajectory', []) if s.get('role') == 'ai'])
                         for t in self.success_trajs]
        failure_steps = [len([s for s in t.get('trajectory', []) if s.get('role') == 'ai'])
                         for t in self.failed_trajs]

        # 动作类型分布
        success_actions = Counter()
        failure_actions = Counter()

        for traj in self.success_trajs:
            for step in traj.get('trajectory', []):
                if step.get('role') == 'ai':
                    action = self._classify_action(step.get('text', ''))
                    success_actions[action] += 1

        for traj in self.failed_trajs:
            for step in traj.get('trajectory', []):
                if step.get('role') == 'ai':
                    action = self._classify_action(step.get('text', ''))
                    failure_actions[action] += 1

        return {
            'step_counts': {
                'success_avg': sum(success_steps) / len(success_steps) if success_steps else 0,
                'success_min': min(success_steps) if success_steps else 0,
                'success_max': max(success_steps) if success_steps else 0,
                'failure_avg': sum(failure_steps) / len(failure_steps) if failure_steps else 0,
                'failure_min': min(failure_steps) if failure_steps else 0,
                'failure_max': max(failure_steps) if failure_steps else 0,
            },
            'action_distribution': {
                'success': dict(success_actions),
                'failure': dict(failure_actions),
            },
            'top_success_sequences': success_sequences.most_common(20),
            'top_failure_sequences': failure_sequences.most_common(20),
            'unique_to_success': self._find_unique_sequences(success_sequences, failure_sequences),
            'unique_to_failure': self._find_unique_sequences(failure_sequences, success_sequences),
        }

    def _find_unique_sequences(self, sequences_a: Counter, sequences_b: Counter, top_n: int = 10) -> List:
        """找出只在 A 中出现但不在 B 中的序列"""
        unique = []
        for seq, count in sequences_a.most_common(50):
            if seq not in sequences_b or sequences_b[seq] < count / 5:
                unique.append((seq, count))
                if len(unique) >= top_n:
                    break
        return unique

    def analyze_loop_patterns(self) -> Dict:
        """分析循环模式（重复动作）"""
        logger.info("Analyzing loop patterns...")

        loop_counts = {'success': 0, 'failure': 0}
        loop_examples = {'success': [], 'failure': []}

        def has_loop(trajectory, min_repeat=3):
            """检测是否有重复的动作序列"""
            actions = []
            for step in trajectory:
                if step.get('role') == 'ai':
                    action = self._classify_action(step.get('text', ''))
                    actions.append(action)

            # 检测连续重复
            for i in range(len(actions) - min_repeat):
                if len(set(actions[i:i+min_repeat])) == 1:
                    return True, actions[i:i+min_repeat]
            return False, None

        for traj in self.success_trajs[:100]:  # 只分析前 100 个
            has_loop_flag, loop = has_loop(traj.get('trajectory', []))
            if has_loop_flag:
                loop_counts['success'] += 1
                loop_examples['success'].append(loop)

        for traj in self.failed_trajs[:100]:
            has_loop_flag, loop = has_loop(traj.get('trajectory', []))
            if has_loop_flag:
                loop_counts['failure'] += 1
                loop_examples['failure'].append(loop)

        return {
            'loop_counts': loop_counts,
            'loop_rate': {
                'success': loop_counts['success'] / min(100, len(self.success_trajs)) if self.success_trajs else 0,
                'failure': loop_counts['failure'] / min(100, len(self.failed_trajs)) if self.failed_trajs else 0,
            },
            'loop_examples': {
                'success': loop_examples['success'][:5],
                'failure': loop_examples['failure'][:5],
            }
        }

    def generate_report(self, output_file: Path):
        """生成完整分析报告"""
        logger.info("Generating comprehensive failure analysis report...")

        report = {
            'summary': {
                'total_trajectories': len(self.trajectories),
                'success_count': len(self.success_trajs),
                'failure_count': len(self.failed_trajs),
                'success_rate': len(self.success_trajs) / len(self.trajectories) if self.trajectories else 0,
            },
            'failure_reasons': self.analyze_failure_reasons(),
            'failure_stages': self.analyze_failure_stages(),
            'success_vs_failure': self.compare_success_vs_failure(),
            'loop_patterns': self.analyze_loop_patterns(),
        }

        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2)

        logger.info(f"Report saved to {output_file}")
        return report

    def print_summary(self, report: Dict):
        """打印报告摘要"""
        print("\n" + "="*80)
        print("失败路径分析报告")
        print("="*80)

        # 基本统计
        summary = report['summary']
        print(f"\n📊 基本统计:")
        print(f"  总轨迹数: {summary['total_trajectories']}")
        print(f"  成功案例: {summary['success_count']} ({summary['success_rate']:.2%})")
        print(f"  失败案例: {summary['failure_count']} ({1-summary['success_rate']:.2%})")

        # 失败原因
        print(f"\n❌ 失败原因分析:")
        failure_counts = report['failure_reasons']['failure_counts']
        for reason, count in sorted(failure_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"  {reason:20s} {count:4d} 次")

        # 失败阶段
        print(f"\n⏱️  失败阶段分析:")
        stages = report['failure_stages']['stage_counts']
        for stage, count in sorted(stages.items(), key=lambda x: -x[1]):
            print(f"  {stage:20s} {count:4d} 次")
        avg_first_error = report['failure_stages']['avg_first_error_step']
        print(f"  平均首次错误步骤: {avg_first_error:.1f}")

        # 步骤数对比
        print(f"\n📏 步骤数对比:")
        steps = report['success_vs_failure']['step_counts']
        print(f"  成功案例平均: {steps['success_avg']:.1f} 步 (范围: {steps['success_min']}-{steps['success_max']})")
        print(f"  失败案例平均: {steps['failure_avg']:.1f} 步 (范围: {steps['failure_min']}-{steps['failure_max']})")

        # 动作分布对比
        print(f"\n🎯 动作类型分布:")
        success_actions = report['success_vs_failure']['action_distribution']['success']
        failure_actions = report['success_vs_failure']['action_distribution']['failure']

        success_total = sum(success_actions.values())
        failure_total = sum(failure_actions.values())

        print(f"  {'动作类型':<15} {'成功案例':<15} {'失败案例':<15}")
        all_actions = set(success_actions.keys()) | set(failure_actions.keys())
        for action in sorted(all_actions):
            s_count = success_actions.get(action, 0)
            f_count = failure_actions.get(action, 0)
            s_pct = s_count / success_total * 100 if success_total else 0
            f_pct = f_count / failure_total * 100 if failure_total else 0
            print(f"  {action:<15} {s_pct:>6.2f}% ({s_count:>5}) {f_pct:>6.2f}% ({f_count:>5})")

        # 循环模式
        print(f"\n🔄 循环模式检测:")
        loops = report['loop_patterns']['loop_rate']
        print(f"  成功案例循环率: {loops['success']:.2%}")
        print(f"  失败案例循环率: {loops['failure']:.2%}")

        # Top 成功序列
        print(f"\n✅ Top 10 成功动作序列:")
        for i, (seq, count) in enumerate(report['success_vs_failure']['top_success_sequences'][:10], 1):
            print(f"  {i:2d}. {' → '.join(seq):40s} ({count} 次)")

        # Top 失败序列
        print(f"\n❌ Top 10 失败动作序列:")
        for i, (seq, count) in enumerate(report['success_vs_failure']['top_failure_sequences'][:10], 1):
            print(f"  {i:2d}. {' → '.join(seq):40s} ({count} 次)")

        print("\n" + "="*80)


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Analyze SWE-agent failure patterns')
    parser.add_argument('--input', '-i', type=str, default='swe_trajectories/trajectories',
                        help='Input trajectories directory')
    parser.add_argument('--output', '-o', type=str, default='failure_analysis_report.json',
                        help='Output report file')
    parser.add_argument('--max-load', '-m', type=int, default=None,
                        help='Maximum trajectories to load (for testing)')

    args = parser.parse_args()

    # 创建分析器
    analyzer = FailureAnalyzer(Path(args.input))

    # 加载数据
    analyzer.load_trajectories(max_load=args.max_load)

    # 生成报告
    report = analyzer.generate_report(Path(args.output))

    # 打印摘要
    analyzer.print_summary(report)

    print(f"\n✅ 完整报告已保存到: {args.output}")


if __name__ == '__main__':
    main()
