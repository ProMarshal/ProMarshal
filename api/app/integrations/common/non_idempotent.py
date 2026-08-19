"""Shared verify-first executor for non-idempotent integration writes."""

import asyncio
from typing import Awaitable, Callable, Optional, Tuple, Type

from app.integrations.common.errors import IntegrationError, IntegrationTransportError
from app.integrations.common.retry import RetryPolicy, compute_retry_delay_seconds


async def execute_non_idempotent_write_with_verify(
    *,
    operation_name: str,
    write_operation: Callable[[], Awaitable[None]],
    verify_operation: Callable[[], Awaitable[bool]],
    max_retries: int,
    integration: str,
    on_retry: Optional[Callable[[int, int, float, Exception], None]] = None,
    ambiguous_exception_types: Optional[Tuple[Type[Exception], ...]] = None,
) -> None:
    """
    Execute a non-idempotent write using verify-first semantics.

    Policy:
    - Never replay after explicit response errors.
    - For ambiguous no-response failures, verify persisted state first.
    - Retry only when verification does not confirm persistence.
    """
    retry_policy = RetryPolicy(max_attempts=max(1, max_retries))
    max_attempts = max(1, retry_policy.max_attempts)
    ambiguous_types = ambiguous_exception_types or (IntegrationTransportError,)
    integration_label = str(integration or "integration").strip().lower() or "integration"

    for attempt_number in range(1, max_attempts + 1):
        try:
            await write_operation()
            return
        except ambiguous_types as exc:
            print(
                f"{integration_label} verify-first guard: ambiguous no-response for "
                f"{operation_name}; verifying persisted state (attempt {attempt_number}/{max_attempts})"
            )
            verified = False
            try:
                verified = bool(await verify_operation())
            except Exception as verify_exc:
                print(
                    f"{integration_label} verify-first check failed for {operation_name}: {verify_exc}"
                )

            if verified:
                print(
                    f"{integration_label} verify-first confirmed persistence for {operation_name}; "
                    "skipping replay."
                )
                return

            if attempt_number >= max_attempts:
                raise IntegrationError(
                    f"Ambiguous no-response for {operation_name}; verify-first did not confirm persistence",
                    integration=integration_label,
                    operation=operation_name,
                    retryable=False,
                    error_code="ambiguous_write_not_verified",
                    details={"attempts": attempt_number},
                ) from exc

            delay_seconds = compute_retry_delay_seconds(
                attempt_index=attempt_number - 1,
                policy=retry_policy,
                retry_after_seconds=getattr(exc, "retry_after_seconds", None),
            )
            if on_retry:
                on_retry(attempt_number, max_attempts, delay_seconds, exc)
            await asyncio.sleep(delay_seconds)
        except IntegrationError as exc:
            print(
                f"{integration_label} non-idempotent write not replayed for {operation_name}: "
                f"error_code={exc.error_code} http_status={exc.http_status}"
            )
            raise
