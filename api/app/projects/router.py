"""Project API routes"""

import asyncio
import json
import os
from datetime import datetime, timezone
from time import perf_counter
import logging
from fastapi import APIRouter, HTTPException, status, Query, Depends, Response
from fastapi.encoders import jsonable_encoder
from typing import Any, Dict, List, Optional
from zoneinfo import available_timezones

from app.projects.schemas import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
    ProjectMember,
    InviteCreate,
    BulkInviteCreate,
    InviteAccept,
    InviteVerifyResponse,
    MemberRoleUpdate,
    ActiveProviderUpdate,
    CadenceScheduleResponse,
    CadenceScheduleUpdate,
    ActionItemDigestScheduleResponse,
    ActionItemDigestScheduleUpdate,
    RecommendationFeedbackRequest,
    RecommendationThresholdsUpdateRequest,
)
from app.projects.service import ProjectService
from app.projects.pm_board_cache import (
    clear_pm_board_summary_dirty,
    get_cached_pm_board_summary,
    invalidate_pm_board_summary_cache,
    is_pm_board_summary_dirty,
    mark_pm_board_summary_dirty,
    set_cached_pm_board_summary,
)
from app.projects.health_service import HealthService
from app.projects.recommendations.recommendation_engine import build_recommendations_from_health
from app.projects.recommendations.recommendation_thresholds import (
    resolve_project_recommendation_thresholds,
    resolve_project_recommendation_thresholds_with_sources,
)
from app.projects.recommendations.recommendation_telemetry import record_recommendation_event
from app.projects.recommendations.recommendation_runtime_cache import (
    get_cached_api_project_recommendations,
    invalidate_cached_api_project_recommendations,
    set_cached_api_project_recommendations,
)
from app.core.config import settings
from app.core.auth_deps import get_current_user
from app.core.authz import assert_project_access_any
from app.core.internal_auth import require_internal_service_auth
from app.core.queue_backends.dramatiq_backend import enqueue as dramatiq_enqueue
from app.core.queue_runtime import QUEUE_CORTEX_RUNS
from app.core.database import ensure_mongo_ready, get_brain_collection, get_database
from app.core.timezone import get_zoneinfo_or_utc
from app.core.redis_queue import get_redis_connection
from app.planner.planner_cache import (
    get_cached_planner_status,
    set_cached_planner_status,
)


async def _require_projects_authz(
    project_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
) -> dict:
    if project_id:
        db = get_database()
        await assert_project_access_any(project_id, str(current_user.get("user_id") or ""), db)
    return current_user


router = APIRouter(
    prefix="/api/projects",
    tags=["projects"],
    dependencies=[Depends(_require_projects_authz)],
)
_PM_BOARD_JOB_STATUS_PREFIX = "pm_board_summary_job:v1:"
_PM_BOARD_JOB_LOCK_PREFIX = "pm_board_summary_job_lock:v1:"
_PM_BOARD_SNAPSHOT_ENTITY_TYPE = "project_insights_snapshot"
_PM_BOARD_SNAPSHOT_EXTERNAL_ID = "project-insights:v1"
_PM_BOARD_JOB_STATUS_TTL_SECONDS = max(
    300,
    int(getattr(settings, "pm_board_summary_job_status_ttl_seconds", 1800) or 1800),
)
_PM_BOARD_JOB_LOCK_TTL_SECONDS = max(
    30,
    int(getattr(settings, "pm_board_summary_job_lock_ttl_seconds", 120) or 120),
)
_PM_BOARD_SUMMARY_METRICS = {
    "calls": 0,
    "errors": 0,
    "health_computed": 0,
    "redis_hits": 0,
    "redis_misses": 0,
    "latency_ms_samples": [],
}
logger = logging.getLogger(__name__)


async def _record_shown_events(
    *,
    project_id: str,
    custom_project_id: str,
    recommendations: list[dict],
    source: str,
) -> None:
    """Best-effort recommendation shown telemetry."""
    if not recommendations:
        return
    for rec in recommendations:
        if not isinstance(rec, dict):
            continue
        task_key = str(rec.get("taskKey") or "").strip()
        if not task_key:
            continue
        await record_recommendation_event(
            project_id=project_id,
            custom_project_id=custom_project_id,
            action="shown",
            task_key=task_key,
            recommendation_id=str(rec.get("id") or "").strip() or None,
            source=source,
            metadata={
                "severity": str(rec.get("severity") or "").strip().lower() or None,
                "bucket": str(rec.get("bucket") or "").strip().lower() or None,
            },
        )


async def _compute_project_health_live(custom_project_id: str) -> dict:
    """Compute project health directly from current work items in brain collection."""
    brain_collection = get_brain_collection(custom_project_id)
    tasks = await brain_collection.find({"entity_type": "work_item"}).to_list(length=None)
    now = datetime.utcnow()
    if not tasks:
        return {
            "calculated_at": now,
            "data_source": "computed",
            "health_type": "none",
            "message": "No tasks found for this project.",
            "summary": {"on_track": 0, "at_risk": 0, "critical": 0, "total_tasks": 0},
            "work_item_summary": {"total": 0, "completed": 0, "on_track": 0, "at_risk": 0, "critical": 0, "open_total": 0},
            "work_items": [],
        }

    return {
        "calculated_at": now,
        "data_source": "computed",
        **HealthService.calculate_health(tasks),
    }


def _apply_recommendations_to_health(
    health_payload: dict,
    recommendations: list[dict],
) -> dict:
    """
    Sync health_action with recommendation text for the same task key.
    Keeps PM Board and Pulse action text aligned by default.
    """
    if not isinstance(health_payload, dict):
        return health_payload
    if not isinstance(recommendations, list) or not recommendations:
        return health_payload

    rec_by_task: dict[str, str] = {}
    for rec in recommendations:
        task_key = str((rec or {}).get("taskKey") or "").strip()
        rec_text = str((rec or {}).get("recommendation") or "").strip()
        if task_key and rec_text and task_key not in rec_by_task:
            rec_by_task[task_key] = rec_text

    if not rec_by_task:
        return health_payload

    work_items = health_payload.get("work_items")
    if isinstance(work_items, list):
        for item in work_items:
            key = str((item or {}).get("task_key") or "").strip()
            if key in rec_by_task:
                item["health_action"] = rec_by_task[key]

    for epic in health_payload.get("epics") or []:
        for child in epic.get("stories") or []:
            key = str((child or {}).get("task_key") or "").strip()
            if key in rec_by_task:
                child["health_action"] = rec_by_task[key]

    for task in health_payload.get("tasks") or []:
        key = str((task or {}).get("task_key") or "").strip()
        if key in rec_by_task:
            task["health_action"] = rec_by_task[key]

    return health_payload


def _pm_board_job_status_key(custom_project_id: str) -> str:
    return f"{_PM_BOARD_JOB_STATUS_PREFIX}{custom_project_id}"


def _pm_board_job_lock_key(custom_project_id: str) -> str:
    return f"{_PM_BOARD_JOB_LOCK_PREFIX}{custom_project_id}"


