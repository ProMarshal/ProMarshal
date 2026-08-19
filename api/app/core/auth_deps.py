"""Authentication dependencies for protected API routes."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Header, HTTPException, Request, status

from app.core.config import settings
from app.core.security import decode_access_token

_PUBLIC_AUTH_BYPASS_PREFIXES = (
    "/api/auth/send-otp",
    "/api/auth/verify-otp",
    "/api/projects/timezones",
    "/api/projects/ops/pm-board-summary-metrics",
    "/api/projects/invite/verify/",
    "/api/projects/invite/accept/",
    "/api/projects/invite/decline/",
    "/api/integrations/slack/callback",
    "/api/integrations/jira/oauth",
    "/api/integrations/slack/events",
    "/api/integrations/slack/interactions",
    "/api/integrations/slack/commands",
)


def _is_public_bypass_path(path: str) -> bool:
    normalized = str(path or "").strip()
    if not normalized:
        return False
    return any(normalized.startswith(prefix) for prefix in _PUBLIC_AUTH_BYPASS_PREFIXES)


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
) -> Dict[str, Any]:
    """
    Resolve the authenticated actor from Authorization: Bearer <token>.

    Returns:
        Dict with at least {"user_id": str, "name": str}
    """
    header_value = str(authorization or "").strip()
    request_path = str(getattr(request.url, "path", "") or "")
    if not header_value and _is_public_bypass_path(request_path):
        return {"user_id": "", "name": "", "sub": ""}
    if not header_value:
        if not settings.jwt_enforcement_enabled:
            fallback_user_id = str(x_user_id or request.query_params.get("user_id") or "").strip()
            if fallback_user_id:
                return {"user_id": fallback_user_id, "name": "", "sub": fallback_user_id}
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    scheme, _, token = header_value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format",
        )
    try:
        payload = decode_access_token(token.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    return {
        "user_id": str(payload.get("user_id") or "").strip(),
        "name": str(payload.get("name") or "").strip(),
        "sub": str(payload.get("sub") or "").strip(),
    }
