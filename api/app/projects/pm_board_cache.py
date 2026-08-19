"""Redis cache helpers for PM Board summary payloads."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi.encoders import jsonable_encoder
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.redis_queue import get_redis_connection

_PM_BOARD_SUMMARY_CACHE_PREFIX = "pm_board_summary:v1:"
_PM_BOARD_SUMMARY_DIRTY_PREFIX = "pm_board_summary:dirty:v1:"


def _cache_key(custom_project_id: str) -> str:
    return f"{_PM_BOARD_SUMMARY_CACHE_PREFIX}{str(custom_project_id or '').strip()}"


def _dirty_key(custom_project_id: str) -> str:
    return f"{_PM_BOARD_SUMMARY_DIRTY_PREFIX}{str(custom_project_id or '').strip()}"


def get_cached_pm_board_summary(custom_project_id: str) -> Optional[Dict[str, Any]]:
    normalized_project_id = str(custom_project_id or "").strip()
    if not normalized_project_id:
        return None
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return None
    try:
        raw = redis_conn.get(_cache_key(normalized_project_id))
    except Exception as exc:
        print(f"pm_board_cache_read_failed: project_id={normalized_project_id} error={exc}")
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except Exception as exc:
        print(f"pm_board_cache_parse_failed: project_id={normalized_project_id} error={exc}")
        return None
    return payload if isinstance(payload, dict) else None


def set_cached_pm_board_summary(custom_project_id: str, payload: Dict[str, Any]) -> None:
    normalized_project_id = str(custom_project_id or "").strip()
    if not normalized_project_id:
        return
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    ttl_seconds = max(5, int(getattr(settings, "pm_board_summary_cache_ttl_seconds", 1800) or 1800))
    try:
        serialized = json.dumps(jsonable_encoder(payload), ensure_ascii=True)
        redis_conn.set(_cache_key(normalized_project_id), serialized, ex=ttl_seconds)
    except Exception as exc:
        print(f"pm_board_cache_write_failed: project_id={normalized_project_id} error={exc}")


def invalidate_pm_board_summary_cache(custom_project_id: str) -> None:
    normalized_project_id = str(custom_project_id or "").strip()
    if not normalized_project_id:
        return
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    try:
        redis_conn.delete(_cache_key(normalized_project_id))
    except Exception as exc:
        print(f"pm_board_cache_invalidate_failed: project_id={normalized_project_id} error={exc}")


def mark_pm_board_summary_dirty(
    custom_project_id: str,
    *,
    reason: str = "",
    source: str = "",
) -> None:
    """
    Mark PM summary as stale for a project.

    Writes a short-lived dirty marker and invalidates Redis summary cache.
    """
    normalized_project_id = str(custom_project_id or "").strip()
    if not normalized_project_id:
        return
    invalidate_pm_board_summary_cache(normalized_project_id)
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    ttl_seconds = max(60, int(getattr(settings, "pm_board_summary_dirty_ttl_seconds", 1800) or 1800))
    payload = json.dumps(
        {
            "dirty": True,
            "reason": str(reason or "").strip() or "unknown",
            "source": str(source or "").strip() or "unknown",
        },
        ensure_ascii=True,
    )
    try:
        redis_conn.set(_dirty_key(normalized_project_id), payload, ex=ttl_seconds)
    except Exception as exc:
        print(f"pm_board_dirty_mark_failed: project_id={normalized_project_id} error={exc}")


def is_pm_board_summary_dirty(custom_project_id: str) -> bool:
    normalized_project_id = str(custom_project_id or "").strip()
    if not normalized_project_id:
        return False
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return False
    try:
        return bool(redis_conn.exists(_dirty_key(normalized_project_id)))
    except Exception as exc:
        print(f"pm_board_dirty_read_failed: project_id={normalized_project_id} error={exc}")
        return False


def clear_pm_board_summary_dirty(custom_project_id: str) -> None:
    normalized_project_id = str(custom_project_id or "").strip()
    if not normalized_project_id:
        return
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    try:
        redis_conn.delete(_dirty_key(normalized_project_id))
    except Exception as exc:
        print(f"pm_board_dirty_clear_failed: project_id={normalized_project_id} error={exc}")


async def invalidate_pm_board_summary_cache_by_object_id(
    db: AsyncIOMotorDatabase,
    object_id: str,
) -> None:
    """Resolve custom project_id from Mongo _id and invalidate PM Board cache."""
    normalized_object_id = str(object_id or "").strip()
    if not normalized_object_id:
        return
    try:
        project_doc = await db.projects.find_one(
            {"_id": ObjectId(normalized_object_id)},
            {"project_id": 1},
        )
    except Exception as exc:
        print(f"pm_board_cache_project_lookup_failed: object_id={normalized_object_id} error={exc}")
        return
    custom_project_id = str((project_doc or {}).get("project_id") or "").strip()
    if not custom_project_id:
        return
    invalidate_pm_board_summary_cache(custom_project_id)
