"""Recommendation generation orchestration (full + incremental)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core.database import get_brain_collection, get_database
from app.projects.health_service import HealthService
from app.projects.pm_board_cache import invalidate_pm_board_summary_cache
from app.projects.recommendations.recommendation_engine import build_recommendations_from_health
from app.projects.recommendations.recommendation_text_extractor import RecommendationTextExtractor
from app.projects.recommendations.recommendation_thresholds import resolve_project_recommendation_thresholds
from app.projects.recommendations.recommendation_runtime_cache import (
    clear_dirty_task_keys,
    delete_task_recommendation,
    get_project_recommendations,
    get_task_recommendation,
    invalidate_cached_api_project_recommendations,
    list_dirty_task_keys,
    mark_task_dirty,
    set_project_recommendations,
    set_recommendation_meta,
    set_task_recommendation,
)
from app.projects.recommendations.recommendation_snapshot_store import (
    get_recommendation_snapshot_payload,
    upsert_recommendation_snapshot,
)

_SEVERITY_RANK = {"critical": 0, "at_risk": 1, "info": 2}
_COMPLETED_STATUSES = {"done", "completed", "closed", "resolved"}
_COMPLETED_STATUS_TOKENS = _COMPLETED_STATUSES | {
    "canceled",
    "cancelled",
    "wont_do",
    "won_t_do",
    "wontfix",
    "won_t_fix",
}
_TEXT_EXTRACTOR = RecommendationTextExtractor()
_COMMITMENT_ISO_PATTERN = re.compile(
    r"\b(20\d{2}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?)\b"
)
_COMMITMENT_FIELD_KEYS = (
    "commitment_eta",
    "commitment_due_at",
    "commitment_due_date",
    "owner_commitment_eta",
    "eta",
    "eta_at",
    "eta_date",
    "target_date",
    "target_at",
    "next_update_at",
)
_DUE_FIELD_KEYS = ("due_date", "dueDate", "due")


def _safe_get_brain_collection(project_id: str):
    """Best-effort collection access for test/runtime paths where DB may be unavailable."""
    try:
        collection = get_brain_collection(project_id)
    except Exception:
        return None
    return collection


async def _resolve_project_runtime_config(custom_project_id: str) -> Dict[str, Any]:
    try:
        db = get_database()
        if db is None:
            return {
                "thresholds": resolve_project_recommendation_thresholds({}),
                "project_timezone": None,
                "workflow_capabilities": {},
            }
        project_doc = await db.projects.find_one(
            {"project_id": custom_project_id},
            {
                "recommendation_thresholds": 1,
                "timezone": 1,
                "workflow_capabilities": 1,
                "recommendation_status_category_map": 1,
                "status_category_map": 1,
            },
        )
        safe_doc = project_doc if isinstance(project_doc, dict) else {}
        workflow_capabilities = safe_doc.get("workflow_capabilities")
        if not isinstance(workflow_capabilities, dict):
            workflow_capabilities = {}
        status_category_map = safe_doc.get("recommendation_status_category_map")
        if not isinstance(status_category_map, dict):
            status_category_map = safe_doc.get("status_category_map")
        if not isinstance(status_category_map, dict):
            status_category_map = {}
        return {
            "thresholds": resolve_project_recommendation_thresholds(safe_doc),
            "project_timezone": str(safe_doc.get("timezone") or "").strip() or None,
            "workflow_capabilities": workflow_capabilities,
            "status_category_map": status_category_map,
        }
    except Exception:
        return {
            "thresholds": resolve_project_recommendation_thresholds({}),
            "project_timezone": None,
            "workflow_capabilities": {},
            "status_category_map": {},
        }


def _build_health_payload(
    tasks: List[Dict[str, Any]],
    *,
    project_timezone: Optional[str] = None,
    workflow_capabilities: Optional[Dict[str, Any]] = None,
    status_category_map: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now = datetime.utcnow()
    if not tasks:
        payload = {
            "calculated_at": now,
            "data_source": "computed",
            "health_type": "none",
            "message": "No tasks found for this project.",
            "summary": {"on_track": 0, "at_risk": 0, "critical": 0, "total_tasks": 0},
            "work_item_summary": {"total": 0, "completed": 0, "on_track": 0, "at_risk": 0, "critical": 0, "open_total": 0},
            "work_items": [],
        }
    else:
        payload = {
            "calculated_at": now,
            "data_source": "computed",
            **HealthService.calculate_health(tasks),
        }
    if str(project_timezone or "").strip():
        payload["project_timezone"] = str(project_timezone).strip()
    if isinstance(workflow_capabilities, dict) and workflow_capabilities:
        payload["workflow_capabilities"] = workflow_capabilities
    if isinstance(status_category_map, dict) and status_category_map:
        payload["status_category_map"] = status_category_map
    return payload


def _sort_recommendations(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        [item for item in items if isinstance(item, dict)],
        key=lambda row: (
            _SEVERITY_RANK.get(str(row.get("severity") or "").strip(), 9),
            str(row.get("id") or ""),
        ),
    )


def _normalize_status_token(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _is_task_completed(task_row: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(task_row, dict):
        return False
    raw_is_closed = task_row.get("is_closed")
    if isinstance(raw_is_closed, bool) and raw_is_closed:
        return True
    status = _normalize_status_token(task_row.get("status"))
    if status in _COMPLETED_STATUS_TOKENS:
        return True
    status_category = _normalize_status_token(task_row.get("status_category"))
    if status_category in {"done", "completed", "closed"}:
        return True
    resolution = _normalize_status_token(task_row.get("resolution"))
    if resolution in {"done", "completed", "resolved", "closed"}:
        return True
    return False


def _coerce_eta_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    isoformat_fn = getattr(value, "isoformat", None)
    if callable(isoformat_fn):
        try:
            return str(isoformat_fn())
        except Exception:
            pass
    parsed = _parse_iso_datetime(value)
    if parsed is not None:
        return parsed.isoformat()
    raw = str(value).strip()
    return raw or None


def _extract_from_mapping_eta(mapping: Dict[str, Any], *, keys: Tuple[str, ...]) -> Optional[str]:
    for key in keys:
        eta = _coerce_eta_value(mapping.get(key))
        if eta:
            return eta
    return None


def _extract_commitment_eta_from_text(
    task_row: Dict[str, Any],
    recommendation: Optional[Dict[str, Any]],
) -> Optional[str]:
    text_chunks: List[str] = []
    for key in ("last_comment", "comment", "health_action", "health_reason", "description"):
        raw = str(task_row.get(key) or "").strip()
        if raw:
            text_chunks.append(raw)
    comments = task_row.get("comments")
    if isinstance(comments, list):
        for item in reversed(comments[-3:]):
            if not isinstance(item, dict):
                continue
            raw = str(item.get("text") or "").strip()
            if raw:
                text_chunks.append(raw)
    if isinstance(recommendation, dict):
        rec_text = str(recommendation.get("recommendation") or "").strip()
        if rec_text:
            text_chunks.append(rec_text)
        why_items = recommendation.get("why")
        if isinstance(why_items, list):
            for why in why_items:
                raw = str(why or "").strip()
                if raw:
                    text_chunks.append(raw)
    for chunk in text_chunks:
        for match in _COMMITMENT_ISO_PATTERN.finditer(chunk):
            eta = _coerce_eta_value(match.group(1))
            if eta:
                return eta
    return None


def _extract_task_commitment_eta(
    task_row: Optional[Dict[str, Any]],
    recommendation: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], str]:
    if not isinstance(task_row, dict):
        return None, "health"
    eta = _extract_from_mapping_eta(task_row, keys=_COMMITMENT_FIELD_KEYS)
    if eta:
        return eta, "commitment_field"
    commitment = task_row.get("commitment")
    if isinstance(commitment, dict):
        eta = _extract_from_mapping_eta(
            commitment,
            keys=("eta", "eta_at", "due", "due_at", "due_date", "by", "target", "target_at"),
        )
        if eta:
            return eta, "commitment_field"
    eta = _extract_from_mapping_eta(task_row, keys=_DUE_FIELD_KEYS)
    if eta:
        return eta, "due_date"
    eta = _extract_commitment_eta_from_text(task_row, recommendation)
    if eta:
        return eta, "commitment_text"
    for key in ("commitment_eta",):
        value = task_row.get(key)
        if value is None:
            continue
        eta = _coerce_eta_value(value)
        if eta:
            return eta, "commitment_field"
    return None, "health"


def _task_runtime_payload(
    project_id: str,
    recommendation: Dict[str, Any],
    *,
    task_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now = datetime.utcnow().isoformat()
    task_key = str(recommendation.get("taskKey") or "").strip()
    commitment_eta, last_signal_source = _extract_task_commitment_eta(task_row, recommendation)
    commitment_status = "met" if _is_task_completed(task_row) else "open"
    return {
        "project_id": project_id,
        "task_key": task_key,
        "severity": recommendation.get("severity"),
        "recommendation": recommendation.get("recommendation"),
        "why": recommendation.get("why") or [],
        "confidence": recommendation.get("confidence"),
        "assignee_suggestion": recommendation.get("assignee_suggestion"),
        "state": {
            "commitment_eta": commitment_eta,
            "commitment_status": commitment_status,
            "last_signal_at": now,
            "last_signal_source": last_signal_source,
        },
        "version": "v1",
        "updated_at": now,
    }


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        normalized = raw
        # Support trailing Z by normalizing to explicit UTC offset for fromisoformat.
        normalized = normalized.replace("Z", "+00:00")
        # Support compact timezone offsets like +0530 / -0700.
        normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", normalized)
        # Normalize date-time separator when source uses a whitespace split.
        normalized = re.sub(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}(:\d{2})?)", r"\1T\2", normalized)
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            return parsed.replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def _escalate_recommendation_text(current: str) -> str:
    text = str(current or "").strip()
    marker = "Commitment ETA missed."
    if not text:
        return f"{marker} Recover plan and owner commitment today."
    if marker.lower() in text.lower():
        return text
    return f"{marker} {text}"


async def _load_project_payload(project_id: str) -> Tuple[Dict[str, Any], str]:
    """
    Load project recommendation payload with Redis-first, snapshot fallback strategy.
    """
    cached = get_project_recommendations(project_id)
    if isinstance(cached, dict) and isinstance(cached.get("recommendations"), list):
        return cached, "redis"

    try:
        snapshot_payload = await get_recommendation_snapshot_payload(project_id)
    except Exception:
        snapshot_payload = None
    if isinstance(snapshot_payload, dict) and isinstance(snapshot_payload.get("recommendations"), list):
        # Warm Redis for subsequent reads in same window.
        set_project_recommendations(project_id, snapshot_payload)
        return snapshot_payload, "snapshot"

    return {}, "none"


async def _persist_project_payload(project_id: str, payload: Dict[str, Any], *, trigger: str, mode: str) -> None:
    set_project_recommendations(project_id, payload)
    set_recommendation_meta(
        project_id,
        trigger=str(trigger or "").strip() or "unknown",
        mode=str(mode or "").strip() or "unknown",
        version="v1",
    )
    try:
        await upsert_recommendation_snapshot(project_id, payload)
    except Exception:
        # Snapshot persistence is secondary to runtime cache durability in this path.
        pass
    invalidate_cached_api_project_recommendations(project_id)
    invalidate_pm_board_summary_cache(project_id)


async def _reevaluate_commitment_misses(project_id: str) -> Dict[str, Any]:
    """
    Re-evaluate commitment ETA status transitions between full runs.
    """
    existing_project_payload, _ = await _load_project_payload(project_id)
    existing_list = existing_project_payload.get("recommendations")
    existing_recommendations: List[Dict[str, Any]] = (
        [item for item in existing_list if isinstance(item, dict)] if isinstance(existing_list, list) else []
    )
    if not existing_recommendations:
        return {"transitioned": 0, "met": 0, "count": 0}

    task_keys = [
        str(item.get("taskKey") or "").strip()
        for item in existing_recommendations
        if str(item.get("taskKey") or "").strip()
    ]
    task_by_key: Dict[str, Dict[str, Any]] = {}
    if task_keys:
        try:
            brain_collection = _safe_get_brain_collection(project_id)
            if brain_collection is None:
                task_rows = []
            else:
                task_rows = await brain_collection.find(
                    {
                        "entity_type": "work_item",
                        "external_id": {"$in": task_keys},
                    }
                ).to_list(length=None)
            task_by_key = {
                str(row.get("external_id") or "").strip(): row
                for row in task_rows
                if str(row.get("external_id") or "").strip()
            }
        except Exception:
            task_by_key = {}

    transitioned = 0
    transitioned_met = 0
    now = datetime.utcnow()
    updated_items: List[Dict[str, Any]] = []
    for item in existing_recommendations:
        task_key = str(item.get("taskKey") or "").strip()
        if not task_key:
            updated_items.append(item)
            continue

        task_runtime = get_task_recommendation(project_id, task_key) or {}
        state = dict(task_runtime.get("state") or {})
        commitment_eta = _parse_iso_datetime(state.get("commitment_eta"))
        commitment_status = str(state.get("commitment_status") or "open").strip().lower() or "open"
        task_row = task_by_key.get(task_key)
        is_completed = _is_task_completed(task_row)
        if is_completed and commitment_status == "open":
            state["commitment_status"] = "met"
            state["last_signal_at"] = now.isoformat()
            state["last_signal_source"] = "completion"
            task_runtime["state"] = state
            task_runtime["updated_at"] = now.isoformat()
            set_task_recommendation(project_id, task_key, task_runtime)
            updated_items.append(item)
            transitioned_met += 1
        elif commitment_eta and commitment_status == "open" and commitment_eta <= now:
            state["commitment_status"] = "missed"
            state["last_signal_at"] = now.isoformat()
            state["last_signal_source"] = "commitment_eta"
            task_runtime["state"] = state
            task_runtime["severity"] = "critical"
            task_runtime["recommendation"] = _escalate_recommendation_text(task_runtime.get("recommendation"))
            task_runtime["updated_at"] = now.isoformat()
            set_task_recommendation(project_id, task_key, task_runtime)

            updated_item = dict(item)
            updated_item["severity"] = "critical"
            updated_item["recommendation"] = _escalate_recommendation_text(updated_item.get("recommendation"))
            updated_items.append(updated_item)
            transitioned += 1
        else:
            updated_items.append(item)

    if transitioned or transitioned_met:
        await _persist_project_payload(
            project_id,
            {
                "project_id": project_id,
                "generated_at": datetime.utcnow().isoformat(),
                "trigger": "commitment_transition",
                "mode": "incremental",
                "version": "v1",
                "count": len(updated_items),
                "recommendations": _sort_recommendations(updated_items),
            },
            trigger="commitment_transition",
            mode="incremental",
        )
    return {"transitioned": transitioned, "met": transitioned_met, "count": len(updated_items)}


async def _cleanup_completed_recommendations(project_id: str) -> Dict[str, Any]:
    """
    Remove completed tasks from recommendation runtime payload between full runs.
    """
    existing_project_payload, _ = await _load_project_payload(project_id)
    existing_list = existing_project_payload.get("recommendations")
    existing_recommendations: List[Dict[str, Any]] = (
        [item for item in existing_list if isinstance(item, dict)] if isinstance(existing_list, list) else []
    )
    if not existing_recommendations:
        return {"removed": 0, "remaining": 0}

    task_keys = [
        str(item.get("taskKey") or "").strip()
        for item in existing_recommendations
        if str(item.get("taskKey") or "").strip()
    ]
    if not task_keys:
        return {"removed": 0, "remaining": len(existing_recommendations)}

    brain_collection = _safe_get_brain_collection(project_id)
    if brain_collection is None:
        return {"removed": 0, "remaining": len(existing_recommendations)}
    completed_rows = await brain_collection.find(
        {
            "entity_type": "work_item",
            "external_id": {"$in": task_keys},
        }
    ).to_list(length=None)
    completed_keys = {
        str(row.get("external_id") or "").strip()
        for row in completed_rows
        if str(row.get("external_id") or "").strip() and _is_task_completed(row)
    }
    if not completed_keys:
        return {"removed": 0, "remaining": len(existing_recommendations)}

    for task_key in completed_keys:
        delete_task_recommendation(project_id, task_key)

    remaining = [
        item for item in existing_recommendations
        if str(item.get("taskKey") or "").strip() not in completed_keys
    ]
    await _persist_project_payload(
        project_id,
        {
            "project_id": project_id,
            "generated_at": datetime.utcnow().isoformat(),
            "trigger": "cleanup_completed",
            "mode": "cleanup",
            "version": "v1",
            "count": len(remaining),
            "recommendations": _sort_recommendations(remaining),
        },
        trigger="cleanup_completed",
        mode="cleanup",
    )
    return {"removed": len(completed_keys), "remaining": len(remaining)}


async def _prune_completed_recommendations_from_list(
    project_id: str,
    recommendations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Remove completed-task recommendations from a provided list.
    Used by dirty-path recompute to preserve completion cleanup parity.
    """
    task_keys = [
        str(item.get("taskKey") or "").strip()
        for item in recommendations
        if str(item.get("taskKey") or "").strip()
    ]
    if not task_keys:
        return {"recommendations": recommendations, "removed": 0}

    brain_collection = _safe_get_brain_collection(project_id)
    if brain_collection is None:
        return {"recommendations": recommendations, "removed": 0}
    completed_rows = await brain_collection.find(
        {
            "entity_type": "work_item",
            "external_id": {"$in": task_keys},
        }
    ).to_list(length=None)
    completed_keys = {
        str(row.get("external_id") or "").strip()
        for row in completed_rows
        if str(row.get("external_id") or "").strip() and _is_task_completed(row)
    }
    if not completed_keys:
        return {"recommendations": recommendations, "removed": 0}

    for task_key in completed_keys:
        delete_task_recommendation(project_id, task_key)

    filtered = [
        item for item in recommendations
        if str(item.get("taskKey") or "").strip() not in completed_keys
    ]
    return {"recommendations": filtered, "removed": len(completed_keys)}