def _read_pm_board_job_status(custom_project_id: str) -> Optional[Dict[str, Any]]:
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return None
    try:
        raw = redis_conn.get(_pm_board_job_status_key(custom_project_id))
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        payload = json.loads(str(raw))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _write_pm_board_job_status(custom_project_id: str, payload: Dict[str, Any]) -> None:
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    try:
        redis_conn.setex(
            _pm_board_job_status_key(custom_project_id),
            _PM_BOARD_JOB_STATUS_TTL_SECONDS,
            json.dumps(payload, default=str),
        )
    except Exception:
        return


def _set_pm_board_job_status(
    *,
    custom_project_id: str,
    status_value: str,
    trigger: str,
    job_id: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": status_value,
        "trigger": trigger,
        "updated_at": datetime.utcnow().isoformat(),
    }
    if job_id:
        payload["job_id"] = job_id
    if error:
        payload["error"] = error
    _write_pm_board_job_status(custom_project_id, payload)
    return payload


def _release_pm_board_job_lock(custom_project_id: str) -> None:
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    try:
        redis_conn.delete(_pm_board_job_lock_key(custom_project_id))
    except Exception:
        return


def _has_pm_board_job_lock(custom_project_id: str) -> bool:
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return False
    try:
        return bool(redis_conn.exists(_pm_board_job_lock_key(custom_project_id)))
    except Exception:
        return False


async def _get_pm_board_summary_snapshot_payload(custom_project_id: str) -> Optional[Dict[str, Any]]:
    normalized_project_id = str(custom_project_id or "").strip()
    if not normalized_project_id:
        return None
    try:
        brain_collection = get_brain_collection(normalized_project_id)
    except Exception:
        return None
    if brain_collection is None:
        return None
    doc = await brain_collection.find_one(
        {
            "entity_type": _PM_BOARD_SNAPSHOT_ENTITY_TYPE,
            "external_id": _PM_BOARD_SNAPSHOT_EXTERNAL_ID,
        },
        {"pm_board_summary": 1},
    )
    payload = (doc or {}).get("pm_board_summary") if isinstance(doc, dict) else None
    return payload if isinstance(payload, dict) else None


async def _upsert_pm_board_summary_snapshot_payload(
    custom_project_id: str,
    payload: Dict[str, Any],
) -> None:
    normalized_project_id = str(custom_project_id or "").strip()
    if not normalized_project_id or not isinstance(payload, dict):
        return
    try:
        brain_collection = get_brain_collection(normalized_project_id)
    except Exception:
        return
    if brain_collection is None:
        return
    now = datetime.utcnow()
    try:
        await brain_collection.update_one(
            {
                "entity_type": _PM_BOARD_SNAPSHOT_ENTITY_TYPE,
                "external_id": _PM_BOARD_SNAPSHOT_EXTERNAL_ID,
            },
            {
                "$set": {
                    "project_id": normalized_project_id,
                    "entity_type": _PM_BOARD_SNAPSHOT_ENTITY_TYPE,
                    "external_id": _PM_BOARD_SNAPSHOT_EXTERNAL_ID,
                    "title": "Project Insights Snapshot",
                    "updated_at": now,
                    "pm_board_summary": jsonable_encoder(payload),
                },
                "$setOnInsert": {
                    "source": "pm_board_summary",
                    "created_at": now,
                },
            },
            upsert=True,
        )
    except Exception:
        return


def _enqueue_pm_board_summary_job(
    *,
    project_id: str,
    custom_project_id: str,
    trigger: str,
) -> Dict[str, Any]:
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return {"accepted": False, "reason": "queue_unavailable"}

    normalized_trigger = str(trigger or "").strip().lower() or "async"
    lock_key = _pm_board_job_lock_key(custom_project_id)
    try:
        claimed = redis_conn.set(lock_key, "1", nx=True, ex=_PM_BOARD_JOB_LOCK_TTL_SECONDS)
    except Exception:
        claimed = None
    if not claimed:
        existing = _read_pm_board_job_status(custom_project_id) or {}
        return {"accepted": True, "reason": "already_processing", "status": existing}

    try:
        enqueue_result = dramatiq_enqueue(
            queue_name=QUEUE_CORTEX_RUNS,
            actor_name="process_pm_board_summary_job",
            kwargs={
                "project_id": project_id,
                "custom_project_id": custom_project_id,
                "trigger": normalized_trigger,
            },
        )
        if not bool(enqueue_result.get("accepted")):
            raise RuntimeError(str(enqueue_result.get("reason") or "dramatiq_enqueue_failed"))
        job_id = str(enqueue_result.get("job_id") or "")
        status_payload = _set_pm_board_job_status(
            custom_project_id=custom_project_id,
            status_value="queued",
            trigger=normalized_trigger,
            job_id=job_id,
        )
        return {
            "accepted": True,
            "reason": "queued",
            "job_id": job_id,
            "status": status_payload,
        }
    except Exception as exc:
        _release_pm_board_job_lock(custom_project_id)
        status_payload = _set_pm_board_job_status(
            custom_project_id=custom_project_id,
            status_value="failed",
            trigger=normalized_trigger,
            error=str(exc),
        )
        return {
            "accepted": False,
            "reason": "enqueue_failed",
            "error": str(exc),
            "status": status_payload,
        }


def trigger_pm_board_summary_recompute(
    *,
    project_id: str,
    custom_project_id: str,
    trigger: str,
) -> Dict[str, Any]:
    """
    Provider/integration mutation hook: mark PM summary stale and request async recompute.
    """
    normalized_trigger = str(trigger or "").strip().lower() or "unknown"
    mark_pm_board_summary_dirty(
        custom_project_id,
        reason="mutation",
        source=normalized_trigger,
    )
    return _enqueue_pm_board_summary_job(
        project_id=project_id,
        custom_project_id=custom_project_id,
        trigger=normalized_trigger,
    )


async def _safe_summary_call(
    *,
    project_id: str,
    label: str,
    default: Any,
    coro,
    timeout_seconds: float = 20.0,
) -> Any:
    started_at = perf_counter()
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except Exception as exc:
        logger.warning(
            "[pm_board_summary] component_fallback project_id=%s component=%s latency_ms=%s error=%s",
            project_id,
            label,
            int((perf_counter() - started_at) * 1000),
            str(exc),
        )
        return default


async def _count_open_action_items(custom_project_id: str) -> int:
    """Count all open action items for PM Board summary."""
    collection = get_brain_collection(custom_project_id)
    return int(
        await collection.count_documents(
            {
                "entity_type": "action_item",
                "status": "open",
            }
        )
    )


