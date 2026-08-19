"""Distributed lock helpers for cadence task-reminder cron runs."""

from __future__ import annotations

import uuid
from typing import Optional, Tuple

from app.core.config import settings
from app.core.redis_queue import get_redis_connection

LOCK_KEY = "cadence:cron:task-reminders:lock"


def acquire_task_reminder_lock(owner_hint: str) -> Tuple[bool, Optional[str], str]:
    """
    Acquire cadence cron lock.

    Returns:
        (acquired, token, reason)
    """
    if not getattr(settings, "cadence_cron_lock_enabled", True):
        return True, None, "lock_disabled"

    redis_conn = get_redis_connection()
    if redis_conn is None:
        # Best-effort degrade so local/dev still runs.
        return True, None, "redis_unavailable"

    token = f"{owner_hint}:{uuid.uuid4()}"
    ttl = max(60, int(getattr(settings, "cadence_cron_lock_ttl_seconds", 1800) or 1800))
    try:
        acquired = redis_conn.set(LOCK_KEY, token.encode("utf-8"), nx=True, ex=ttl)
        if acquired:
            return True, token, "acquired"
        return False, None, "already_locked"
    except Exception as exc:
        print(f"Cadence cron lock acquire failed, running without lock: {exc}")
        return True, None, "lock_error"


def release_task_reminder_lock(token: Optional[str]) -> None:
    """Release cadence cron lock if token ownership matches."""
    if not token:
        return

    redis_conn = get_redis_connection()
    if redis_conn is None:
        return

    try:
        release_script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("DEL", KEYS[1])
        else
            return 0
        end
        """
        redis_conn.eval(release_script, 1, LOCK_KEY, token.encode("utf-8"))
    except Exception as exc:
        print(f"Cadence cron lock release failed: {exc}")

