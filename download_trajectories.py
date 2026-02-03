"""
批量下载 SWE-agent 轨迹数据
================================
从多个数据源下载轨迹数据：
1. HuggingFace: nebius/SWE-agent-trajectories
2. HuggingFace: 其他可用的轨迹数据集
3. 本地保存以便后续分析
"""

import json
from pathlib import Path
from typing import Iterator, Dict
import logging
from datetime import datetime
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class TrajectoryDownloader:
    """轨迹数据下载器"""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 创建子目录
        self.trajectories_dir = self.output_dir / 'trajectories'
        self.trajectories_dir.mkdir(exist_ok=True)

        self.metadata_dir = self.output_dir / 'metadata'
        self.metadata_dir.mkdir(exist_ok=True)

        self.stats = {
            'total_downloaded': 0,
            'successful': 0,
            'failed': 0,
            'resolved': 0,
            'unresolved': 0,
        }

    def download_from_huggingface_streaming(self, dataset_name: str, max_trajectories: int = None):
        """
        从 HuggingFace 流式下载轨迹（推荐用于大数据集）

        Args:
            dataset_name: 数据集名称，如 'nebius/SWE-agent-trajectories'
            max_trajectories: 最多下载数量，None 表示全部
        """
        try:
            from datasets import load_dataset
        except ImportError:
            logger.error("Please install datasets: pip install datasets")
            return

        logger.info(f"Starting streaming download from {dataset_name}...")

        try:
            # 流式加载
            ds = load_dataset(dataset_name, split='train', streaming=True)

            count = 0
            for item in tqdm(ds, desc=f"Downloading from {dataset_name}"):
                if max_trajectories and count >= max_trajectories:
                    break

                try:
                    self._save_trajectory(item, source=dataset_name)
                    count += 1
                    self.stats['total_downloaded'] += 1

                    if count % 100 == 0:
                        logger.info(f"Downloaded {count} trajectories so far...")
                        self._save_stats()

                except Exception as e:
                    logger.warning(f"Failed to save trajectory {count}: {e}")
                    self.stats['failed'] += 1

            logger.info(f"Completed download: {count} trajectories from {dataset_name}")

        except Exception as e:
            logger.error(f"Error downloading from {dataset_name}: {e}")

    def download_from_huggingface_full(self, dataset_name: str, split: str = 'train'):
        """
        从 HuggingFace 完整下载（用于小数据集）

        Args:
            dataset_name: 数据集名称
            split: 数据集分割（train/test/validation）
        """
        try:
            from datasets import load_dataset
        except ImportError:
            logger.error("Please install datasets: pip install datasets")
            return

        logger.info(f"Downloading full dataset {dataset_name} ({split} split)...")

        try:
            ds = load_dataset(dataset_name, split=split)

            logger.info(f"Loaded {len(ds)} items, saving to disk...")

            for idx, item in enumerate(tqdm(ds, desc="Saving trajectories")):
                try:
                    self._save_trajectory(item, source=dataset_name)
                    self.stats['total_downloaded'] += 1

                except Exception as e:
                    logger.warning(f"Failed to save item {idx}: {e}")
                    self.stats['failed'] += 1

            logger.info(f"Completed: saved {self.stats['total_downloaded']} trajectories")

        except Exception as e:
            logger.error(f"Error downloading {dataset_name}: {e}")

    def _save_trajectory(self, item: Dict, source: str):
        """保存单个轨迹"""
        # 提取 instance_id
        instance_id = item.get('instance_id', item.get('id', f'unknown_{self.stats["total_downloaded"]}'))

        # 清理 instance_id 作为文件名
        safe_id = instance_id.replace('/', '_').replace('\\', '_')

        # 保存轨迹
        traj_file = self.trajectories_dir / f"{safe_id}.json"
        with open(traj_file, 'w', encoding='utf-8') as f:
            json.dump(item, f, indent=2, ensure_ascii=False)

        # 更新统计
        self.stats['successful'] += 1

        # 检查是否已解决
        is_resolved = item.get('is_resolved', item.get('resolved', False))
        if is_resolved:
            self.stats['resolved'] += 1
        else:
            self.stats['unresolved'] += 1

        return traj_file

    def _save_stats(self):
        """保存统计信息"""
        stats_file = self.metadata_dir / 'download_stats.json'
        with open(stats_file, 'w') as f:
            json.dump({
                'stats': self.stats,
                'timestamp': datetime.now().isoformat(),
            }, f, indent=2)

    def download_all_sources(self, max_per_source: int = None):
        """
        从所有已知数据源下载

        Args:
            max_per_source: 每个数据源最多下载数量
        """
        sources = [
            ('nebius/SWE-agent-trajectories', 'streaming'),
            # 可以添加更多数据源
        ]

        for dataset_name, method in sources:
            logger.info(f"\n{'='*60}")
            logger.info(f"Downloading from: {dataset_name}")
            logger.info(f"{'='*60}\n")

            try:
                if method == 'streaming':
                    self.download_from_huggingface_streaming(dataset_name, max_per_source)
                else:
                    self.download_from_huggingface_full(dataset_name)
            except Exception as e:
                logger.error(f"Failed to download from {dataset_name}: {e}")
                continue

        # 保存最终统计
        self._save_stats()
        self._print_summary()

    def _print_summary(self):
        """打印下载摘要"""
        print("\n" + "="*60)
        print("Download Summary")
        print("="*60)
        print(f"Total downloaded:    {self.stats['total_downloaded']}")
        print(f"Successfully saved:  {self.stats['successful']}")
        print(f"Failed:              {self.stats['failed']}")
        print(f"Resolved cases:      {self.stats['resolved']}")
        print(f"Unresolved cases:    {self.stats['unresolved']}")
        print(f"\nData saved to: {self.output_dir}")
        print(f"Trajectories: {self.trajectories_dir}")
        print(f"Metadata: {self.metadata_dir}")
        print("="*60)


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Download SWE-agent trajectories')
    parser.add_argument('--output', '-o', type=str, default='swe_trajectories',
                        help='Output directory')
    parser.add_argument('--max', '-m', type=int, default=None,
                        help='Maximum trajectories per source (default: all)')
    parser.add_argument('--dataset', '-d', type=str, default=None,
                        help='Specific dataset to download (default: all)')
    parser.add_argument('--method', type=str, default='streaming',
                        choices=['streaming', 'full'],
                        help='Download method: streaming (memory efficient) or full')

    args = parser.parse_args()

    # 创建下载器
    downloader = TrajectoryDownloader(Path(args.output))

    if args.dataset:
        # 下载特定数据集
        logger.info(f"Downloading specific dataset: {args.dataset}")
        if args.method == 'streaming':
            downloader.download_from_huggingface_streaming(args.dataset, args.max)
        else:
            downloader.download_from_huggingface_full(args.dataset)
    else:
        # 下载所有数据源
        logger.info("Downloading from all sources...")
        downloader.download_all_sources(args.max)

    # 保存最终统计
    downloader._save_stats()
    downloader._print_summary()


if __name__ == '__main__':
    main()
