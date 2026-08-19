"""Cadence interaction idempotency guards for Slack retries and duplicate deliveries."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Dict, Optional

from app.core.config import settings
from app.core.redis_queue import get_redis_connection

_MEMORY_LOCK = Lock()
_MEMORY_KEYS: Dict[str, datetime] = {}
_WARNED_REDIS_FALLBACK = False


def _cleanup_memory(now: datetime) -> None:
    stale = [k for k, v in _MEMORY_KEYS.items() if v <= now]
    for key in stale:
        _MEMORY_KEYS.pop(key, None)


def _stable_hash(value: Any) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        raw = str(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _build_view_submission_key(payload: Dict[str, Any]) -> Optional[str]:
    view = payload.get("view") or {}
    user = payload.get("user") or {}
    callback_id = str(view.get("callback_id") or "").strip()
    view_id = str(view.get("id") or "").strip()
    root_view_id = str(view.get("root_view_id") or "").strip()
    user_id = str(user.get("id") or "").strip()
    state_values = (view.get("state") or {}).get("values") or {}
    state_hash = _stable_hash(state_values)
    if not callback_id or not user_id:
        return None
    return (
        "cadence:idem:view_submission:"
        f"{callback_id}:{user_id}:{view_id or root_view_id}:{state_hash}"
    )


def _build_block_action_key(payload: Dict[str, Any]) -> Optional[str]:
    actions = payload.get("actions") or []
    action = actions[0] if actions else {}
    user = payload.get("user") or {}
    container = payload.get("container") or {}
    message = payload.get("message") or {}

    action_id = str(action.get("action_id") or "").strip()
    user_id = str(user.get("id") or "").strip()
    value = action.get("value")
    ts = (
        str(container.get("message_ts") or "").strip()
        or str(message.get("ts") or "").strip()
    )

    if not action_id or not user_id:
        return None
    return (
        "cadence:idem:block_actions:"
        f"{action_id}:{user_id}:{ts}:{_stable_hash(value)}"
    )


def build_cadence_interaction_key(payload: Dict[str, Any]) -> Optional[str]:
    interaction_type = str(payload.get("type") or "").strip()
    if interaction_type == "view_submission":
        return _build_view_submission_key(payload)
    if interaction_type == "block_actions":
        return _build_block_action_key(payload)
    return None


def is_duplicate_cadence_interaction(payload: Dict[str, Any], *, ttl_seconds: int = 300) -> bool:
    """
    Return True when interaction is already seen within TTL window.

    Redis is preferred for multi-instance safety.
    Falls back to process-local memory (best effort) when Redis unavailable.
    """
    key = build_cadence_interaction_key(payload)
    if not key:
        return False

    ttl_seconds = max(
        30,
        int(
            getattr(settings, "cadence_interaction_idempotency_ttl_seconds", ttl_seconds)
            or ttl_seconds
        ),
    )

    redis_conn = get_redis_connection()
    if redis_conn is not None:
        try:
            created = redis_conn.set(key, b"1", nx=True, ex=max(30, int(ttl_seconds)))
            return not bool(created)
        except Exception as exc:
            global _WARNED_REDIS_FALLBACK
            if not _WARNED_REDIS_FALLBACK:
                print(f"Cadence idempotency Redis guard unavailable; using memory fallback: {exc}")
                _WARNED_REDIS_FALLBACK = True

    now = datetime.utcnow()
    with _MEMORY_LOCK:
        _cleanup_memory(now)
        expires_at = _MEMORY_KEYS.get(key)
        if expires_at and expires_at > now:
            return True
        _MEMORY_KEYS[key] = now + timedelta(seconds=max(30, int(ttl_seconds)))
    return False
