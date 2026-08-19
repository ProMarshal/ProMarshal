"""Internal service auth helpers for server-to-server endpoints."""

from __future__ import annotations

import hmac
import time
from typing import Optional

from fastapi import Header, HTTPException, status

from app.core.config import settings


def is_valid_internal_secret(candidate: Optional[str]) -> bool:
    provided = str(candidate or "").strip()
    if not provided:
        return False
    current = str(getattr(settings, "internal_service_secret", "") or "").strip()
    previous = str(getattr(settings, "internal_service_secret_previous", "") or "").strip()
    for expected in (current, previous):
        if expected and hmac.compare_digest(provided, expected):
            return True
    return False


def validate_internal_request_timestamp(timestamp_header: Optional[str]) -> None:
    try:
        ts = int(str(timestamp_header or "").strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing or invalid X-Request-Timestamp",
        ) from exc

    skew_seconds = max(1, int(getattr(settings, "internal_request_timestamp_skew_seconds", 60) or 60))
    if abs(time.time() - ts) > skew_seconds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Request timestamp outside acceptable window",
        )


async def require_internal_service_auth(
    x_internal_secret: Optional[str] = Header(None, alias="X-Internal-Secret"),
    x_request_timestamp: Optional[str] = Header(None, alias="X-Request-Timestamp"),
) -> None:
    if not is_valid_internal_secret(x_internal_secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid internal service secret",
        )
    validate_internal_request_timestamp(x_request_timestamp)

