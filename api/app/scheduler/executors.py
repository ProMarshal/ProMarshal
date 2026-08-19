"""Scheduler job executors."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.timezone import UTC_TZ_NAME, get_zoneinfo_or_utc

logger = logging.getLogger("app.scheduler.executors")


async def _project_exists(db: AsyncIOMotorDatabase, project_id: str) -> bool:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return False
    project = await db.projects.find_one({"project_id": normalized_project_id}, {"_id": 1})
    return bool(project)


async def _is_weekend_in_project_timezone(db: AsyncIOMotorDatabase, project_id: str) -> bool:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return False
    project = await db.projects.find_one({"project_id": normalized_project_id}, {"timezone": 1})
    tz_name = str((project or {}).get("timezone") or "UTC").strip() or "UTC"
    tz = get_zoneinfo_or_utc(tz_name, context="scheduler_weekend_policy")
    utc_tz = get_zoneinfo_or_utc(UTC_TZ_NAME, context="scheduler_weekend_policy_utc")
    now_local = datetime.utcnow().replace(tzinfo=utc_tz).astimezone(tz)
    return now_local.weekday() >= 5


async def execute_cadence_reminder(db: AsyncIOMotorDatabase, schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Run cadence reminder dispatch for a single project schedule."""
    from app.cadence.cron_lock import acquire_task_reminder_lock, release_task_reminder_lock
    from app.cadence.llm_service import initialize_llm_service
    from app.cadence.session_store import expire_stale_sessions
    from app.cadence.tier_router import route_project_by_tier
    from app.core.config import settings

    project_id = str(schedule.get("project_id") or "").strip()
    if not project_id:
        logger.warning("cadence_schedule_skip reason=missing_project_id")
        return {"skipped": True, "reason": "missing_project_id"}
    schedule_spec = dict(schedule.get("schedule_spec") or {})
    if bool(schedule_spec.get("skip_weekends", False)) and await _is_weekend_in_project_timezone(db, project_id):
        logger.info("cadence_schedule_skip reason=weekend_policy project_id=%s", project_id)
        return {"skipped": True, "reason": "weekend_policy"}

    project = await db.projects.find_one({"project_id": project_id})
    if not project:
        logger.warning("cadence_schedule_skip reason=project_not_found project_id=%s", project_id)
        return {"skipped": True, "reason": "project_not_found"}
    project["_cadence_schedule_spec"] = dict(schedule_spec or {})

    lock_token = None
    correlation_id = str(uuid.uuid4())
    try:
        lock_acquired, lock_token, _ = acquire_task_reminder_lock(owner_hint=f"scheduler:{correlation_id}")
        if not lock_acquired:
            return {"skipped": True, "reason": "cadence_lock_active"}

        if settings.groq_api_key and settings.groq_api_key != "your-groq-api-key-here":
            initialize_llm_service(settings.groq_api_key)

        await expire_stale_sessions(db)
        reminders_sent = await route_project_by_tier(db, project, correlation_id)
        return {"project_id": project_id, "reminders_sent": int(reminders_sent or 0)}
    finally:
        release_task_reminder_lock(lock_token)


