"""
数据加载模块
===========
支持从多种来源加载 SWE-bench 数据和轨迹
"""

import json
from pathlib import Path
from typing import List, Dict, Optional, Iterator
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class SWEBenchInstance:
    """SWE-bench 单个实例"""
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str
    patch: str  # gold patch
    test_patch: str
    version: str
    created_at: str
    
    # 可选字段
    issue_url: Optional[str] = None
    pr_url: Optional[str] = None


class HuggingFaceLoader:
    """从 HuggingFace 加载 SWE-bench 数据集"""
    
    DATASETS = {
        'swe-bench': 'princeton-nlp/SWE-bench',
        'swe-bench-lite': 'SWE-bench/SWE-bench_Lite',
        'swe-bench-verified': 'SWE-bench/SWE-bench_Verified',
        'swe-smith': 'SWE-bench/SWE-smith',
    }
    
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or Path.home() / '.cache' / 'swebench_analyzer'
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def load_dataset(self, dataset_name: str = 'swe-bench-lite', split: str = 'test') -> List[SWEBenchInstance]:
        """加载数据集"""
        try:
            from datasets import load_dataset
        except ImportError:
            logger.error("Please install datasets: pip install datasets")
            raise
        
        dataset_id = self.DATASETS.get(dataset_name, dataset_name)
        logger.info(f"Loading {dataset_id} ({split} split)...")
        
        ds = load_dataset(dataset_id, split=split)
        
        instances = []
        for item in ds:
            instance = SWEBenchInstance(
                instance_id=item.get('instance_id', ''),
                repo=item.get('repo', ''),
                base_commit=item.get('base_commit', ''),
                problem_statement=item.get('problem_statement', ''),
                hints_text=item.get('hints_text', ''),
                patch=item.get('patch', ''),
                test_patch=item.get('test_patch', ''),
                version=item.get('version', ''),
                created_at=item.get('created_at', ''),
                issue_url=item.get('issue_url'),
                pr_url=item.get('pr_url'),
            )
            instances.append(instance)
        
        logger.info(f"Loaded {len(instances)} instances")
        return instances
    
    def load_trajectories(self, dataset_name: str = 'nebius/SWE-agent-trajectories') -> Iterator[Dict]:
        """加载轨迹数据集"""
        try:
            from datasets import load_dataset
        except ImportError:
            logger.error("Please install datasets: pip install datasets")
            raise
        
        logger.info(f"Loading trajectories from {dataset_name}...")
        ds = load_dataset(dataset_name, split='train', streaming=True)
        
        for item in ds:
            yield item


class LocalFileLoader:
    """从本地文件加载轨迹"""
    
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
    
    def load_trajectory_file(self, file_path: Path) -> Dict:
        """加载单个轨迹文件"""
        with open(file_path, 'r') as f:
            return json.load(f)
    
    def iterate_trajectories(self, pattern: str = '*.traj') -> Iterator[Dict]:
        """遍历所有轨迹文件"""
        for traj_file in self.base_dir.rglob(pattern):
            try:
                yield self.load_trajectory_file(traj_file)
            except Exception as e:
                logger.warning(f"Failed to load {traj_file}: {e}")
    
    def load_predictions(self, predictions_path: Path) -> Dict[str, Dict]:
        """加载预测结果文件"""
        with open(predictions_path, 'r') as f:
            predictions = {}
            for line in f:
                pred = json.loads(line)
                predictions[pred['instance_id']] = pred
            return predictions
    
    def load_results(self, results_path: Path) -> Dict:
        """加载评估结果"""
        with open(results_path, 'r') as f:
            return json.load(f)


