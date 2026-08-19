"""Redis-backed queue reliability primitives for Cortex per-session execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

from app.cortex.sessions import build_session_key


RUN_LOCK_PREFIX = "cortex:run:"
SESSION_QUEUE_PREFIX = "cortex:queue:"
INFLIGHT_PREFIX = "cortex:inflight:"
REQUEUED_PREFIX = "cortex:requeued:"
DISPATCH_PREFIX = "cortex:dispatch:"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _from_iso(raw: Optional[str]) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decode_redis_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return value.decode("utf-8", errors="ignore")
    return str(value)


def _json_dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def _json_loads(raw: Any) -> Optional[Dict[str, Any]]:
    decoded = _decode_redis_value(raw)
    if not decoded:
        return None
    try:
        parsed = json.loads(decoded)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def run_lock_key(session_key: str) -> str:
    return f"{RUN_LOCK_PREFIX}{session_key}"


def session_queue_key(session_key: str) -> str:
    return f"{SESSION_QUEUE_PREFIX}{session_key}"


def inflight_key(session_key: str) -> str:
    return f"{INFLIGHT_PREFIX}{session_key}"


def requeued_guard_key(queued_msg_id: str) -> str:
    return f"{REQUEUED_PREFIX}{queued_msg_id}"


def dispatch_key(session_key: str) -> str:
    return f"{DISPATCH_PREFIX}{session_key}"


def session_key_from_ingress(ingress: Dict[str, Any]) -> str:
    return build_session_key(
        project_id=str(ingress.get("project_id") or ""),
        user_id=str(ingress.get("user_id") or ""),
        source=str(ingress.get("source") or ""),
        anchor=str(ingress.get("session_anchor") or ""),
    )


@dataclass(frozen=True)
class QueueItem:
    queued_msg_id: str
    ingress: Dict[str, Any]
    raw_event: Dict[str, Any]
    enqueued_at: str

    def to_payload(self) -> Dict[str, Any]:
        return {
            "queued_msg_id": self.queued_msg_id,
            "ingress": self.ingress,
            "raw_event": self.raw_event,
            "enqueued_at": self.enqueued_at,
        }


def build_queue_item(
    *,
    ingress: Dict[str, Any],
    raw_event: Optional[Dict[str, Any]] = None,
    queued_msg_id: Optional[str] = None,
) -> QueueItem:
    return QueueItem(
        queued_msg_id=str(queued_msg_id or uuid4()),
        ingress=dict(ingress or {}),
        raw_event=dict(raw_event or {}),
        enqueued_at=_to_iso(_utc_now()),
    )


def parse_queue_item(raw: Any) -> Optional[QueueItem]:
    payload = _json_loads(raw)
    if not payload:
        return None
    ingress = payload.get("ingress")
    if not isinstance(ingress, dict):
        return None
    raw_event = payload.get("raw_event")
    if not isinstance(raw_event, dict):
        raw_event = {}
    queued_msg_id = str(payload.get("queued_msg_id") or "").strip() or str(uuid4())
    enqueued_at = str(payload.get("enqueued_at") or "").strip() or _to_iso(_utc_now())
    return QueueItem(
        queued_msg_id=queued_msg_id,
        ingress=ingress,
        raw_event=raw_event,
        enqueued_at=enqueued_at,
    )


def push_session_queue_item(*, redis_conn: Any, session_key: str, item: QueueItem) -> bool:
    try:
        redis_conn.rpush(session_queue_key(session_key), _json_dumps(item.to_payload()))
        return True
    except Exception as exc:
        print(f"Cortex queue push failed: session_key={session_key} error={exc}")
        return False


def pop_session_queue_item(*, redis_conn: Any, session_key: str) -> Optional[QueueItem]:
    try:
        raw = redis_conn.lpop(session_queue_key(session_key))
    except Exception as exc:
        print(f"Cortex queue pop failed: session_key={session_key} error={exc}")
        return None
    return parse_queue_item(raw)


def session_queue_length(*, redis_conn: Any, session_key: str) -> int:
    try:
        return int(redis_conn.llen(session_queue_key(session_key)) or 0)
    except Exception:
        return 0


def try_claim_run_lock(
    *,
    redis_conn: Any,
    session_key: str,
    run_id: str,
    ttl_seconds: int,
) -> bool:
    try:
        created = redis_conn.set(
            run_lock_key(session_key),
            str(run_id),
            ex=max(10, int(ttl_seconds)),
            nx=True,
        )
        return bool(created)
    except Exception as exc:
        print(f"Cortex run lock claim failed: session_key={session_key} error={exc}")
        return False


def renew_run_lock_if_owner(
    *,
    redis_conn: Any,
    session_key: str,
    run_id: str,
    ttl_seconds: int,
) -> bool:
    script = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  redis.call('expire', KEYS[1], tonumber(ARGV[2]))
  return 1
end
return 0
"""
    try:
        renewed = redis_conn.eval(
            script,
            1,
            run_lock_key(session_key),
            str(run_id),
            str(max(10, int(ttl_seconds))),
        )
        return bool(int(renewed or 0))
    except Exception as exc:
        print(f"Cortex run lock heartbeat failed: session_key={session_key} error={exc}")
        return False


