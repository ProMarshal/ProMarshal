"""Shared async retry helpers for integration calls."""

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """Retry policy with exponential backoff."""

    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 30.0


def parse_retry_after_seconds(raw_value: Optional[str], fallback_seconds: float) -> float:
    """Parse Retry-After header safely and clamp invalid values."""
    if raw_value is None:
        return fallback_seconds
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        return fallback_seconds
    return parsed if parsed >= 0 else fallback_seconds


def compute_retry_delay_seconds(
    attempt_index: int,
    policy: RetryPolicy,
    retry_after_seconds: Optional[float] = None,
) -> float:
    """Compute retry delay from explicit Retry-After or exponential backoff."""
    if retry_after_seconds is not None and retry_after_seconds >= 0:
        return min(retry_after_seconds, policy.max_delay_seconds)

    backoff = policy.base_delay_seconds * (policy.backoff_multiplier ** attempt_index)
    return min(backoff, policy.max_delay_seconds)


async def execute_with_retry(
    operation_name: str,
    operation: Callable[[], Awaitable[T]],
    *,
    policy: Optional[RetryPolicy] = None,
    on_retry: Optional[Callable[[int, int, float, Exception], None]] = None,
) -> T:
    """
    Execute an async operation with shared retry semantics.

    Retries only when the raised exception has attribute `retryable=True`.
    """
    retry_policy = policy or RetryPolicy()
    max_attempts = max(1, retry_policy.max_attempts)

    for attempt_number in range(1, max_attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            retryable = bool(getattr(exc, "retryable", False))
            if (not retryable) or attempt_number >= max_attempts:
                raise

            retry_after = getattr(exc, "retry_after_seconds", None)
            delay_seconds = compute_retry_delay_seconds(
                attempt_index=attempt_number - 1,
                policy=retry_policy,
                retry_after_seconds=retry_after,
            )
            if on_retry:
                on_retry(attempt_number, max_attempts, delay_seconds, exc)
            await asyncio.sleep(delay_seconds)

    raise RuntimeError(f"Retry loop exhausted unexpectedly for {operation_name}")