async def _compute_pm_board_summary_payload(
    *,
    project_id: str,
    custom_project_id: str,
    project: Dict[str, Any],
) -> Dict[str, Any]:
    from app.planner.service import planner_service

    async def _safe_charter_status():
        cached = get_cached_planner_status(custom_project_id)
        if isinstance(cached, dict):
            return cached
        try:
            value = await planner_service.get_planner_status(custom_project_id)
            if isinstance(value, dict):
                set_cached_planner_status(custom_project_id, value)
            return value
        except Exception:
            return None

    (
        overdue_tasks,
        tasks_today,
        tasks_tomorrow,
        bottlenecks,
        tasks_due_24h,
        tomorrow_availability,
        unassigned_urgent,
        silent_tasks,
        stale_reviews,
        action_item_open_count,
        health_payload,
        members,
        orphan_assignees,
        charter_status,
        burndown,
        velocity,
    ) = await asyncio.gather(
        _safe_summary_call(
            project_id=project_id,
            label="overdue_tasks",
            default=[],
            coro=ProjectService.get_overdue_tasks(project_id),
        ),
        _safe_summary_call(
            project_id=project_id,
            label="tasks_due_today",
            default=[],
            coro=ProjectService.get_tasks_due_today(project_id),
        ),
        _safe_summary_call(
            project_id=project_id,
            label="tasks_due_tomorrow",
            default=[],
            coro=ProjectService.get_tasks_due_tomorrow(project_id),
        ),
        _safe_summary_call(
            project_id=project_id,
            label="bottlenecks",
            default=[],
            coro=ProjectService.get_bottlenecks(project_id),
        ),
        _safe_summary_call(
            project_id=project_id,
            label="tasks_due_24h",
            default=[],
            coro=ProjectService.get_tasks_due_24h(project_id),
        ),
        _safe_summary_call(
            project_id=project_id,
            label="tomorrow_availability",
            default=[],
            coro=ProjectService.get_tomorrow_availability(project_id),
        ),
        _safe_summary_call(
            project_id=project_id,
            label="unassigned_urgent",
            default=[],
            coro=ProjectService.get_unassigned_urgent(project_id),
        ),
        _safe_summary_call(
            project_id=project_id,
            label="silent_tasks",
            default=[],
            coro=ProjectService.get_silent_tasks(project_id),
        ),
        _safe_summary_call(
            project_id=project_id,
            label="stale_reviews",
            default=[],
            coro=ProjectService.get_stale_reviews(project_id),
        ),
        _safe_summary_call(
            project_id=project_id,
            label="action_item_open_count",
            default=0,
            coro=_count_open_action_items(custom_project_id),
        ),
        _safe_summary_call(
            project_id=project_id,
            label="health",
            default={},
            coro=_compute_project_health_live(custom_project_id),
            timeout_seconds=25.0,
        ),
        _safe_summary_call(
            project_id=project_id,
            label="members",
            default=[],
            coro=ProjectService.get_members(project_id),
        ),
        _safe_summary_call(
            project_id=project_id,
            label="orphan_assignees",
            default=[],
            coro=ProjectService.get_orphan_assignees(project_id),
        ),
        _safe_summary_call(
            project_id=project_id,
            label="charter_status",
            default=None,
            coro=_safe_charter_status(),
            timeout_seconds=12.0,
        ),
        _safe_summary_call(
            project_id=project_id,
            label="burndown",
            default=None,
            coro=ProjectService.get_burndown_data(project_id),
        ),
        _safe_summary_call(
            project_id=project_id,
            label="velocity",
            default=None,
            coro=ProjectService.get_velocity_trend(project_id),
        ),
    )

    pending_invites = [
        {
            "email": m.get("email"),
            "name": m.get("name") or (m.get("email") or "").split("@")[0] or "Unknown",
            "invited_at": m.get("invited_at"),
            "expires_at": m.get("expires_at"),
            "invited_by": m.get("invited_by"),
            "reminder_count": m.get("reminder_count", 0),
            "last_reminded_at": m.get("last_reminded_at"),
        }
        for m in members
        if m.get("status") == "pending"
    ]
    pending_jira_ids = {
        m.get("integration_ids", {}).get("jira_account_id")
        for m in members
        if m.get("status") == "pending" and m.get("integration_ids", {}).get("jira_account_id")
    }
    filtered_orphans = [
        orphan for orphan in orphan_assignees
        if orphan.get("account_id") not in pending_jira_ids
    ]

    _PM_BOARD_SUMMARY_METRICS["health_computed"] += 1
    thresholds = resolve_project_recommendation_thresholds(project or {})
    project_timezone = str(project.get("timezone") or "").strip()
    workflow_capabilities = project.get("workflow_capabilities")
    status_category_map = (
        project.get("recommendation_status_category_map")
        if isinstance(project.get("recommendation_status_category_map"), dict)
        else project.get("status_category_map")
    )

    health_for_recommendations = dict(health_payload) if isinstance(health_payload, dict) else {}
    if project_timezone:
        health_for_recommendations["project_timezone"] = project_timezone
    if isinstance(workflow_capabilities, dict) and workflow_capabilities:
        health_for_recommendations["workflow_capabilities"] = workflow_capabilities
    if isinstance(status_category_map, dict) and status_category_map:
        health_for_recommendations["status_category_map"] = status_category_map

    recommendations = build_recommendations_from_health(
        health_for_recommendations,
        thresholds=thresholds,
    )
    health_payload = _apply_recommendations_to_health(
        health_payload if isinstance(health_payload, dict) else {},
        recommendations,
    )

    return {
        "project_id": project_id,
        "custom_project_id": custom_project_id,
        "health": health_payload or {
            "health_type": "none",
            "summary": {"on_track": 0, "at_risk": 0, "critical": 0, "total_tasks": 0},
            "work_item_summary": {"total": 0, "completed": 0, "on_track": 0, "at_risk": 0, "critical": 0, "open_total": 0},
            "work_items": [],
            "calculated_at": None,
        },
        "tasks": {
            "overdue": overdue_tasks,
            "today": tasks_today,
            "tomorrow": tasks_tomorrow,
            "bottlenecks": bottlenecks,
            "due_24h": tasks_due_24h,
            "tomorrow_availability": tomorrow_availability,
            "unassigned_urgent": unassigned_urgent,
            "silent": silent_tasks,
            "stale_reviews": stale_reviews,
        },
        "review": {
            "members": members,
            "pending_invites": pending_invites,
            "orphan_assignees": filtered_orphans,
        },
        "action_items": {
            "open_count": int(action_item_open_count or 0),
        },
        "charter_status": charter_status,
        "forecast": {
            "burndown": burndown,
            "velocity": velocity,
        },
        "recommendations": recommendations,
    }


async def _process_pm_board_summary_job_async(project_id: str, trigger: str = "async", custom_project_id: str = "") -> None:
    normalized_trigger = str(trigger or "").strip().lower() or "async"
    custom_project_id = str(custom_project_id or "").strip()

    try:
        runtime_owner = f"dramatiq_event_loop_thread:{os.getpid()}"
        await ensure_mongo_ready(runtime_owner=runtime_owner)

        project = await ProjectService.get_by_id(project_id)
        if not project:
            raise RuntimeError("Project not found while processing PM summary job")
        custom_project_id = str(project.get("project_id") or "").strip()
        if not custom_project_id:
            raise RuntimeError("Project custom project_id missing while processing PM summary job")

        _set_pm_board_job_status(
            custom_project_id=custom_project_id,
            status_value="processing",
            trigger=normalized_trigger,
        )
        payload = await _compute_pm_board_summary_payload(
            project_id=project_id,
            custom_project_id=custom_project_id,
            project=project,
        )
        set_cached_pm_board_summary(custom_project_id, payload)
        await _upsert_pm_board_summary_snapshot_payload(custom_project_id, payload)
        clear_pm_board_summary_dirty(custom_project_id)
        _set_pm_board_job_status(
            custom_project_id=custom_project_id,
            status_value="ready",
            trigger=normalized_trigger,
        )
    except Exception as exc:
        logger.exception(
            "[pm_board_summary] async_job_failed project_id=%s trigger=%s error=%s",
            project_id,
            normalized_trigger,
            str(exc),
        )
        if custom_project_id:
            _set_pm_board_job_status(
                custom_project_id=custom_project_id,
                status_value="failed",
                trigger=normalized_trigger,
                error=str(exc),
            )
    finally:
        if custom_project_id:
            _release_pm_board_job_lock(custom_project_id)


