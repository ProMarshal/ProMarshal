"""Shared integration error taxonomy."""

from typing import Any, Dict, Optional


class IntegrationError(Exception):
    """Base exception for integration call failures."""

    def __init__(
        self,
        message: str,
        *,
        integration: str,
        operation: str,
        retryable: bool = False,
        error_code: str = "integration_error",
        http_status: Optional[int] = None,
        retry_after_seconds: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.integration = integration
        self.operation = operation
        self.retryable = retryable
        self.error_code = error_code
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        self.details = details or {}


class IntegrationAuthError(IntegrationError):
    """Auth failures that should fail fast."""

    def __init__(
        self,
        message: str,
        *,
        integration: str,
        operation: str,
        http_status: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message,
            integration=integration,
            operation=operation,
            retryable=False,
            error_code="auth_failed",
            http_status=http_status,
            details=details,
        )


class IntegrationRateLimitError(IntegrationError):
    """Rate-limit failures with optional Retry-After delay."""

    def __init__(
        self,
        message: str,
        *,
        integration: str,
        operation: str,
        retry_after_seconds: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message,
            integration=integration,
            operation=operation,
            retryable=True,
            error_code="rate_limited",
            http_status=429,
            retry_after_seconds=retry_after_seconds,
            details=details,
        )


class IntegrationTransportError(IntegrationError):
    """Network/transport failure where retry is typically safe."""

    def __init__(
        self,
        message: str,
        *,
        integration: str,
        operation: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message,
            integration=integration,
            operation=operation,
            retryable=True,
            error_code="transport_error",
            details=details,
        )


class IntegrationRequestError(IntegrationError):
    """HTTP request failure with explicit retryability."""

    def __init__(
        self,
        message: str,
        *,
        integration: str,
        operation: str,
        retryable: bool,
        http_status: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message,
            integration=integration,
            operation=operation,
            retryable=retryable,
            error_code="request_failed",
            http_status=http_status,
            details=details,
        )
