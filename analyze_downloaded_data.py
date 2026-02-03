"""
分析下载的轨迹数据
=================
统计下载的轨迹数量、成功率、仓库分布等信息
"""

import json
from pathlib import Path
from collections import defaultdict, Counter
from typing import Dict, List
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DataAnalyzer:
    """轨迹数据分析器"""

    def __init__(self, trajectories_dir: Path):
        self.trajectories_dir = Path(trajectories_dir)
        self.trajectories: List[Dict] = []
        self.stats = defaultdict(int)
        self.repo_distribution = Counter()
        self.model_distribution = Counter()

    def load_all_trajectories(self):
        """加载所有轨迹文件"""
        logger.info(f"Loading trajectories from {self.trajectories_dir}...")

        traj_files = list(self.trajectories_dir.glob('*.json'))
        logger.info(f"Found {len(traj_files)} trajectory files")

        for traj_file in traj_files:
            try:
                with open(traj_file, 'r') as f:
                    traj_data = json.load(f)
                    self.trajectories.append(traj_data)
                    self._update_stats(traj_data)
            except Exception as e:
                logger.warning(f"Failed to load {traj_file}: {e}")
                self.stats['load_failed'] += 1

        logger.info(f"Successfully loaded {len(self.trajectories)} trajectories")

    def _update_stats(self, traj_data: Dict):
        """更新统计信息"""
        # 实例 ID
        instance_id = traj_data.get('instance_id', '')
        if instance_id:
            # 提取仓库名（通常是 repo__issue 格式）
            repo = instance_id.split('-')[0] if '-' in instance_id else instance_id
            self.repo_distribution[repo] += 1

        # 模型名称
        model_name = traj_data.get('model_name', 'unknown')
        self.model_distribution[model_name] += 1

        # 是否解决
        is_resolved = traj_data.get('target', traj_data.get('resolved', False))
        if is_resolved:
            self.stats['resolved'] += 1
        else:
            self.stats['unresolved'] += 1

        # 轨迹长度
        trajectory = traj_data.get('trajectory', [])
        self.stats['total_steps'] += len(trajectory)

        # 统计动作类型（简化版）
        for step in trajectory:
            if isinstance(step, dict):
                role = step.get('role', '')
                if role == 'ai':
                    self.stats['ai_actions'] += 1
                elif role == 'user':
                    self.stats['user_responses'] += 1

    def print_summary(self):
        """打印统计摘要"""
        print("\n" + "=" * 70)
        print("Trajectory Data Analysis Summary")
        print("=" * 70)

        print(f"\n📊 Overall Statistics:")
        print(f"  Total trajectories:  {len(self.trajectories)}")
        print(f"  Resolved (success):  {self.stats['resolved']}")
        print(f"  Unresolved (failed): {self.stats['unresolved']}")

        if len(self.trajectories) > 0:
            success_rate = self.stats['resolved'] / len(self.trajectories) * 100
            print(f"  Success rate:        {success_rate:.2f}%")

            avg_steps = self.stats['total_steps'] / len(self.trajectories)
            print(f"  Avg steps per traj:  {avg_steps:.1f}")

        print(f"\n🤖 Model Distribution:")
        for model, count in self.model_distribution.most_common(10):
            print(f"  {model:40s} {count:4d} trajectories")

        print(f"\n📦 Top 20 Repositories:")
        for repo, count in self.repo_distribution.most_common(20):
            print(f"  {repo:40s} {count:4d} instances")

        print(f"\n💬 Interaction Statistics:")
        print(f"  AI actions:          {self.stats['ai_actions']}")
        print(f"  User responses:      {self.stats['user_responses']}")

        print("=" * 70)

    def filter_failed_trajectories(self, output_file: Path = None):
        """筛选失败的轨迹"""
        failed = [t for t in self.trajectories if not t.get('target', t.get('resolved', False))]

        logger.info(f"Found {len(failed)} failed trajectories")

        if output_file:
            with open(output_file, 'w') as f:
                json.dump({
                    'count': len(failed),
                    'instances': [t.get('instance_id') for t in failed]
                }, f, indent=2)
            logger.info(f"Saved failed trajectory list to {output_file}")

        return failed

    def get_repo_success_rates(self) -> Dict[str, float]:
        """计算每个仓库的成功率"""
        repo_stats = defaultdict(lambda: {'total': 0, 'resolved': 0})

        for traj in self.trajectories:
            instance_id = traj.get('instance_id', '')
            if instance_id:
                repo = instance_id.split('-')[0] if '-' in instance_id else instance_id
                repo_stats[repo]['total'] += 1
                if traj.get('target', traj.get('resolved', False)):
                    repo_stats[repo]['resolved'] += 1

        # 计算成功率
        success_rates = {}
        for repo, stats in repo_stats.items():
            if stats['total'] > 0:
                success_rates[repo] = stats['resolved'] / stats['total']

        return success_rates

    def save_analysis_report(self, output_file: Path):
        """保存分析报告"""
        report = {
            'total_trajectories': len(self.trajectories),
            'resolved': self.stats['resolved'],
            'unresolved': self.stats['unresolved'],
            'success_rate': self.stats['resolved'] / len(self.trajectories) if self.trajectories else 0,
            'avg_steps': self.stats['total_steps'] / len(self.trajectories) if self.trajectories else 0,
            'model_distribution': dict(self.model_distribution),
            'repo_distribution': dict(self.repo_distribution.most_common(50)),
            'repo_success_rates': self.get_repo_success_rates(),
        }

        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2)

        logger.info(f"Saved analysis report to {output_file}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Analyze downloaded trajectories')
    parser.add_argument('--input', '-i', type=str, default='swe_trajectories/trajectories',
                        help='Input trajectories directory')
    parser.add_argument('--output', '-o', type=str, default='trajectory_analysis.json',
                        help='Output analysis report file')
    parser.add_argument('--failed-list', type=str, default='failed_trajectories.json',
                        help='Output file for failed trajectory list')

    args = parser.parse_args()

    # 创建分析器
    analyzer = DataAnalyzer(Path(args.input))

    # 加载并分析
    analyzer.load_all_trajectories()
    analyzer.print_summary()

    # 保存报告
    analyzer.save_analysis_report(Path(args.output))

    # 筛选失败案例
    analyzer.filter_failed_trajectories(Path(args.failed_list))

    print(f"\n✅ Analysis complete!")
    print(f"   Report saved to: {args.output}")
    print(f"   Failed list saved to: {args.failed_list}")


if __name__ == '__main__':
    main()