def process_pm_board_summary_job(project_id: str, trigger: str = "async", custom_project_id: str = "") -> None:
    """RQ worker entrypoint for PM summary recompute."""
    asyncio.run(_process_pm_board_summary_job_async(project_id=project_id, trigger=trigger, custom_project_id=custom_project_id))


@router.get("/{project_id}/pm-board-summary")
async def get_pm_board_summary(
    project_id: str,
    response: Response,
    trigger: str = Query(default="unknown"),
):
    """
    Composed PM Board summary endpoint to reduce frontend fan-out calls.
    project_id is Mongo ObjectId string.
    """
    started_at = perf_counter()
    _PM_BOARD_SUMMARY_METRICS["calls"] += 1
    try:
        normalized_trigger = str(trigger or "").strip().lower()
        project = await ProjectService.get_by_id(project_id)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

        custom_project_id = str(project.get("project_id") or "").strip()
        if not custom_project_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Project is missing custom project_id"
            )
        bypass_cache = normalized_trigger in {"manual_refresh"}
        cached_payload = get_cached_pm_board_summary(custom_project_id)
        if isinstance(cached_payload, dict) and not bypass_cache:
            live_open_count = await _safe_summary_call(
                project_id=project_id,
                label="action_item_open_count_live_cached",
                default=int(((cached_payload.get("action_items") or {}).get("open_count") or 0)),
                coro=_count_open_action_items(custom_project_id),
                timeout_seconds=5.0,
            )
            cached_payload = dict(cached_payload)
            cached_action_items = dict(cached_payload.get("action_items") or {})
            cached_action_items["open_count"] = int(live_open_count or 0)
            cached_payload["action_items"] = cached_action_items
            _PM_BOARD_SUMMARY_METRICS["redis_hits"] += 1
            response.headers["Cache-Control"] = "private, max-age=30, stale-while-revalidate=30"
            latency_ms = int((perf_counter() - started_at) * 1000)
            _PM_BOARD_SUMMARY_METRICS["latency_ms_samples"].append(latency_ms)
            if len(_PM_BOARD_SUMMARY_METRICS["latency_ms_samples"]) > 1000:
                _PM_BOARD_SUMMARY_METRICS["latency_ms_samples"] = _PM_BOARD_SUMMARY_METRICS["latency_ms_samples"][-1000:]
            logger.info(
                "[pm_board_summary] project_id=%s trigger=%s latency_ms=%s cache_source=redis_hit calls=%s errors=%s",
                project_id,
                normalized_trigger or "unknown",
                latency_ms,
                _PM_BOARD_SUMMARY_METRICS["calls"],
                _PM_BOARD_SUMMARY_METRICS["errors"],
            )
            recommendations = cached_payload.get("recommendations") if isinstance(cached_payload, dict) else []
            await _record_shown_events(
                project_id=project_id,
                custom_project_id=custom_project_id,
                recommendations=recommendations if isinstance(recommendations, list) else [],
                source="pm_board_summary_cache",
            )
            return cached_payload

        if not bypass_cache:
            if is_pm_board_summary_dirty(custom_project_id):
                logger.info(
                    "[pm_board_summary] project_id=%s trigger=%s cache_source=snapshot_bypass_dirty",
                    project_id,
                    normalized_trigger or "unknown",
                )
            else:
                snapshot_payload = await _get_pm_board_summary_snapshot_payload(custom_project_id)
                if isinstance(snapshot_payload, dict):
                    live_open_count = await _safe_summary_call(
                        project_id=project_id,
                        label="action_item_open_count_live_snapshot",
                        default=int(((snapshot_payload.get("action_items") or {}).get("open_count") or 0)),
                        coro=_count_open_action_items(custom_project_id),
                        timeout_seconds=5.0,
                    )
                    snapshot_payload = dict(snapshot_payload)
                    snapshot_action_items = dict(snapshot_payload.get("action_items") or {})
                    snapshot_action_items["open_count"] = int(live_open_count or 0)
                    snapshot_payload["action_items"] = snapshot_action_items
                    set_cached_pm_board_summary(custom_project_id, snapshot_payload)
                    latency_ms = int((perf_counter() - started_at) * 1000)
                    _PM_BOARD_SUMMARY_METRICS["latency_ms_samples"].append(latency_ms)
                    if len(_PM_BOARD_SUMMARY_METRICS["latency_ms_samples"]) > 1000:
                        _PM_BOARD_SUMMARY_METRICS["latency_ms_samples"] = _PM_BOARD_SUMMARY_METRICS["latency_ms_samples"][-1000:]
                    response.headers["Cache-Control"] = "private, max-age=30, stale-while-revalidate=30"
                    recommendations = snapshot_payload.get("recommendations") if isinstance(snapshot_payload, dict) else []
                    await _record_shown_events(
                        project_id=project_id,
                        custom_project_id=custom_project_id,
                        recommendations=recommendations if isinstance(recommendations, list) else [],
                        source="pm_board_summary_snapshot",
                    )
                    logger.info(
                        "[pm_board_summary] project_id=%s trigger=%s latency_ms=%s cache_source=snapshot_hit calls=%s errors=%s",
                        project_id,
                        normalized_trigger or "unknown",
                        latency_ms,
                        _PM_BOARD_SUMMARY_METRICS["calls"],
                        _PM_BOARD_SUMMARY_METRICS["errors"],
                    )
                    return snapshot_payload

        _PM_BOARD_SUMMARY_METRICS["redis_misses"] += 1
        response.headers["Cache-Control"] = "no-store"

        enqueue_result = _enqueue_pm_board_summary_job(
            project_id=project_id,
            custom_project_id=custom_project_id,
            trigger=normalized_trigger or "unknown",
        )
        if enqueue_result.get("accepted"):
            response.headers["Retry-After"] = "1"
            response.status_code = status.HTTP_202_ACCEPTED
            return {
                "status": "processing",
                "project_id": project_id,
                "custom_project_id": custom_project_id,
                "job_id": enqueue_result.get("job_id"),
                "reason": enqueue_result.get("reason"),
                "job_status": enqueue_result.get("status"),
                "retry_after_ms": 1000,
            }

        # Fallback path when queue/redis is unavailable: compute synchronously.
        response_payload = await _compute_pm_board_summary_payload(
            project_id=project_id,
            custom_project_id=custom_project_id,
            project=project,
        )
        set_cached_pm_board_summary(custom_project_id, response_payload)
        await _upsert_pm_board_summary_snapshot_payload(custom_project_id, response_payload)
        clear_pm_board_summary_dirty(custom_project_id)
        recommendations = response_payload.get("recommendations")
        await _record_shown_events(
            project_id=project_id,
            custom_project_id=custom_project_id,
            recommendations=recommendations if isinstance(recommendations, list) else [],
            source="pm_board_summary_sync_fallback",
        )
        latency_ms = int((perf_counter() - started_at) * 1000)
        _PM_BOARD_SUMMARY_METRICS["latency_ms_samples"].append(latency_ms)
        if len(_PM_BOARD_SUMMARY_METRICS["latency_ms_samples"]) > 1000:
            _PM_BOARD_SUMMARY_METRICS["latency_ms_samples"] = _PM_BOARD_SUMMARY_METRICS["latency_ms_samples"][-1000:]
        logger.info(
            "[pm_board_summary] project_id=%s trigger=%s latency_ms=%s cache_source=%s calls=%s errors=%s",
            project_id,
            normalized_trigger or "unknown",
            latency_ms,
            "computed_sync_fallback",
            _PM_BOARD_SUMMARY_METRICS["calls"],
            _PM_BOARD_SUMMARY_METRICS["errors"],
        )
        return response_payload
    except HTTPException:
        _PM_BOARD_SUMMARY_METRICS["errors"] += 1
        raise
    except Exception as e:
        _PM_BOARD_SUMMARY_METRICS["errors"] += 1
        latency_ms = int((perf_counter() - started_at) * 1000)
        logger.exception(
            "[pm_board_summary] failed project_id=%s trigger=%s latency_ms=%s error=%s",
            project_id,
            trigger,
            latency_ms,
            str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get PM Board summary: {str(e)}"
        )