def mark_project_task_dirty(project_id: str, task_key: str) -> bool:
    """Mark task as dirty for coalesced incremental recompute."""
    return mark_task_dirty(project_id, task_key)


async def generate_project_recommendations(
    project_id: str,
    *,
    trigger: str = "scheduler",
) -> Dict[str, Any]:
    """
    Run full recommendation generation for one project.
    project_id must be custom project_id (proj-XXXX-YYYY).
    """
    custom_project_id = str(project_id or "").strip()
    if not custom_project_id:
        return {"skipped": True, "reason": "missing_project_id"}

    brain_collection = _safe_get_brain_collection(custom_project_id)
    if brain_collection is None:
        return {
            "project_id": custom_project_id,
            "mode": "full",
            "count": 0,
            "skipped": True,
            "reason": "brain_collection_unavailable",
            "trigger": str(trigger or "").strip() or "scheduler",
        }
    tasks = await brain_collection.find({"entity_type": "work_item"}).to_list(length=None)
    runtime_config = await _resolve_project_runtime_config(custom_project_id)
    thresholds = runtime_config.get("thresholds") if isinstance(runtime_config, dict) else {}
    project_timezone = (runtime_config.get("project_timezone") if isinstance(runtime_config, dict) else None) or None
    workflow_capabilities = (
        runtime_config.get("workflow_capabilities") if isinstance(runtime_config, dict) else {}
    ) or {}
    status_category_map = (
        runtime_config.get("status_category_map") if isinstance(runtime_config, dict) else {}
    ) or {}
    health_payload = _build_health_payload(
        tasks,
        project_timezone=project_timezone,
        workflow_capabilities=workflow_capabilities if isinstance(workflow_capabilities, dict) else {},
        status_category_map=status_category_map if isinstance(status_category_map, dict) else {},
    )
    text_signals = await _TEXT_EXTRACTOR.extract_signals(tasks, project_id=custom_project_id)
    recommendations = _sort_recommendations(
        build_recommendations_from_health(
            health_payload if isinstance(health_payload, dict) else {},
            text_signals=text_signals,
            thresholds=thresholds,
        )
    )
    prune_stats = await _prune_completed_recommendations_from_list(
        custom_project_id,
        recommendations,
    )
    recommendations = _sort_recommendations(
        prune_stats.get("recommendations") if isinstance(prune_stats, dict) else recommendations
    )
    task_by_key = {
        str(task.get("external_id") or "").strip(): task
        for task in tasks
        if str(task.get("external_id") or "").strip()
    }

    for rec in recommendations:
        task_key = str(rec.get("taskKey") or "").strip()
        if not task_key:
            continue
        set_task_recommendation(
            custom_project_id,
            task_key,
            _task_runtime_payload(
                custom_project_id,
                rec,
                task_row=task_by_key.get(task_key),
            ),
        )

    project_payload = {
        "project_id": custom_project_id,
        "generated_at": datetime.utcnow().isoformat(),
        "trigger": str(trigger or "").strip() or "scheduler",
        "mode": "full",
        "version": "v1",
        "count": len(recommendations),
        "recommendations": recommendations,
    }
    await _persist_project_payload(
        custom_project_id,
        project_payload,
        trigger=str(trigger or "").strip() or "scheduler",
        mode="full",
    )
    return {
        "project_id": custom_project_id,
        "mode": "full",
        "count": len(recommendations),
        "completed_removed": int((prune_stats or {}).get("removed") or 0),
        "trigger": str(trigger or "").strip() or "scheduler",
    }


