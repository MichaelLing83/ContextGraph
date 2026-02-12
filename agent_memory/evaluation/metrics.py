"""Evaluation metrics calculation."""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ProblemResult:
    """Result of running agent on a single problem."""

    problem_id: str
    attempts: List[bool]  # Success/fail for each attempt
    tokens: List[int]     # Tokens used per attempt

    def __post_init__(self) -> None:
        if len(self.attempts) != len(self.tokens):
            raise ValueError(
                "attempts and tokens must have the same length, "
                f"got {len(self.attempts)} and {len(self.tokens)}"
            )

    @property
    def pass_at_1(self) -> bool:
        return len(self.attempts) > 0 and self.attempts[0]

    @property
    def pass_at_3(self) -> bool:
        return any(self.attempts[:3])

    @property
    def pass_at_5(self) -> bool:
        return any(self.attempts[:5])

    @property
    def first_success_attempt(self) -> Optional[int]:
        """1-indexed attempt number of first success, or None if all failed."""
        for i, success in enumerate(self.attempts):
            if success:
                return i + 1
        return None

    @property
    def total_tokens(self) -> int:
        return sum(self.tokens)


@dataclass
class EvaluationMetrics:
    """Aggregated evaluation metrics."""

    pass_at_1: float
    pass_at_3: float
    pass_at_5: float
    total_problems: int
    avg_tokens_per_problem: float
    avg_attempts_to_success: Optional[float]
    failure_ratio: float = 0.0  # failed_attempts / total_attempts


def calculate_metrics(results: List[ProblemResult]) -> EvaluationMetrics:
    """
    Calculate aggregated metrics from problem results.

    Args:
        results: List of ProblemResult objects

    Returns:
        EvaluationMetrics with pass@k rates and efficiency metrics
    """
    if not results:
        return EvaluationMetrics(
            pass_at_1=0.0,
            pass_at_3=0.0,
            pass_at_5=0.0,
            total_problems=0,
            avg_tokens_per_problem=0.0,
            avg_attempts_to_success=None,
        )

    n = len(results)

    # pass@k rates
    pass_1 = sum(1 for r in results if r.pass_at_1) / n
    pass_3 = sum(1 for r in results if r.pass_at_3) / n
    pass_5 = sum(1 for r in results if r.pass_at_5) / n

    # Token consumption
    total_tokens = sum(r.total_tokens for r in results)
    avg_tokens = total_tokens / n

    # Efficiency (average attempts to first success, only for successful problems)
    success_attempts = [r.first_success_attempt for r in results if r.first_success_attempt is not None]
    avg_attempts = sum(success_attempts) / len(success_attempts) if success_attempts else None

    # Failure ratio: total failed attempts / total attempts
    total_attempts = sum(len(r.attempts) for r in results)
    failed_attempts = sum(
        1 for r in results for success in r.attempts if not success
    )
    failure_ratio = failed_attempts / total_attempts if total_attempts > 0 else 0.0

    return EvaluationMetrics(
        pass_at_1=pass_1,
        pass_at_3=pass_3,
        pass_at_5=pass_5,
        total_problems=n,
        avg_tokens_per_problem=avg_tokens,
        avg_attempts_to_success=avg_attempts,
        failure_ratio=failure_ratio,
    )