@router.get("/{project_id}/pm-board-summary-status")
async def get_pm_board_summary_status(project_id: str):
    """
    Lightweight status endpoint for async PM summary generation.
    """
    project = await ProjectService.get_by_id(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    custom_project_id = str(project.get("project_id") or "").strip()
    if not custom_project_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Project is missing custom project_id",
        )

    cached_payload = get_cached_pm_board_summary(custom_project_id)
    cached_ready = isinstance(cached_payload, dict)
    job_status = _read_pm_board_job_status(custom_project_id) or {}
    status_value = str(job_status.get("status") or ("ready" if cached_ready else "idle")).strip().lower()
    has_lock = _has_pm_board_job_lock(custom_project_id)
    if status_value == "queued" and not cached_ready and not has_lock:
        status_value = "failed"
        _set_pm_board_job_status(
            custom_project_id=custom_project_id,
            status_value="failed",
            trigger=str(job_status.get("trigger") or "status_probe"),
            error="stale_queued_without_lock",
        )
    elif status_value == "processing" and not cached_ready and not has_lock:
        status_value = "failed"
        _set_pm_board_job_status(
            custom_project_id=custom_project_id,
            status_value="failed",
            trigger=str(job_status.get("trigger") or "status_probe"),
            error="stale_processing_without_lock",
        )

    retry_after_ms = 0
    if status_value in {"queued", "processing"}:
        retry_after_ms = 1000
    elif status_value == "failed":
        retry_after_ms = 5000

    return {
        "project_id": project_id,
        "custom_project_id": custom_project_id,
        "status": status_value,
        "has_data": cached_ready,
        "updated_at": job_status.get("updated_at"),
        "error": job_status.get("error"),
        "job_id": job_status.get("job_id"),
        "retry_after_ms": retry_after_ms,
    }


@router.get("/ops/pm-board-summary-metrics")
async def get_pm_board_summary_metrics(
    _internal_ok: None = Depends(require_internal_service_auth),
):
    """
    Lightweight in-process PM Board summary metrics for observability.
    Note: process-local metrics; use centralized log/metrics pipeline for multi-instance aggregation.
    """
    samples = list(_PM_BOARD_SUMMARY_METRICS.get("latency_ms_samples", []))
    samples.sort()
    count = len(samples)

    def _percentile(values: List[int], p: float) -> Optional[int]:
        if not values:
            return None
        if len(values) == 1:
            return values[0]
        rank = max(0, min(len(values) - 1, int(round((p / 100.0) * (len(values) - 1)))))
        return values[rank]

    calls = int(_PM_BOARD_SUMMARY_METRICS.get("calls", 0) or 0)
    errors = int(_PM_BOARD_SUMMARY_METRICS.get("errors", 0) or 0)
    health_computed = int(_PM_BOARD_SUMMARY_METRICS.get("health_computed", 0) or 0)
    redis_hits = int(_PM_BOARD_SUMMARY_METRICS.get("redis_hits", 0) or 0)
    redis_misses = int(_PM_BOARD_SUMMARY_METRICS.get("redis_misses", 0) or 0)

    error_ratio = round((errors / calls) * 100.0, 2) if calls > 0 else 0.0
    redis_hit_ratio = round((redis_hits / calls) * 100.0, 2) if calls > 0 else 0.0

    return {
        "calls": calls,
        "errors": errors,
        "error_ratio_pct": error_ratio,
        "health_computed": health_computed,
        "redis_hits": redis_hits,
        "redis_misses": redis_misses,
        "redis_hit_ratio_pct": redis_hit_ratio,
        "latency_samples_count": count,
        "latency_ms": {
            "p50": _percentile(samples, 50),
            "p95": _percentile(samples, 95),
            "p99": _percentile(samples, 99),
        },
    }


@router.get("/{project_id}/recommendations")
async def get_project_recommendations(
    project_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    bucket: Optional[str] = Query(default=None),
    include_supporting: bool = Query(default=True),
):
    """
    Supporting recommendations endpoint.
    PM/Pulse UI should continue to consume recommendations via /pm-board-summary.
    """
    project = await ProjectService.get_by_id(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    custom_project_id = str(project.get("project_id") or "").strip()
    if not custom_project_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Project is missing custom project_id",
        )

    normalized_bucket = str(bucket or "").strip().lower()
    if normalized_bucket and normalized_bucket not in {"active", "up_next"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="bucket must be one of: active, up_next",
        )

    cached_payload = get_cached_api_project_recommendations(custom_project_id)
    cached_recommendations = (cached_payload or {}).get("recommendations")
    if isinstance(cached_recommendations, list):
        recommendations = list(cached_recommendations)
    else:
        health_payload = await _compute_project_health_live(custom_project_id)
        thresholds = resolve_project_recommendation_thresholds(project)
        project_timezone = str(project.get("timezone") or "").strip()
        workflow_capabilities = project.get("workflow_capabilities")
        status_category_map = (
            project.get("recommendation_status_category_map")
            if isinstance(project.get("recommendation_status_category_map"), dict)
            else project.get("status_category_map")
        )
        health_for_recommendations = dict(health_payload) if isinstance(health_payload, dict) else {}
        if project_timezone:
            health_for_recommendations["project_timezone"] = project_timezone
        if isinstance(workflow_capabilities, dict) and workflow_capabilities:
            health_for_recommendations["workflow_capabilities"] = workflow_capabilities
        if isinstance(status_category_map, dict) and status_category_map:
            health_for_recommendations["status_category_map"] = status_category_map
        recommendations = build_recommendations_from_health(
            health_for_recommendations,
            thresholds=thresholds,
        )
        set_cached_api_project_recommendations(
            custom_project_id,
            {
                "project_id": project_id,
                "custom_project_id": custom_project_id,
                "recommendations": recommendations,
                "cached_at": datetime.utcnow().isoformat(),
            },
        )

    if normalized_bucket:
        recommendations = [
            rec for rec in recommendations
            if str(rec.get("bucket") or "").strip().lower() == normalized_bucket
        ]

    # Current engine emits primary work-item recommendations.
    # Keep the contract now for future supporting actions without breaking clients.
    if not include_supporting:
        recommendations = [
            rec for rec in recommendations
            if str(rec.get("category") or "").strip().lower() == "work_item"
        ]

    recommendations = recommendations[:limit]
    await _record_shown_events(
        project_id=project_id,
        custom_project_id=custom_project_id,
        recommendations=recommendations,
        source="recommendations_endpoint",
    )

    return {
        "project_id": project_id,
        "custom_project_id": custom_project_id,
        "count": len(recommendations),
        "recommendations": recommendations,
    }