async def recompute_dirty_recommendations(
    project_id: str,
    *,
    trigger: str = "event",
    limit: int = 200,
) -> Dict[str, Any]:
    """
    Recompute recommendations for dirty tasks only and merge into project payload.
    project_id must be custom project_id (proj-XXXX-YYYY).
    """
    custom_project_id = str(project_id or "").strip()
    if not custom_project_id:
        return {"skipped": True, "reason": "missing_project_id"}

    dirty_task_keys = list_dirty_task_keys(custom_project_id, limit=limit)
    if not dirty_task_keys:
        commitment_stats = await _reevaluate_commitment_misses(custom_project_id)
        cleanup_stats = await _cleanup_completed_recommendations(custom_project_id)
        return {
            "project_id": custom_project_id,
            "mode": "incremental",
            "skipped": True,
            "reason": "no_dirty_tasks",
            "commitment_missed_transitions": int(commitment_stats.get("transitioned") or 0),
            "commitment_met_transitions": int(commitment_stats.get("met") or 0),
            "completed_removed": int(cleanup_stats.get("removed") or 0),
            "remaining_count": int(cleanup_stats.get("remaining") or 0),
        }

    brain_collection = _safe_get_brain_collection(custom_project_id)
    if brain_collection is None:
        return {
            "project_id": custom_project_id,
            "mode": "incremental",
            "skipped": True,
            "reason": "brain_collection_unavailable",
        }
    dirty_tasks = await brain_collection.find(
        {"entity_type": "work_item", "external_id": {"$in": dirty_task_keys}}
    ).to_list(length=None)
    runtime_config = await _resolve_project_runtime_config(custom_project_id)
    thresholds = runtime_config.get("thresholds") if isinstance(runtime_config, dict) else {}
    project_timezone = (runtime_config.get("project_timezone") if isinstance(runtime_config, dict) else None) or None
    workflow_capabilities = (
        runtime_config.get("workflow_capabilities") if isinstance(runtime_config, dict) else {}
    ) or {}
    status_category_map = (
        runtime_config.get("status_category_map") if isinstance(runtime_config, dict) else {}
    ) or {}
    health_payload = _build_health_payload(
        dirty_tasks,
        project_timezone=project_timezone,
        workflow_capabilities=workflow_capabilities if isinstance(workflow_capabilities, dict) else {},
        status_category_map=status_category_map if isinstance(status_category_map, dict) else {},
    )
    text_signals = await _TEXT_EXTRACTOR.extract_signals(dirty_tasks, project_id=custom_project_id)
    dirty_recommendations = _sort_recommendations(
        build_recommendations_from_health(
            health_payload if isinstance(health_payload, dict) else {},
            text_signals=text_signals,
            thresholds=thresholds,
        )
    )
    dirty_task_by_key = {
        str(task.get("external_id") or "").strip(): task
        for task in dirty_tasks
        if str(task.get("external_id") or "").strip()
    }
    dirty_rec_by_task = {
        str(item.get("taskKey") or "").strip(): item
        for item in dirty_recommendations
        if str(item.get("taskKey") or "").strip()
    }

    for task_key in dirty_task_keys:
        rec = dirty_rec_by_task.get(task_key)
        if rec is None:
            delete_task_recommendation(custom_project_id, task_key)
            continue
        set_task_recommendation(
            custom_project_id,
            task_key,
            _task_runtime_payload(
                custom_project_id,
                rec,
                task_row=dirty_task_by_key.get(task_key),
            ),
        )

    existing_project_payload, payload_source = await _load_project_payload(custom_project_id)
    existing_list = existing_project_payload.get("recommendations")
    existing_recommendations: List[Dict[str, Any]] = (
        [item for item in existing_list if isinstance(item, dict)] if isinstance(existing_list, list) else []
    )
    if not existing_recommendations and payload_source == "none":
        # Preserve deterministic merge behavior when Redis runtime payload expired.
        await generate_project_recommendations(
            custom_project_id,
            trigger=f"{str(trigger or '').strip() or 'event'}_bootstrap_full",
        )
        existing_project_payload, _ = await _load_project_payload(custom_project_id)
        existing_list = existing_project_payload.get("recommendations")
        existing_recommendations = (
            [item for item in existing_list if isinstance(item, dict)] if isinstance(existing_list, list) else []
        )
    merged_base = [
        item for item in existing_recommendations
        if str(item.get("taskKey") or "").strip() not in set(dirty_task_keys)
    ]
    merged_recommendations = _sort_recommendations(merged_base + list(dirty_rec_by_task.values()))
    prune_stats = await _prune_completed_recommendations_from_list(
        custom_project_id,
        merged_recommendations,
    )
    merged_recommendations = _sort_recommendations(
        prune_stats.get("recommendations") if isinstance(prune_stats, dict) else merged_recommendations
    )

    project_payload = {
        "project_id": custom_project_id,
        "generated_at": datetime.utcnow().isoformat(),
        "trigger": str(trigger or "").strip() or "event",
        "mode": "incremental",
        "version": "v1",
        "count": len(merged_recommendations),
        "recommendations": merged_recommendations,
    }
    await _persist_project_payload(
        custom_project_id,
        project_payload,
        trigger=str(trigger or "").strip() or "event",
        mode="incremental",
    )
    cleared = clear_dirty_task_keys(custom_project_id, dirty_task_keys)
    return {
        "project_id": custom_project_id,
        "mode": "incremental",
        "dirty_seen": len(dirty_task_keys),
        "dirty_cleared": int(cleared or 0),
        "updated_count": len(dirty_rec_by_task),
        "completed_removed": int((prune_stats or {}).get("removed") or 0),
        "count": len(merged_recommendations),
        "trigger": str(trigger or "").strip() or "event",
    }