class S3Loader:
    """从 S3 加载 SWE-bench 实验数据"""
    
    BUCKET = 'swe-bench'
    
    def __init__(self):
        try:
            import boto3
            self.s3 = boto3.client('s3')
        except ImportError:
            logger.error("Please install boto3: pip install boto3")
            raise
    
    def list_experiments(self, prefix: str = 'evaluation/') -> List[str]:
        """列出所有实验"""
        response = self.s3.list_objects_v2(
            Bucket=self.BUCKET,
            Prefix=prefix,
            Delimiter='/'
        )
        
        experiments = []
        for prefix_obj in response.get('CommonPrefixes', []):
            experiments.append(prefix_obj['Prefix'])
        
        return experiments
    
    def download_trajectories(self, experiment_prefix: str, local_dir: Path):
        """下载实验轨迹"""
        local_dir.mkdir(parents=True, exist_ok=True)
        
        paginator = self.s3.get_paginator('list_objects_v2')
        
        for page in paginator.paginate(Bucket=self.BUCKET, Prefix=experiment_prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if key.endswith('.traj'):
                    local_path = local_dir / Path(key).name
                    self.s3.download_file(self.BUCKET, key, str(local_path))
                    logger.info(f"Downloaded {key}")


class ExperimentsLoader:
    """加载 SWE-bench/experiments 仓库的数据"""
    
    def __init__(self, experiments_dir: Path):
        self.experiments_dir = Path(experiments_dir)
    
    def list_submissions(self, split: str = 'lite') -> List[Path]:
        """列出所有提交"""
        eval_dir = self.experiments_dir / 'evaluation' / split
        if not eval_dir.exists():
            return []
        
        return [d for d in eval_dir.iterdir() if d.is_dir()]
    
    def load_submission(self, submission_path: Path) -> Dict:
        """加载单个提交的数据"""
        data = {
            'name': submission_path.name,
            'predictions': None,
            'results': None,
            'trajectories': [],
        }
        
        # 加载预测
        pred_files = list(submission_path.glob('*.jsonl'))
        if pred_files:
            data['predictions'] = self._load_jsonl(pred_files[0])
        
        # 加载结果
        results_file = submission_path / 'results.json'
        if results_file.exists():
            with open(results_file) as f:
                data['results'] = json.load(f)
        
        # 加载轨迹
        trajs_dir = submission_path / 'trajs'
        if trajs_dir.exists():
            for traj_file in trajs_dir.glob('*.traj'):
                with open(traj_file) as f:
                    data['trajectories'].append(json.load(f))
        
        return data
    
    def _load_jsonl(self, path: Path) -> List[Dict]:
        """加载 JSONL 文件"""
        items = []
        with open(path) as f:
            for line in f:
                items.append(json.loads(line))
        return items


# ============================================================================
# 便捷函数
# ============================================================================

def load_swebench_lite() -> List[SWEBenchInstance]:
    """快速加载 SWE-bench Lite"""
    loader = HuggingFaceLoader()
    return loader.load_dataset('swe-bench-lite', 'test')


def load_swebench_verified() -> List[SWEBenchInstance]:
    """快速加载 SWE-bench Verified"""
    loader = HuggingFaceLoader()
    return loader.load_dataset('swe-bench-verified', 'test')


def load_trajectories_from_dir(directory: Path) -> Iterator[Dict]:
    """从目录加载轨迹"""
    loader = LocalFileLoader(directory)
    return loader.iterate_trajectories()


# ============================================================================
# 示例使用
# ============================================================================

if __name__ == '__main__':
    # 示例：加载 SWE-bench Lite 数据集
    print("Loading SWE-bench Lite...")
    
    try:
        instances = load_swebench_lite()
        print(f"Loaded {len(instances)} instances")
        
        # 打印第一个实例
        if instances:
            inst = instances[0]
            print(f"\nFirst instance:")
            print(f"  ID: {inst.instance_id}")
            print(f"  Repo: {inst.repo}")
            print(f"  Problem: {inst.problem_statement[:200]}...")
    except Exception as e:
        print(f"Error: {e}")
        print("Make sure to install datasets: pip install datasets")