@router.post("/{project_id}/recommendations/feedback")
async def post_recommendation_feedback(
    project_id: str,
    payload: RecommendationFeedbackRequest,
):
    """Persist recommendation feedback events (accepted/dismissed/completed)."""
    project = await ProjectService.get_by_id(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    custom_project_id = str(project.get("project_id") or "").strip()
    if not custom_project_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Project is missing custom project_id",
        )

    success = await record_recommendation_event(
        project_id=project_id,
        custom_project_id=custom_project_id,
        action=payload.action,
        task_key=payload.task_key,
        recommendation_id=payload.recommendation_id,
        source=payload.source or "feedback_endpoint",
        actor_user_id=payload.actor_user_id,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist recommendation feedback",
        )

    return {
        "ok": True,
        "project_id": project_id,
        "custom_project_id": custom_project_id,
        "task_key": payload.task_key,
        "action": payload.action,
    }


@router.get("/{project_id}/recommendations/thresholds")
async def get_project_recommendation_thresholds(project_id: str):
    """Read effective recommendation thresholds for admin tuning UX."""
    project = await ProjectService.get_by_id(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    custom_project_id = str(project.get("project_id") or "").strip()
    if not custom_project_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Project is missing custom project_id",
        )

    threshold_state = resolve_project_recommendation_thresholds_with_sources(project)
    return {
        "project_id": project_id,
        "custom_project_id": custom_project_id,
        "thresholds": threshold_state.get("resolved") or {},
        "sources": threshold_state.get("sources") or {},
    }


@router.put("/{project_id}/recommendations/thresholds")
async def update_project_recommendation_thresholds(
    project_id: str,
    payload: RecommendationThresholdsUpdateRequest,
):
    """Update project-level recommendation threshold overrides for admin tuning UX."""
    existing_project = await ProjectService.get_by_id(project_id)
    if not existing_project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    custom_project_id = str(existing_project.get("project_id") or "").strip()
    if not custom_project_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Project is missing custom project_id",
        )

    update_override: dict[str, object] = {}
    if payload.overload_threshold is not None:
        update_override["overload_threshold"] = float(payload.overload_threshold)
    if payload.due_soon_hours is not None:
        update_override["due_soon_hours"] = int(payload.due_soon_hours)
    if payload.sprint_end_soon_hours is not None:
        update_override["sprint_end_soon_hours"] = int(payload.sprint_end_soon_hours)
    if payload.sprint_open_critical_threshold is not None:
        update_override["sprint_open_critical_threshold"] = int(payload.sprint_open_critical_threshold)

    if not payload.clear_overrides and not update_override:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one threshold field or set clear_overrides=true",
        )

    current_override = existing_project.get("recommendation_thresholds")
    next_override = (
        {}
        if payload.clear_overrides
        else {**(current_override if isinstance(current_override, dict) else {}), **update_override}
    )

    updated_project = await ProjectService.update(
        project_id,
        {"recommendation_thresholds": next_override},
    )
    if not updated_project:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update recommendation thresholds",
        )

    invalidate_cached_api_project_recommendations(custom_project_id)
    invalidate_pm_board_summary_cache(custom_project_id)

    threshold_state = resolve_project_recommendation_thresholds_with_sources(updated_project)
    return {
        "ok": True,
        "project_id": project_id,
        "custom_project_id": custom_project_id,
        "thresholds": threshold_state.get("resolved") or {},
        "sources": threshold_state.get("sources") or {},
    }


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(project: ProjectCreate):
    """Create a new project"""
    # Verify user exists
    if not await ProjectService.user_exists(project.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    created_project = await ProjectService.create(project.model_dump())
    return ProjectResponse(**created_project)


@router.get("/timezones", response_model=List[str])
async def list_supported_timezones():
    """Return all supported IANA timezone identifiers."""
    try:
        zones = sorted(str(tz).strip() for tz in available_timezones() if str(tz).strip())
        return zones
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list timezones: {exc}",
        )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str):
    """Get a project by ID"""
    project = await ProjectService.get_by_id(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )
    return ProjectResponse(**project)


@router.get("", response_model=List[ProjectResponse], include_in_schema=False)
@router.get("/", response_model=List[ProjectResponse])
async def list_projects(
    user_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: dict = Depends(get_current_user),
):
    """List projects for the authenticated user."""
    actor_user_id = str(current_user.get("user_id") or "").strip()
    requested_user_id = str(user_id or "").strip()
    if requested_user_id and requested_user_id != actor_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Actor identity mismatch between token and query param",
        )
    projects = await ProjectService.list_all(actor_user_id, skip, limit)
    return [ProjectResponse(**project) for project in projects]


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, project_update: ProjectUpdate):
    """Update a project"""
    # Check if project exists
    existing_project = await ProjectService.get_by_id(project_id)
    if not existing_project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    updated_project = await ProjectService.update(project_id, project_update.model_dump())
    return ProjectResponse(**updated_project)


@router.get("/{project_id}/cadence-schedule", response_model=CadenceScheduleResponse)
async def get_cadence_schedule(project_id: str):
    """Get scheduler-backed cadence reminder settings for a project."""
    try:
        schedule = await ProjectService.get_cadence_schedule(project_id)
        return CadenceScheduleResponse(**schedule)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch cadence schedule: {exc}",
        )


@router.put("/{project_id}/cadence-schedule", response_model=CadenceScheduleResponse)
async def update_cadence_schedule(project_id: str, payload: CadenceScheduleUpdate):
    """Update scheduler-backed cadence reminder settings for a project."""
    try:
        schedule = await ProjectService.update_cadence_schedule(
            project_id=project_id,
            enabled=payload.enabled,
            time=payload.time,
            skip_weekends=payload.skip_weekends,
            ignore_followup_for_owner=payload.ignore_followup_for_owner,
        )
        return CadenceScheduleResponse(**schedule)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update cadence schedule: {exc}",
        )


@router.get("/{project_id}/action-item-digest-schedule", response_model=ActionItemDigestScheduleResponse)
async def get_action_item_digest_schedule(project_id: str):
    """Get scheduler-backed action-item digest settings for a project."""
    try:
        schedule = await ProjectService.get_action_item_digest_schedule(project_id)
        return ActionItemDigestScheduleResponse(**schedule)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch action-item digest schedule: {exc}",
        )


@router.put("/{project_id}/action-item-digest-schedule", response_model=ActionItemDigestScheduleResponse)
async def update_action_item_digest_schedule(project_id: str, payload: ActionItemDigestScheduleUpdate):
    """Update scheduler-backed action-item digest settings for a project."""
    try:
        schedule = await ProjectService.update_action_item_digest_schedule(
            project_id=project_id,
            enabled=payload.enabled,
            time=payload.time,
            skip_weekends=payload.skip_weekends,
            ignore_followup_for_owner=payload.ignore_followup_for_owner,
        )
        return ActionItemDigestScheduleResponse(**schedule)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update action-item digest schedule: {exc}",
        )


