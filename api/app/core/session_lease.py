"""Cadence session lease primitives backed by Redis."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Optional, Tuple

from app.core.redis_queue import get_redis_connection

@dataclass(frozen=True)
class ActiveSessionLease:
    session_id: str
    lease_token: int
    enforce: bool


_ACTIVE_SESSION_LEASE: ContextVar[Optional[ActiveSessionLease]] = ContextVar(
    "active_session_lease",
    default=None,
)


def cadence_lock_key(session_id: str) -> str:
    normalized = str(session_id or "").strip()
    return f"cadence:{{{normalized}}}:lock"


def cadence_fence_key(session_id: str) -> str:
    normalized = str(session_id or "").strip()
    return f"cadence:{{{normalized}}}:fence"


def acquire_session_lease(session_id: str, ttl_seconds: int = 30) -> Tuple[bool, int]:
    """
    Acquire a lease for one cadence session.

    Returns:
    - `(True, token)` when acquired
    - `(False, token)` when contended
    - `(False, 0)` when redis/session_id invalid
    """
    normalized = str(session_id or "").strip()
    if not normalized:
        return False, 0

    redis_conn = get_redis_connection()
    if redis_conn is None:
        return False, 0

    ttl = max(5, int(ttl_seconds or 30))
    try:
        token = int(redis_conn.incr(cadence_fence_key(normalized)) or 0)
        created = redis_conn.set(
            cadence_lock_key(normalized),
            str(token),
            ex=ttl,
            nx=True,
        )
        return bool(created), token
    except Exception:
        return False, 0


def renew_session_lease(session_id: str, token: int, ttl_seconds: int = 30) -> bool:
    """Renew lease TTL only if the caller still owns the lease token."""
    normalized = str(session_id or "").strip()
    if not normalized or int(token or 0) <= 0:
        return False

    redis_conn = get_redis_connection()
    if redis_conn is None:
        return False

    ttl = max(5, int(ttl_seconds or 30))
    script = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
return 0
"""
    try:
        renewed = redis_conn.eval(
            script,
            1,
            cadence_lock_key(normalized),
            str(int(token)),
            str(ttl),
        )
        return bool(int(renewed or 0))
    except Exception:
        return False


def set_active_session_lease(
    *,
    session_id: str,
    lease_token: int,
    enforce: bool,
) -> Token:
    normalized = str(session_id or "").strip()
    token_value = int(lease_token or 0)
    lease_obj: Optional[ActiveSessionLease]
    if normalized and token_value > 0:
        lease_obj = ActiveSessionLease(
            session_id=normalized,
            lease_token=token_value,
            enforce=bool(enforce),
        )
    else:
        lease_obj = None
    return _ACTIVE_SESSION_LEASE.set(lease_obj)


def reset_active_session_lease(token: Token) -> None:
    try:
        _ACTIVE_SESSION_LEASE.reset(token)
    except Exception:
        return


def get_active_session_lease() -> Optional[ActiveSessionLease]:
    try:
        return _ACTIVE_SESSION_LEASE.get()
    except Exception:
        return None


def release_session_lease(session_id: str, token: int) -> bool:
    """Release lease only if owned by the given token."""
    normalized = str(session_id or "").strip()
    if not normalized or int(token or 0) <= 0:
        return False

    redis_conn = get_redis_connection()
    if redis_conn is None:
        return False

    script = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
    try:
        released = redis_conn.eval(
            script,
            1,
            cadence_lock_key(normalized),
            str(int(token)),
        )
        return bool(int(released or 0))
    except Exception:
        return False
