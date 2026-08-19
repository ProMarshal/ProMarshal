"""Structured tool error envelope helpers for Cortex."""

from typing import Any, Dict, Optional

from pydantic import ValidationError


ALLOWED_ERROR_CLASSES = {
    "validation",
    "permission",
    "policy",
    "integration",
    "not_found",
    "conflict",
    "timeout",
    "internal",
    "unknown",
}


def build_tool_error(
    *,
    error_code: str,
    error_class: str,
    retryable: bool,
    http_status: Optional[int],
    user_message: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the canonical tool error envelope."""
    normalized_error_class = str(error_class or "unknown").strip().lower()
    if normalized_error_class not in ALLOWED_ERROR_CLASSES:
        normalized_error_class = "unknown"

    return {
        "ok": False,
        "error_code": str(error_code).strip() or "unknown_error",
        "error_class": normalized_error_class,
        "retryable": bool(retryable),
        "http_status": http_status,
        "user_message": str(user_message).strip() or "Tool execution failed.",
        "details": details or {},
    }


def build_validation_error(
    *,
    tool_name: str,
    error: ValidationError,
) -> Dict[str, Any]:
    """Build a validation-class envelope from Pydantic validation errors."""
    return build_tool_error(
        error_code="invalid_tool_input",
        error_class="validation",
        retryable=False,
        http_status=400,
        user_message="The tool input is invalid. Please correct the arguments and try again.",
        details={
            "tool_name": tool_name,
            "validation_errors": error.errors(),
        },
    )

def build_permission_denied_error(
    *,
    tool_name: str,
    reason: Optional[str],
) -> Dict[str, Any]:
    """Build a permission-denied envelope for execution-time gating."""
    return build_tool_error(
        error_code="permission_denied",
        error_class="permission",
        retryable=False,
        http_status=403,
        user_message="You do not have permission to run this action.",
        details={
            "tool_name": tool_name,
            "reason": reason or "permission_denied",
        },
    )