@router.put("/{project_id}/active-provider")
@router.patch("/{project_id}/active-provider")
async def update_active_provider(project_id: str, body: ActiveProviderUpdate):
    """Update project active provider (owner/admin only)."""
    try:
        return await ProjectService.set_active_provider(
            project_id=project_id,
            user_id=body.user_id,
            active_provider=body.active_provider,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a project (owner only)."""
    result = await ProjectService.delete(
        project_id,
        actor_user_id=str(current_user.get("user_id") or "").strip(),
    )
    status_value = str((result or {}).get("status") or "").strip().lower()
    if status_value == "deleted":
        return None
    if status_value == "forbidden":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only project owner can delete project")
    if status_value == "delete_in_progress":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="delete_in_progress")
    if status_value == "cleanup_failed":
        detail = str((result or {}).get("detail") or "Project delete cleanup failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)
    if status_value == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Project delete failed")
    return None


# ==================== INVITE ENDPOINTS ====================

@router.post("/{project_id}/invite", status_code=status.HTTP_201_CREATED)
async def create_invite(project_id: str, invite: InviteCreate):
    """
    Send an invitation to join a project.
    Creates a pending member entry with an invite token.
    """
    try:
        result = await ProjectService.create_invite(
            project_id=project_id,
            email=invite.email,
            invited_by=invite.invited_by,
            role=invite.role,
            jira_account_id=invite.jira_account_id
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create invite: {str(e)}"
        )


@router.post("/{project_id}/invite/remind")
async def remind_invite(project_id: str, body: dict):
    """
    Resend invite email to a pending member.
    Refreshes the invite token and extends expiry.
    """
    email = body.get("email")
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="email is required")
    try:
        result = await ProjectService.remind_invite(project_id=project_id, email=email)
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resend invite: {str(e)}"
        )


@router.post("/{project_id}/invite/bulk", status_code=status.HTTP_201_CREATED)
async def create_bulk_invites(project_id: str, data: BulkInviteCreate):
    """
    Send multiple invitations at once.
    Returns list of successful and failed invites.
    """
    try:
        results = await ProjectService.create_bulk_invites(
            project_id=project_id,
            emails=data.emails,
            invited_by=data.invited_by,
            role=data.role
        )
        return results
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create invites: {str(e)}"
        )


@router.get("/invite/verify/{token}", response_model=InviteVerifyResponse)
async def verify_invite(token: str):
    """
    Verify an invite token and get invite details.
    Used for the invite acceptance page.
    Does not require authentication.
    """
    try:
        result = await ProjectService.verify_invite(token)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to verify invite: {str(e)}"
        )


@router.post("/invite/accept/{token}")
async def accept_invite(token: str, data: InviteAccept):
    """
    Accept an invitation.
    Updates the member entry with user info and sets status to active.
    """
    try:
        result = await ProjectService.accept_invite(
            token=token,
            user_id=data.user_id,
            name=data.name,
            email=data.email
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to accept invite: {str(e)}"
        )


@router.post("/invite/decline/{token}")
async def decline_invite(token: str):
    """Decline an invitation"""
    try:
        result = await ProjectService.decline_invite(token)
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to decline invite: {str(e)}"
        )


@router.post("/{project_id}/invite/resend")
async def resend_invite(
    project_id: str,
    email: str = Query(..., description="Email of the pending invite to resend")
):
    """
    Resend an invitation.
    Generates a new token and extends the expiry date.
    """
    try:
        result = await ProjectService.resend_invite(project_id, email)
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resend invite: {str(e)}"
        )


@router.delete("/{project_id}/invite/cancel")
async def cancel_invite(
    project_id: str,
    email: str = Query(..., description="Email of the pending invite to cancel")
):
    """Cancel a pending invitation"""
    try:
        await ProjectService.cancel_invite(project_id, email)
        return {"message": "Invite cancelled successfully"}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel invite: {str(e)}"
        )


# ==================== MEMBER ENDPOINTS ====================

@router.get("/{project_id}/members", response_model=List[ProjectMember])
async def get_project_members(
    project_id: str,
    status: Optional[str] = Query(None, description="Filter by status: active, pending, declined, removed")
):
    """Get all members of a project"""
    try:
        members = await ProjectService.get_members(project_id, status)
        return members
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get members: {str(e)}"
        )


@router.get("/{project_id}/orphan-assignees")
async def get_orphan_assignees(project_id: str):
    """
    Get task assignees who have tasks but are not project members.
    Useful for showing notifications to invite team members.
    """
    try:
        orphans = await ProjectService.get_orphan_assignees(project_id)
        return orphans
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get orphan assignees: {str(e)}"
        )


@router.put("/{project_id}/members/{user_id}/role")
async def update_member_role(project_id: str, user_id: str, data: MemberRoleUpdate, current_user: dict = Depends(get_current_user)):
    """Update a member's role"""
    try:
        result = await ProjectService.update_member_role(
            project_id,
            user_id,
            data.role,
            actor_user_id=str(current_user.get("user_id") or "").strip(),
        )
        return result
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update role: {str(e)}"
        )


@router.delete("/{project_id}/members/{user_id}")
async def remove_member(project_id: str, user_id: str, current_user: dict = Depends(get_current_user)):
    """Remove a member from the project"""
    try:
        await ProjectService.remove_member(
            project_id,
            user_id,
            actor_user_id=str(current_user.get("user_id") or "").strip(),
        )
        return {"message": "Member removed successfully"}
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to remove member: {str(e)}"
        )


@router.get("/{project_id}/team-workload")
async def get_team_workload(project_id: str):
    """
    Get team workload statistics.
    Returns task counts (completed, in_progress, todo) for each team member.
    """
    try:
        workload = await ProjectService.get_team_workload(project_id)
        return workload
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get team workload: {str(e)}"
        )


# ==================== SPRINT ENDPOINTS ====================

@router.get("/{project_id}/active-sprint")
async def get_active_sprint(project_id: str):
    """
    Get active sprint information for the project.
    Returns sprint name, start date, and end date if an active sprint exists.
    """
    try:
        from app.core.database import get_brain_collection

        brain_collection = get_brain_collection(project_id)

        # Find one task with active sprint
        task = await brain_collection.find_one({
            "entity_type": "work_item",
            "sprint_state": "active"
        })

        if not task:
            return None

        return {
            "name": task.get("sprint"),
            "startDate": task.get("sprint_start_date"),
            "endDate": task.get("sprint_end_date")
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get active sprint: {str(e)}"
        )


# ==================== ANALYTICS ENDPOINTS ====================

@router.get("/{project_id}/overdue-tasks")
async def get_overdue_tasks(project_id: str):
    """
    Get overdue tasks for a project.
    Returns tasks past their due date with severity levels.
    """
    try:
        overdue = await ProjectService.get_overdue_tasks(project_id)
        return overdue
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get overdue tasks: {str(e)}"
        )