async def execute_cadence_expiry(db: AsyncIOMotorDatabase, schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Run cadence timeout finalization independent of reminder cadence."""
    from app.cadence.session_store import expire_stale_sessions

    expired_count = await expire_stale_sessions(db)
    return {"expired_sessions": int(expired_count or 0)}


async def execute_sessions_cleanup(db: AsyncIOMotorDatabase, schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Run unified sessions cleanup."""
    from app.sessions.cleanup import run_unified_session_cleanup

    result = await run_unified_session_cleanup(db)
    return {
        "deleted": result.get("deleted", {}),
        "duration_ms": result.get("duration_ms"),
    }


async def execute_slack_hourly_reader(db: AsyncIOMotorDatabase, schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Run hourly Slack message reader."""
    from app.workers.slack_messages.hourly_reader import run_hourly_slack_reader

    stats = await run_hourly_slack_reader()
    return {"stats": stats}


async def execute_team_poll_cycle(db: AsyncIOMotorDatabase, schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Run team poll lifecycle cycle (deferred delivery, nudge, expiry, finalize)."""
    from app.team_poll.scheduler_hooks import execute_team_poll_cycle as _execute

    return await _execute(db, schedule)


async def execute_recommendation_full(db: AsyncIOMotorDatabase, schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Run full recommendation recompute for one project."""
    from app.projects.recommendations.recommendation_orchestrator import generate_project_recommendations

    project_id = str(schedule.get("project_id") or "").strip()
    if not project_id:
        return {"skipped": True, "reason": "missing_project_id"}
    if not await _project_exists(db, project_id):
        return {"skipped": True, "reason": "project_not_found"}
    return await generate_project_recommendations(project_id, trigger="scheduler_full")


async def execute_recommendation_incremental(db: AsyncIOMotorDatabase, schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Run incremental recommendation recompute for one project dirty set."""
    from app.core.config import settings
    from app.projects.recommendations.recommendation_orchestrator import recompute_dirty_recommendations

    project_id = str(schedule.get("project_id") or "").strip()
    if not project_id:
        return {"skipped": True, "reason": "missing_project_id"}
    if not await _project_exists(db, project_id):
        return {"skipped": True, "reason": "project_not_found"}
    batch_limit = max(1, int(getattr(settings, "recommendation_incremental_batch_limit", 200) or 200))
    return await recompute_dirty_recommendations(
        project_id,
        trigger="scheduler_incremental",
        limit=batch_limit,
    )


async def execute_action_item_digest(db: AsyncIOMotorDatabase, schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Run action-item digest reminder dispatch for one project."""
    from app.action_items.reminders import execute_action_item_digest as _execute

    project_id = str(schedule.get("project_id") or "").strip()
    if project_id:
        schedule_spec = dict(schedule.get("schedule_spec") or {})
        if bool(schedule_spec.get("skip_weekends", False)) and await _is_weekend_in_project_timezone(db, project_id):
            logger.info("action_item_digest_skip reason=weekend_policy project_id=%s", project_id)
            return {"skipped": True, "reason": "weekend_policy"}
    return await _execute(db, schedule)


async def execute_action_item_followup_escalation(
    db: AsyncIOMotorDatabase,
    schedule: Dict[str, Any],
) -> Dict[str, Any]:
    """Run unresolved follow-up escalation for one project."""
    from app.action_items.reminders import execute_followup_escalation as _execute

    return await _execute(db, schedule)


async def execute_schedule_runs_cleanup(db: AsyncIOMotorDatabase, schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Prune old schedule_runs history in bounded batches."""
    from app.core.config import settings
    from app.scheduler.repository import SchedulerRepository

    retention_days = max(1, int(getattr(settings, "schedule_runs_retention_days", 7) or 7))
    batch_limit = max(1, int(getattr(settings, "cleanup_job_batch_limit", 500) or 500))
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    repo = SchedulerRepository(db)
    stats = await repo.prune_schedule_runs_before(cutoff=cutoff, limit=batch_limit)
    return {
        "retention_days": retention_days,
        "cutoff": cutoff.isoformat(),
        "batch_limit": batch_limit,
        **stats,
    }


async def execute_channel_index_cleanup(db: AsyncIOMotorDatabase, schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Prune stale channel_index rows (phase-1: null/empty active_project_id only)."""
    from app.core.config import settings
    from app.scheduler.repository import SchedulerRepository

    retention_days = max(1, int(getattr(settings, "channel_index_retention_days", 30) or 30))
    batch_limit = max(1, int(getattr(settings, "cleanup_job_batch_limit", 500) or 500))
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    repo = SchedulerRepository(db)
    stats = await repo.prune_channel_index_before(
        cutoff=cutoff,
        limit=batch_limit,
        active_project_only_null=True,
    )
    return {
        "retention_days": retention_days,
        "cutoff": cutoff.isoformat(),
        "batch_limit": batch_limit,
        "phase": "phase1_null_active_project_only",
        **stats,
    }


async def execute_project_summary_cleanup(db: AsyncIOMotorDatabase, schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Prune project Brain summary artifacts by retention policy."""
    from app.core.config import settings
    from app.core.database import get_brain_collection

    if not bool(getattr(settings, "cleanup_summary_retention_enabled", True)):
        return {"skipped": True, "reason": "cleanup_summary_retention_disabled"}

    project_id = str(schedule.get("project_id") or "").strip()
    if not project_id:
        return {"skipped": True, "reason": "missing_project_id"}

    batch_limit = max(1, int(getattr(settings, "cleanup_job_batch_limit", 500) or 500))
    team_poll_retention_days = max(1, int(getattr(settings, "team_poll_result_retention_days", 30) or 30))
    cadence_retention_days = max(
        1, int(getattr(settings, "cadence_daily_summary_retention_days", 90) or 90)
    )
    now = datetime.utcnow()
    team_poll_cutoff = now - timedelta(days=team_poll_retention_days)
    cadence_cutoff = now - timedelta(days=cadence_retention_days)

    collection = get_brain_collection(project_id)

    team_poll_ids = [
        item.get("_id")
        for item in await collection.find(
            {
                "entity_type": "team_poll_result",
                "$or": [
                    {"generated_at": {"$lt": team_poll_cutoff}},
                    {"generated_at": {"$exists": False}, "created_at": {"$lt": team_poll_cutoff}},
                ],
            },
            {"_id": 1},
        ).sort("generated_at", 1).limit(batch_limit).to_list(length=batch_limit)
        if item.get("_id") is not None
    ]
    cadence_ids = [
        item.get("_id")
        for item in await collection.find(
            {
                "entity_type": "cadence_daily_summary",
                "$or": [
                    {"generated_at": {"$lt": cadence_cutoff}},
                    {"generated_at": {"$exists": False}, "created_at": {"$lt": cadence_cutoff}},
                ],
            },
            {"_id": 1},
        ).sort("generated_at", 1).limit(batch_limit).to_list(length=batch_limit)
        if item.get("_id") is not None
    ]

    team_poll_deleted = 0
    cadence_deleted = 0
    if team_poll_ids:
        result = await collection.delete_many({"_id": {"$in": team_poll_ids}})
        team_poll_deleted = int(result.deleted_count or 0)
    if cadence_ids:
        result = await collection.delete_many({"_id": {"$in": cadence_ids}})
        cadence_deleted = int(result.deleted_count or 0)

    return {
        "project_id": project_id,
        "batch_limit": batch_limit,
        "team_poll_result_retention_days": team_poll_retention_days,
        "cadence_daily_summary_retention_days": cadence_retention_days,
        "team_poll_result_cutoff": team_poll_cutoff.isoformat(),
        "cadence_daily_summary_cutoff": cadence_cutoff.isoformat(),
        "team_poll_result_selected": len(team_poll_ids),
        "cadence_daily_summary_selected": len(cadence_ids),
        "team_poll_result_deleted": team_poll_deleted,
        "cadence_daily_summary_deleted": cadence_deleted,
    }


EXECUTOR_REGISTRY = {
    "cadence_reminder": execute_cadence_reminder,
    "cadence_expiry": execute_cadence_expiry,
    "sessions_cleanup": execute_sessions_cleanup,
    "slack_hourly_reader": execute_slack_hourly_reader,
    "team_poll_cycle": execute_team_poll_cycle,
    "recommendation_full": execute_recommendation_full,
    "recommendation_incremental": execute_recommendation_incremental,
    "action_item_digest": execute_action_item_digest,
    "action_item_followup_escalation": execute_action_item_followup_escalation,
    "schedule_runs_cleanup": execute_schedule_runs_cleanup,
    "channel_index_cleanup": execute_channel_index_cleanup,
    "project_summary_cleanup": execute_project_summary_cleanup,
}