def release_run_lock_if_owner(*, redis_conn: Any, session_key: str, run_id: str) -> bool:
    script = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  redis.call('del', KEYS[1])
  return 1
end
return 0
"""
    try:
        released = redis_conn.eval(script, 1, run_lock_key(session_key), str(run_id))
        return bool(int(released or 0))
    except Exception as exc:
        print(f"Cortex run lock release failed: session_key={session_key} error={exc}")
        return False


def has_active_run(*, redis_conn: Any, session_key: str) -> bool:
    try:
        return bool(redis_conn.get(run_lock_key(session_key)))
    except Exception:
        return False


def try_claim_dispatch(
    *,
    redis_conn: Any,
    session_key: str,
    dispatch_token: str,
    ttl_seconds: int,
) -> bool:
    try:
        created = redis_conn.set(
            dispatch_key(session_key),
            str(dispatch_token),
            ex=max(10, int(ttl_seconds)),
            nx=True,
        )
        return bool(created)
    except Exception as exc:
        print(f"Cortex dispatch claim failed: session_key={session_key} error={exc}")
        return False


def has_pending_dispatch(*, redis_conn: Any, session_key: str) -> bool:
    try:
        return bool(redis_conn.get(dispatch_key(session_key)))
    except Exception:
        return False


def release_dispatch_if_owner(
    *,
    redis_conn: Any,
    session_key: str,
    dispatch_token: str,
) -> bool:
    script = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  redis.call('del', KEYS[1])
  return 1
end
return 0
"""
    try:
        released = redis_conn.eval(script, 1, dispatch_key(session_key), str(dispatch_token))
        return bool(int(released or 0))
    except Exception as exc:
        print(f"Cortex dispatch release failed: session_key={session_key} error={exc}")
        return False


def set_inflight(
    *,
    redis_conn: Any,
    session_key: str,
    item: QueueItem,
    run_id: str,
    ttl_seconds: int,
) -> bool:
    payload = {
        "queued_msg_id": item.queued_msg_id,
        "item": item.to_payload(),
        "run_id": str(run_id),
        "popped_at": _to_iso(_utc_now()),
    }
    try:
        redis_conn.set(
            inflight_key(session_key),
            _json_dumps(payload),
            ex=max(30, int(ttl_seconds)),
        )
        return True
    except Exception as exc:
        print(f"Cortex inflight set failed: session_key={session_key} error={exc}")
        return False


def clear_inflight(*, redis_conn: Any, session_key: str) -> None:
    try:
        redis_conn.delete(inflight_key(session_key))
    except Exception as exc:
        print(f"Cortex inflight clear failed: session_key={session_key} error={exc}")


def recover_stale_inflight(
    *,
    redis_conn: Any,
    session_key: str,
    stale_after_seconds: int,
    requeue_guard_ttl_seconds: int,
    inflight_ttl_seconds: int,
) -> Tuple[bool, str]:
    """
    Recover stale inflight item once.

    Returns `(recovered, reason)`.
    """
    try:
        inflight_payload = _json_loads(redis_conn.get(inflight_key(session_key)))
    except Exception as exc:
        return False, f"inflight_read_error:{exc}"

    if not inflight_payload:
        return False, "no_inflight"

    queued_msg_id = str(inflight_payload.get("queued_msg_id") or "").strip()
    raw_item = inflight_payload.get("item")
    popped_at = _from_iso(str(inflight_payload.get("popped_at") or ""))

    if not queued_msg_id or not isinstance(raw_item, dict):
        clear_inflight(redis_conn=redis_conn, session_key=session_key)
        return False, "invalid_inflight_payload"

    age_seconds = None
    if popped_at is not None:
        age_seconds = (_utc_now() - popped_at).total_seconds()
    if age_seconds is None or age_seconds <= max(5, int(stale_after_seconds)):
        return False, "inflight_not_stale"

    guard_key = requeued_guard_key(queued_msg_id)
    try:
        first_requeue = redis_conn.set(
            guard_key,
            b"1",
            ex=max(int(requeue_guard_ttl_seconds), int(inflight_ttl_seconds)),
            nx=True,
        )
    except Exception as exc:
        return False, f"requeue_guard_error:{exc}"

    if not first_requeue:
        clear_inflight(redis_conn=redis_conn, session_key=session_key)
        return False, "requeue_guard_exists"

    item = parse_queue_item(_json_dumps(raw_item))
    if item is None:
        clear_inflight(redis_conn=redis_conn, session_key=session_key)
        return False, "invalid_requeue_item"

    pushed = push_session_queue_item(redis_conn=redis_conn, session_key=session_key, item=item)
    clear_inflight(redis_conn=redis_conn, session_key=session_key)
    if pushed:
        return True, "requeued_stale_inflight"
    return False, "requeue_push_failed"