@router.get("/{project_id}/bottlenecks")
async def get_bottlenecks(project_id: str):
    """
    Detect tasks stuck in progress for >3 days.
    Returns tasks that haven't been updated recently.
    """
    try:
        bottlenecks = await ProjectService.get_bottlenecks(project_id)
        return bottlenecks
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get bottlenecks: {str(e)}"
        )


@router.get("/{project_id}/tasks-due-24h")
async def get_tasks_due_24h(project_id: str):
    """Tasks due within the next 24 hours."""
    try:
        return await ProjectService.get_tasks_due_24h(project_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed: {str(e)}")


@router.get("/{project_id}/tomorrow-availability")
async def get_tomorrow_availability(project_id: str):
    """Members with no tasks due tomorrow."""
    try:
        return await ProjectService.get_tomorrow_availability(project_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed: {str(e)}")


@router.get("/{project_id}/unassigned-urgent")
async def get_unassigned_urgent(project_id: str):
    """High/critical priority tasks with no assignee."""
    try:
        return await ProjectService.get_unassigned_urgent(project_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed: {str(e)}")


@router.get("/{project_id}/tasks-due-today")
async def get_tasks_due_today(project_id: str):
    """Tasks due today."""
    try:
        return await ProjectService.get_tasks_due_today(project_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed: {str(e)}")


@router.get("/{project_id}/tasks-due-tomorrow")
async def get_tasks_due_tomorrow(project_id: str):
    """Tasks due tomorrow."""
    try:
        return await ProjectService.get_tasks_due_tomorrow(project_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed: {str(e)}")


@router.get("/{project_id}/silent-tasks")
async def get_silent_tasks(project_id: str):
    """Tasks not updated in 5+ days, still open."""
    try:
        return await ProjectService.get_silent_tasks(project_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed: {str(e)}")


@router.get("/{project_id}/stale-reviews")
async def get_stale_reviews(project_id: str):
    """Tasks stuck in review/QA status for 2+ days."""
    try:
        return await ProjectService.get_stale_reviews(project_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed: {str(e)}")



@router.get("/{project_id}/burndown")
async def get_burndown_data(project_id: str):
    """
    Get burndown chart data for current sprint.
    Returns daily actual vs ideal remaining tasks.
    """
    try:
        burndown = await ProjectService.get_burndown_data(project_id)
        return burndown
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get burndown data: {str(e)}"
        )


@router.get("/{project_id}/velocity-trend")
async def get_velocity_trend(project_id: str):
    """
    Get velocity trend for last 5 sprints.
    Returns historical velocity and trend analysis.
    """
    try:
        velocity = await ProjectService.get_velocity_trend(project_id)
        return velocity
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get velocity trend: {str(e)}"
        )


@router.get("/{project_id}/team-activity-heatmap")
async def get_team_activity_heatmap(project_id: str, week_start: str = None):
    """Get team activity heatmap showing status changes by day for a given week"""
    try:
        heatmap_data = await ProjectService.get_team_activity_heatmap(project_id, week_start)
        return heatmap_data
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get team activity heatmap: {str(e)}"
        )


# ==================== CADENCE SUMMARY ENDPOINTS ====================

@router.get("/{project_id}/cadence-summary")
async def get_cadence_summary(
    project_id: str,
    date: Optional[str] = Query(
        None,
        description="Date in YYYY-MM-DD format. Defaults to today.",
    ),
):
    """Retrieve the pre-computed daily cadence summary for a project.

    Returns the structured summary document with overall summary,
    PM attention points, member update widgets, and missed responses.
    No computation or LLM calls occur at query time.
    """
    from datetime import datetime as dt, timedelta as td
    from app.core.database import (
        get_brain_collection,
        ensure_project_cadence_summary_indexes,
    )
    from app.cadence.summary import DAILY_SUMMARY_ENTITY_TYPE

    # Validate project exists
    project = await ProjectService.get_by_id(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    custom_project_id = str(project.get("project_id") or "").strip()
    if not custom_project_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Project missing custom project identifier",
        )

    project_timezone = str(project.get("timezone") or "UTC").strip() or "UTC"
    project_tz = get_zoneinfo_or_utc(
        project_timezone,
        context="projects_cadence_summary_endpoint",
    )

    if date:
        try:
            requested_day = dt.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date format. Expected YYYY-MM-DD.",
            )
    else:
        now = dt.now(timezone.utc).astimezone(project_tz)
        requested_day = dt(now.year, now.month, now.day)

    requested_date = requested_day.strftime("%Y-%m-%d")

    await ensure_project_cadence_summary_indexes(custom_project_id)
    collection = get_brain_collection(custom_project_id)
    summary_doc = await collection.find_one(
        {
            "entity_type": DAILY_SUMMARY_ENTITY_TYPE,
            "date": requested_date,
        },
        {"_id": 0},
        sort=[("generated_at", -1)],
    )

    if summary_doc:
        return {
            "status": "ready",
            "date": requested_date,
            "summary": summary_doc,
        }

    # Check if there are active (non-completed) sessions for requested date
    from app.cadence.session_store import (
        ENTITY_TYPE as SESSION_ENTITY_TYPE,
        SOURCE as SESSION_SOURCE,
    )
    from app.sessions.repository import SessionRepository

    repo = SessionRepository()
    day_start_local = dt(
        requested_day.year,
        requested_day.month,
        requested_day.day,
        tzinfo=project_tz,
    )
    day_end_local = day_start_local + td(days=1)
    day_start = day_start_local.astimezone(timezone.utc).replace(tzinfo=None)
    day_end = day_end_local.astimezone(timezone.utc).replace(tzinfo=None)
    active_sessions = await repo.list_session_index(
        query={
            "entity_type": SESSION_ENTITY_TYPE,
            "source": SESSION_SOURCE,
            "project_id": custom_project_id,
            "status": {"$ne": "completed"},
            "created_at": {"$gte": day_start, "$lt": day_end},
        },
        limit=1,
    )

    if active_sessions:
        return {
            "status": "in_progress",
            "date": requested_date,
            "summary": None,
        }

    return {
        "status": "no_data",
        "date": requested_date,
        "summary": None,
    }


@router.get("/{project_id}/project-health")
async def get_project_health(project_id: str, force_refresh: bool = False, response: Response = None):
    """
    Get project health by live computation from current work items.
    force_refresh is accepted for backward compatibility and has no effect.
    """
    started_at = perf_counter()
    try:
        from app.core.database import get_database

        db = get_database()

        # Get project by custom project_id
        project = await db.projects.find_one({"project_id": project_id})

        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

        health_result = await _compute_project_health_live(project_id)

        if response is not None:
            response.headers["Cache-Control"] = "private, max-age=30, stale-while-revalidate=30"
        logger.info(
            "[project_health] project_id=%s force_refresh=%s latency_ms=%s data_source=%s",
            project_id,
            force_refresh,
            int((perf_counter() - started_at) * 1000),
            health_result.get("data_source"),
        )
        return {
            "project_id": project_id,
            "project_name": project.get("project_name"),
            **health_result
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.exception(
            "[project_health] failed project_id=%s force_refresh=%s latency_ms=%s error=%s",
            project_id,
            force_refresh,
            int((perf_counter() - started_at) * 1000),
            str(e),
        )
        print(f"[Project Health Error] {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get project health: {str(e)}"
        )
