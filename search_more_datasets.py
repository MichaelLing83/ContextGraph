"""
搜索更多 SWE-bench 轨迹数据集
============================
"""

from datasets import list_datasets
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def search_swe_datasets():
    """搜索 HuggingFace 上的 SWE-bench 相关数据集"""
    logger.info("Searching for SWE-bench related datasets on HuggingFace...")

    try:
        all_datasets = list_datasets()

        # 筛选与 SWE 相关的数据集
        swe_datasets = [
            ds for ds in all_datasets
            if any(keyword in ds.lower() for keyword in ['swe', 'agent', 'trajectory', 'bench'])
        ]

        print(f"\nFound {len(swe_datasets)} potentially relevant datasets:\n")
        for ds in sorted(swe_datasets)[:50]:  # 显示前 50 个
            print(f"  - {ds}")

        return swe_datasets

    except Exception as e:
        logger.error(f"Error searching datasets: {e}")
        return []

if __name__ == '__main__':
    datasets = search_swe_datasets()

    print(f"\n\n📌 Recommended datasets to try:")
    recommended = [
        'nebius/SWE-agent-trajectories',
        'princeton-nlp/SWE-bench',
        'SWE-bench/SWE-bench_Lite',
        'SWE-bench/SWE-bench_Verified',
    ]

    for ds in recommended:
        print(f"  - {ds}")
