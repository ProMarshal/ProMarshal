"""Redis runtime cache helpers for project recommendations."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi.encoders import jsonable_encoder

from app.core.config import settings
from app.core.redis_queue import get_redis_connection

_RECO_PROJECT_PREFIX = "reco:project:"
_RECO_TASK_PREFIX = "reco:task:"
_RECO_META_PREFIX = "reco:meta:"
_RECO_DIRTY_PREFIX = "reco:dirty:"
_RECO_DIRTY_SET_PREFIX = "reco:dirty:set:"
_RECO_API_PROJECT_PREFIX = "reco:api:project:"


def _clean_project_id(project_id: str) -> str:
    return str(project_id or "").strip()


def _clean_task_key(task_key: str) -> str:
    return str(task_key or "").strip()


def _project_key(project_id: str) -> str:
    return f"{_RECO_PROJECT_PREFIX}{project_id}"


def _task_key(project_id: str, task_key: str) -> str:
    return f"{_RECO_TASK_PREFIX}{project_id}:{task_key}"


def _meta_key(project_id: str) -> str:
    return f"{_RECO_META_PREFIX}{project_id}"


def _dirty_key(project_id: str, task_key: str) -> str:
    return f"{_RECO_DIRTY_PREFIX}{project_id}:{task_key}"


def _dirty_set_key(project_id: str) -> str:
    return f"{_RECO_DIRTY_SET_PREFIX}{project_id}"


def _api_project_key(project_id: str) -> str:
    return f"{_RECO_API_PROJECT_PREFIX}{project_id}"


def _default_runtime_ttl_seconds() -> int:
    return max(60, int(getattr(settings, "recommendation_runtime_ttl_seconds", 21600) or 21600))


def _default_payload_ttl_seconds() -> int:
    return max(
        60,
        min(120, int(getattr(settings, "recommendation_payload_cache_ttl_seconds", 90) or 90)),
    )


def _default_dirty_ttl_seconds() -> int:
    return max(30, int(getattr(settings, "recommendation_dirty_ttl_seconds", 1800) or 1800))


def _decode_json_dict(raw: Any) -> Optional[Dict[str, Any]]:
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


def get_project_recommendations(project_id: str) -> Optional[Dict[str, Any]]:
    project = _clean_project_id(project_id)
    if not project:
        return None
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return None
    try:
        return _decode_json_dict(redis_conn.get(_project_key(project)))
    except Exception:
        return None


def set_project_recommendations(project_id: str, payload: Dict[str, Any]) -> None:
    project = _clean_project_id(project_id)
    if not project or not isinstance(payload, dict):
        return
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    try:
        encoded = json.dumps(jsonable_encoder(payload), ensure_ascii=True)
        redis_conn.set(_project_key(project), encoded, ex=_default_runtime_ttl_seconds())
    except Exception:
        return


def get_task_recommendation(project_id: str, task_key: str) -> Optional[Dict[str, Any]]:
    project = _clean_project_id(project_id)
    task = _clean_task_key(task_key)
    if not project or not task:
        return None
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return None
    try:
        return _decode_json_dict(redis_conn.get(_task_key(project, task)))
    except Exception:
        return None


def set_task_recommendation(project_id: str, task_key: str, payload: Dict[str, Any]) -> None:
    project = _clean_project_id(project_id)
    task = _clean_task_key(task_key)
    if not project or not task or not isinstance(payload, dict):
        return
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    try:
        encoded = json.dumps(jsonable_encoder(payload), ensure_ascii=True)
        redis_conn.set(_task_key(project, task), encoded, ex=_default_runtime_ttl_seconds())
    except Exception:
        return


def delete_task_recommendation(project_id: str, task_key: str) -> None:
    project = _clean_project_id(project_id)
    task = _clean_task_key(task_key)
    if not project or not task:
        return
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    try:
        redis_conn.delete(_task_key(project, task))
    except Exception:
        return


def set_recommendation_meta(project_id: str, *, trigger: str, mode: str, version: str = "v1") -> None:
    project = _clean_project_id(project_id)
    if not project:
        return
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    payload = {
        "project_id": project,
        "generated_at": datetime.utcnow().isoformat(),
        "trigger": str(trigger or "").strip() or "unknown",
        "mode": str(mode or "").strip() or "full",
        "version": str(version or "v1").strip() or "v1",
    }
    try:
        encoded = json.dumps(jsonable_encoder(payload), ensure_ascii=True)
        redis_conn.set(_meta_key(project), encoded, ex=_default_runtime_ttl_seconds())
    except Exception:
        return


def get_recommendation_meta(project_id: str) -> Optional[Dict[str, Any]]:
    project = _clean_project_id(project_id)
    if not project:
        return None
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return None
    try:
        return _decode_json_dict(redis_conn.get(_meta_key(project)))
    except Exception:
        return None


def mark_task_dirty(project_id: str, task_key: str) -> bool:
    """
    Mark a task as dirty for coalesced recompute.
    Returns True when this call created/extended the marker.
    """
    project = _clean_project_id(project_id)
    task = _clean_task_key(task_key)
    if not project or not task:
        return False
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return False

    dirty_ttl = _default_dirty_ttl_seconds()
    marker_key = _dirty_key(project, task)
    set_key = _dirty_set_key(project)
    try:
        marker_created = bool(redis_conn.set(marker_key, "1", ex=dirty_ttl, nx=True))
        if not marker_created:
            redis_conn.expire(marker_key, dirty_ttl)
        redis_conn.sadd(set_key, task)
        redis_conn.expire(set_key, dirty_ttl)
        return True
    except Exception:
        return False


def list_dirty_task_keys(project_id: str, *, limit: int = 200) -> List[str]:
    project = _clean_project_id(project_id)
    if not project:
        return []
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return []
    bounded_limit = max(1, min(int(limit or 200), 1000))
    try:
        values = redis_conn.smembers(_dirty_set_key(project))
    except Exception:
        return []
    result: List[str] = []
    for raw in values or []:
        value = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw or "")
        cleaned = value.strip()
        if cleaned:
            result.append(cleaned)
    result.sort()
    return result[:bounded_limit]


def clear_dirty_task_keys(project_id: str, task_keys: List[str]) -> int:
    project = _clean_project_id(project_id)
    if not project:
        return 0
    cleaned = [key.strip() for key in task_keys if str(key or "").strip()]
    if not cleaned:
        return 0
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return 0
    marker_keys = [_dirty_key(project, key) for key in cleaned]
    try:
        removed_from_set = redis_conn.srem(_dirty_set_key(project), *cleaned)
        if marker_keys:
            redis_conn.delete(*marker_keys)
        return int(removed_from_set or 0)
    except Exception:
        return 0


def get_cached_api_project_recommendations(project_id: str) -> Optional[Dict[str, Any]]:
    project = _clean_project_id(project_id)
    if not project:
        return None
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return None
    try:
        return _decode_json_dict(redis_conn.get(_api_project_key(project)))
    except Exception:
        return None


def set_cached_api_project_recommendations(project_id: str, payload: Dict[str, Any]) -> None:
    project = _clean_project_id(project_id)
    if not project or not isinstance(payload, dict):
        return
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    try:
        encoded = json.dumps(jsonable_encoder(payload), ensure_ascii=True)
        redis_conn.set(_api_project_key(project), encoded, ex=_default_payload_ttl_seconds())
    except Exception:
        return


def invalidate_cached_api_project_recommendations(project_id: str) -> None:
    project = _clean_project_id(project_id)
    if not project:
        return
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    try:
        redis_conn.delete(_api_project_key(project))
    except Exception:
        return
