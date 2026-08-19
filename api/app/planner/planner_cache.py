"""Redis cache helpers for planner read payloads."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi.encoders import jsonable_encoder

from app.core.config import settings
from app.core.redis_queue import get_redis_connection

_PLANNER_STATUS_PREFIX = "planner_status:v1:"
_PLANNER_ENTITIES_PREFIX = "planner_entities:v1:"
_PLANNER_CURRENT_STAGE_PREFIX = "planner_current_stage:v1:"
_PLANNER_BOOTSTRAP_PAYLOAD_PREFIX = "planner_bootstrap_payload:v1:"
_PLANNER_BOOTSTRAP_STATUS_PREFIX = "planner_bootstrap_status:v1:"
_PLANNER_BOOTSTRAP_LOCK_PREFIX = "planner_bootstrap_lock:v1:"
_METRICS: Dict[str, int] = {
    "status_hits": 0,
    "status_misses": 0,
    "entities_hits": 0,
    "entities_misses": 0,
    "current_stage_hits": 0,
    "current_stage_misses": 0,
    "read_errors": 0,
    "writes": 0,
    "write_errors": 0,
    "invalidations": 0,
    "invalidate_errors": 0,
    "invalidate_all_calls": 0,
    "invalidate_all_keys_deleted": 0,
    "invalidate_all_errors": 0,
    "bootstrap_payload_hits": 0,
    "bootstrap_payload_misses": 0,
    "bootstrap_status_hits": 0,
    "bootstrap_status_misses": 0,
    "bootstrap_lock_acquired": 0,
    "bootstrap_lock_rejected": 0,
    "bootstrap_lock_released": 0,
}


def planner_cache_enabled() -> bool:
    return bool(getattr(settings, "planner_cache_enabled", True))


def _inc(metric: str, amount: int = 1) -> None:
    _METRICS[metric] = int(_METRICS.get(metric, 0) or 0) + int(amount)


def get_planner_cache_metrics() -> Dict[str, int]:
    return dict(_METRICS)


def reset_planner_cache_metrics() -> None:
    for key in list(_METRICS.keys()):
        _METRICS[key] = 0


def _status_key(project_id: str) -> str:
    return f"{_PLANNER_STATUS_PREFIX}{str(project_id or '').strip()}"


def _entities_key(project_id: str, stage: str) -> str:
    return f"{_PLANNER_ENTITIES_PREFIX}{str(project_id or '').strip()}:{str(stage or '').strip()}"


def _entities_pattern(project_id: str) -> str:
    return f"{_PLANNER_ENTITIES_PREFIX}{str(project_id or '').strip()}:*"


def _current_stage_key(project_id: str) -> str:
    return f"{_PLANNER_CURRENT_STAGE_PREFIX}{str(project_id or '').strip()}"


def _bootstrap_payload_key(project_id: str) -> str:
    return f"{_PLANNER_BOOTSTRAP_PAYLOAD_PREFIX}{str(project_id or '').strip()}"


def _bootstrap_status_key(project_id: str) -> str:
    return f"{_PLANNER_BOOTSTRAP_STATUS_PREFIX}{str(project_id or '').strip()}"


def _bootstrap_lock_key(project_id: str) -> str:
    return f"{_PLANNER_BOOTSTRAP_LOCK_PREFIX}{str(project_id or '').strip()}"


def _decode_payload(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _set_payload(key: str, payload: Dict[str, Any], ttl_seconds: int, err_tag: str) -> None:
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    try:
        redis_conn.set(key, json.dumps(jsonable_encoder(payload), ensure_ascii=True), ex=ttl_seconds)
        _inc("writes")
    except Exception as exc:
        _inc("write_errors")
        print(f"{err_tag}: key={key} error={exc}")


def _get_payload(key: str, err_tag: str) -> Optional[Dict[str, Any]]:
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return None
    try:
        return _decode_payload(redis_conn.get(key))
    except Exception as exc:
        _inc("read_errors")
        print(f"{err_tag}: key={key} error={exc}")
        return None


def _delete_key(key: str, err_tag: str) -> None:
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    try:
        redis_conn.delete(key)
        _inc("invalidations")
    except Exception as exc:
        _inc("invalidate_errors")
        print(f"{err_tag}: key={key} error={exc}")


def get_cached_planner_status(project_id: str) -> Optional[Dict[str, Any]]:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return None
    payload = _get_payload(_status_key(normalized_project_id), "planner_cache_status_read_failed")
    _inc("status_hits" if isinstance(payload, dict) else "status_misses")
    return payload


def set_cached_planner_status(project_id: str, payload: Dict[str, Any]) -> None:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return
    ttl_seconds = max(5, int(getattr(settings, "planner_status_cache_ttl_seconds", 60) or 60))
    _set_payload(
        _status_key(normalized_project_id),
        payload,
        ttl_seconds,
        "planner_cache_status_write_failed",
    )


def invalidate_planner_status(project_id: str) -> None:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return
    _delete_key(_status_key(normalized_project_id), "planner_cache_status_invalidate_failed")


def get_cached_stage_entities(project_id: str, stage: str) -> Optional[Dict[str, Any]]:
    normalized_project_id = str(project_id or "").strip()
    normalized_stage = str(stage or "").strip()
    if not normalized_project_id or not normalized_stage:
        return None
    payload = _get_payload(
        _entities_key(normalized_project_id, normalized_stage),
        "planner_cache_entities_read_failed",
    )
    _inc("entities_hits" if isinstance(payload, dict) else "entities_misses")
    return payload


def set_cached_stage_entities(project_id: str, stage: str, payload: Dict[str, Any]) -> None:
    normalized_project_id = str(project_id or "").strip()
    normalized_stage = str(stage or "").strip()
    if not normalized_project_id or not normalized_stage:
        return
    ttl_seconds = max(5, int(getattr(settings, "planner_entities_cache_ttl_seconds", 60) or 60))
    _set_payload(
        _entities_key(normalized_project_id, normalized_stage),
        payload,
        ttl_seconds,
        "planner_cache_entities_write_failed",
    )


def invalidate_stage_entities(project_id: str, stage: str) -> None:
    normalized_project_id = str(project_id or "").strip()
    normalized_stage = str(stage or "").strip()
    if not normalized_project_id or not normalized_stage:
        return
    _delete_key(
        _entities_key(normalized_project_id, normalized_stage),
        "planner_cache_entities_invalidate_failed",
    )


def invalidate_all_stage_entities(project_id: str) -> None:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return
    _inc("invalidate_all_calls")
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    pattern = _entities_pattern(normalized_project_id)
    try:
        deleted_count = 0
        for key in redis_conn.scan_iter(match=pattern, count=200):
            deleted_count += int(redis_conn.delete(key) or 0)
        _inc("invalidate_all_keys_deleted", deleted_count)
    except Exception as exc:
        _inc("invalidate_all_errors")
        print(
            f"planner_cache_entities_invalidate_all_failed: project_id={normalized_project_id} error={exc}"
        )


def get_cached_current_stage(project_id: str) -> Optional[Dict[str, Any]]:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return None
    payload = _get_payload(
        _current_stage_key(normalized_project_id),
        "planner_cache_current_stage_read_failed",
    )
    _inc("current_stage_hits" if isinstance(payload, dict) else "current_stage_misses")
    return payload


def set_cached_current_stage(project_id: str, payload: Dict[str, Any]) -> None:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return
    ttl_seconds = max(5, int(getattr(settings, "planner_current_stage_cache_ttl_seconds", 30) or 30))
    _set_payload(
        _current_stage_key(normalized_project_id),
        payload,
        ttl_seconds,
        "planner_cache_current_stage_write_failed",
    )


def invalidate_current_stage(project_id: str) -> None:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return
    _delete_key(
        _current_stage_key(normalized_project_id),
        "planner_cache_current_stage_invalidate_failed",
    )


def invalidate_all_planner_cache(project_id: str) -> None:
    invalidate_planner_status(project_id)
    invalidate_current_stage(project_id)
    invalidate_all_stage_entities(project_id)
    invalidate_planner_bootstrap_payload(project_id)
    invalidate_planner_bootstrap_job_status(project_id)
    invalidate_planner_bootstrap_job_lock(project_id)


def get_cached_planner_bootstrap_payload(project_id: str) -> Optional[Dict[str, Any]]:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return None
    payload = _get_payload(
        _bootstrap_payload_key(normalized_project_id),
        "planner_cache_bootstrap_payload_read_failed",
    )
    _inc("bootstrap_payload_hits" if isinstance(payload, dict) else "bootstrap_payload_misses")
    return payload


def set_cached_planner_bootstrap_payload(project_id: str, payload: Dict[str, Any]) -> None:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return
    ttl_seconds = max(30, int(getattr(settings, "planner_bootstrap_payload_ttl_seconds", 300) or 300))
    _set_payload(
        _bootstrap_payload_key(normalized_project_id),
        payload,
        ttl_seconds,
        "planner_cache_bootstrap_payload_write_failed",
    )


def invalidate_planner_bootstrap_payload(project_id: str) -> None:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return
    _delete_key(
        _bootstrap_payload_key(normalized_project_id),
        "planner_cache_bootstrap_payload_invalidate_failed",
    )


def invalidate_planner_bootstrap_job_status(project_id: str) -> None:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return
    _delete_key(
        _bootstrap_status_key(normalized_project_id),
        "planner_cache_bootstrap_status_invalidate_failed",
    )


def invalidate_planner_bootstrap_job_lock(project_id: str) -> None:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return
    _delete_key(
        _bootstrap_lock_key(normalized_project_id),
        "planner_cache_bootstrap_lock_invalidate_failed",
    )


def get_planner_bootstrap_job_status(project_id: str) -> Optional[Dict[str, Any]]:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return None
    payload = _get_payload(
        _bootstrap_status_key(normalized_project_id),
        "planner_cache_bootstrap_status_read_failed",
    )
    _inc("bootstrap_status_hits" if isinstance(payload, dict) else "bootstrap_status_misses")
    return payload


def set_planner_bootstrap_job_status(project_id: str, payload: Dict[str, Any]) -> None:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return
    ttl_seconds = max(60, int(getattr(settings, "planner_bootstrap_job_status_ttl_seconds", 1800) or 1800))
    _set_payload(
        _bootstrap_status_key(normalized_project_id),
        payload,
        ttl_seconds,
        "planner_cache_bootstrap_status_write_failed",
    )


def acquire_planner_bootstrap_job_lock(project_id: str) -> bool:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return False
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return False
    ttl_seconds = max(30, int(getattr(settings, "planner_bootstrap_job_lock_ttl_seconds", 120) or 120))
    try:
        acquired = bool(redis_conn.set(_bootstrap_lock_key(normalized_project_id), "1", nx=True, ex=ttl_seconds))
    except Exception as exc:
        _inc("invalidate_errors")
        print(f"planner_cache_bootstrap_lock_acquire_failed: project_id={normalized_project_id} error={exc}")
        return False
    if acquired:
        _inc("bootstrap_lock_acquired")
    else:
        _inc("bootstrap_lock_rejected")
    return acquired


def has_planner_bootstrap_job_lock(project_id: str) -> bool:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return False
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return False
    try:
        return bool(redis_conn.exists(_bootstrap_lock_key(normalized_project_id)))
    except Exception as exc:
        _inc("read_errors")
        print(f"planner_cache_bootstrap_lock_read_failed: project_id={normalized_project_id} error={exc}")
        return False


def release_planner_bootstrap_job_lock(project_id: str) -> None:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    try:
        redis_conn.delete(_bootstrap_lock_key(normalized_project_id))
        _inc("bootstrap_lock_released")
    except Exception as exc:
        _inc("invalidate_errors")
        print(f"planner_cache_bootstrap_lock_release_failed: project_id={normalized_project_id} error={exc}")
