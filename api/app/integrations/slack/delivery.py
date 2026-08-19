"""Shared Slack delivery wrapper with retry/backoff handling."""

from __future__ import annotations

from typing import Any, Dict

from slack_sdk.errors import SlackApiError, SlackRequestError

from app.integrations.common.errors import (
    IntegrationAuthError,
    IntegrationError,
    IntegrationRateLimitError,
    IntegrationRequestError,
    IntegrationTransportError,
)
from app.integrations.common.retry import RetryPolicy, execute_with_retry, parse_retry_after_seconds

_AUTH_ERROR_CODES = {"invalid_auth", "not_authed", "account_inactive", "token_revoked"}
_TRANSIENT_ERROR_CODES = {"internal_error", "fatal_error", "request_timeout", "temporarily_unavailable"}


def _as_dict(response: Any) -> Dict[str, Any]:
    if isinstance(response, dict):
        return response
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return data
    try:
        return dict(response)
    except Exception:
        return {}


def _map_api_error(*, operation: str, exc: SlackApiError) -> IntegrationError:
    response = exc.response
    status_code = getattr(response, "status_code", None)
    headers = getattr(response, "headers", {}) or {}
    data = _as_dict(response)
    error_code = str(data.get("error") or "").strip().lower()

    if status_code == 429 or error_code == "ratelimited":
        retry_after = parse_retry_after_seconds(
            headers.get("Retry-After") if isinstance(headers, dict) else None,
            fallback_seconds=1.0,
        )
        return IntegrationRateLimitError(
            f"Slack rate limited {operation}",
            integration="slack",
            operation=operation,
            retry_after_seconds=retry_after,
            details={"error_code": error_code},
        )

    if status_code in (401, 403) or error_code in _AUTH_ERROR_CODES:
        return IntegrationAuthError(
            f"Slack auth failed {operation}",
            integration="slack",
            operation=operation,
            http_status=status_code,
            details={"error_code": error_code},
        )

    if (status_code is not None and status_code >= 500) or error_code in _TRANSIENT_ERROR_CODES:
        return IntegrationRequestError(
            f"Slack transient request failure {operation}",
            integration="slack",
            operation=operation,
            retryable=True,
            http_status=status_code,
            details={"error_code": error_code},
        )

    if status_code is not None and status_code >= 400:
        return IntegrationRequestError(
            f"Slack request failed {operation}",
            integration="slack",
            operation=operation,
            retryable=False,
            http_status=status_code,
            details={"error_code": error_code},
        )

    return IntegrationRequestError(
        f"Slack API error {operation}",
        integration="slack",
        operation=operation,
        retryable=False,
        details={"error_code": error_code},
    )


def _map_error_payload(*, operation: str, data: Dict[str, Any]) -> IntegrationError:
    error_code = str(data.get("error") or "").strip().lower()
    if error_code == "ratelimited":
        return IntegrationRateLimitError(
            f"Slack rate limited {operation}",
            integration="slack",
            operation=operation,
            retry_after_seconds=1.0,
            details={"error_code": error_code},
        )
    if error_code in _AUTH_ERROR_CODES:
        return IntegrationAuthError(
            f"Slack auth failed {operation}",
            integration="slack",
            operation=operation,
            details={"error_code": error_code},
        )
    if error_code in _TRANSIENT_ERROR_CODES:
        return IntegrationRequestError(
            f"Slack transient request failure {operation}",
            integration="slack",
            operation=operation,
            retryable=True,
            details={"error_code": error_code},
        )
    return IntegrationRequestError(
        f"Slack request failed {operation}",
        integration="slack",
        operation=operation,
        retryable=False,
        details={"error_code": error_code},
    )


async def chat_post_message_with_retry(
    *,
    slack_client: Any,
    payload: Dict[str, Any],
    operation_name: str = "slack.chat_postMessage",
) -> Dict[str, Any]:
    """Call `chat.postMessage` with shared retry/backoff behavior."""

    async def _request() -> Dict[str, Any]:
        base_client = getattr(slack_client, "_client", slack_client)
        try:
            response = await base_client.chat_postMessage(**payload)
        except SlackApiError as exc:
            raise _map_api_error(operation=operation_name, exc=exc) from exc
        except SlackRequestError as exc:
            raise IntegrationTransportError(
                f"Slack transport error {operation_name}: {exc}",
                integration="slack",
                operation=operation_name,
            ) from exc
        except Exception as exc:
            raise IntegrationRequestError(
                f"Slack request error {operation_name}: {exc}",
                integration="slack",
                operation=operation_name,
                retryable=False,
            ) from exc

        data = _as_dict(response)
        if data.get("ok"):
            return data
        raise _map_error_payload(operation=operation_name, data=data)

    return await execute_with_retry(
        operation_name,
        _request,
        policy=RetryPolicy(max_attempts=3, base_delay_seconds=1.0, backoff_multiplier=2.0, max_delay_seconds=8.0),
    )


async def views_open_with_retry(
    *,
    slack_client: Any,
    payload: Dict[str, Any],
    operation_name: str = "slack.views_open",
) -> Dict[str, Any]:
    """Call `views.open` with shared retry/backoff behavior."""

    async def _request() -> Dict[str, Any]:
        base_client = getattr(slack_client, "_client", slack_client)
        try:
            response = await base_client.views_open(**payload)
        except SlackApiError as exc:
            raise _map_api_error(operation=operation_name, exc=exc) from exc
        except SlackRequestError as exc:
            raise IntegrationTransportError(
                f"Slack transport error {operation_name}: {exc}",
                integration="slack",
                operation=operation_name,
            ) from exc
        except Exception as exc:
            raise IntegrationRequestError(
                f"Slack request error {operation_name}: {exc}",
                integration="slack",
                operation=operation_name,
                retryable=False,
            ) from exc

        data = _as_dict(response)
        if data.get("ok"):
            return data
        raise _map_error_payload(operation=operation_name, data=data)

    return await execute_with_retry(
        operation_name,
        _request,
        policy=RetryPolicy(max_attempts=3, base_delay_seconds=1.0, backoff_multiplier=2.0, max_delay_seconds=8.0),
    )


class ResilientSlackWebClient:
    """Proxy wrapper to enforce delivery retry semantics for common Slack send APIs."""

    def __init__(self, client: Any):
        self._client = client

    def __getattr__(self, item: str) -> Any:
        return getattr(self._client, item)

    async def chat_postMessage(self, **kwargs: Any) -> Dict[str, Any]:
        return await chat_post_message_with_retry(
            slack_client=self._client,
            payload=kwargs,
            operation_name="slack.chat_postMessage",
        )

    async def views_open(self, **kwargs: Any) -> Dict[str, Any]:
        return await views_open_with_retry(
            slack_client=self._client,
            payload=kwargs,
            operation_name="slack.views_open",
        )
