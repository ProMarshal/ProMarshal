"""Higher-level scheduler service helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.scheduler.repository import SchedulerRepository
from app.scheduler.timecalc import compute_next_run_at


def _normalize_hhmm(value: str) -> str:
    raw = str(value or "").strip()
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError("time must be in HH:MM format")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("time must be in HH:MM format")
    return f"{hour:02d}:{minute:02d}"


def _parse_recommendation_daily_times(value: str) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        raw = "09:00,18:00"
    result: list[str] = []
    seen: set[str] = set()
    for chunk in raw.split(","):
        normalized = _normalize_hhmm(chunk)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result or ["09:00", "18:00"]


async def ensure_global_schedules(db: AsyncIOMotorDatabase) -> None:
    """Ensure required global schedules exist."""
    repo = SchedulerRepository(db)
    await repo.ensure_indexes()

    # Remove legacy health snapshot schedule (replaced by live-compute + Redis cache).
    await repo.delete_schedule(job_type="health_calc", project_id=None)

    now = datetime.utcnow()
    defaults = [
        ("slack_hourly_reader", {"mode": "interval", "minutes": 60}),
        ("sessions_cleanup", {"mode": "interval", "minutes": 30}),
        ("cadence_expiry", {"mode": "interval", "minutes": 10}),
        ("schedule_runs_cleanup", {"mode": "daily", "time": "02:00"}),
        ("channel_index_cleanup", {"mode": "daily", "time": "02:30"}),
    ]
    for job_type, spec in defaults:
        existing = await repo.get_schedule(job_type=job_type, project_id=None)
        if existing:
            continue
        # Make global schedule immediately due on first installation so first
        # scheduler tick does useful work.
        next_run_at = now
        await repo.upsert_schedule(
            job_type=job_type,
            project_id=None,
            schedule_spec=spec,
            next_run_at=next_run_at,
            enabled=True,
        )


async def ensure_project_summary_cleanup_schedules_for_projects(db: AsyncIOMotorDatabase) -> int:
    """
    Ensure per-project summary cleanup schedules exist.

    Weekly cadence is modeled via interval minutes (10080) because scheduler supports
    interval/daily modes only.
    """
    repo = SchedulerRepository(db)
    await repo.ensure_indexes()
    changed = 0
    now = datetime.utcnow()
    spec = {"mode": "interval", "minutes": 10080}
    next_run_at = compute_next_run_at(
        schedule_spec=spec,
        now_utc=now,
        project_timezone="UTC",
    )
    projects = await db.projects.find(
        {},
        {"project_id": 1},
    ).to_list(length=None)
    for project in projects:
        custom_project_id = str(project.get("project_id") or "").strip()
        if not custom_project_id:
            continue
        existing = await repo.get_schedule(job_type="project_summary_cleanup", project_id=custom_project_id)
        if existing:
            continue
        await repo.upsert_schedule(
            job_type="project_summary_cleanup",
            project_id=custom_project_id,
            schedule_spec=spec,
            next_run_at=next_run_at,
            enabled=True,
        )
        changed += 1
    return changed


async def ensure_team_poll_schedules_for_active_projects(db: AsyncIOMotorDatabase) -> int:
    """
    Ensure team_poll_cycle schedules exist for projects with active team poll sessions.
    """
    from app.core.config import settings

    repo = SchedulerRepository(db)
    await repo.ensure_indexes()
    now = datetime.utcnow()
    interval_minutes = max(1, int(getattr(settings, "team_poll_cycle_interval_minutes", 5) or 5))

    active_project_ids = await db.session_index.distinct(
        "project_id",
        {
            "entity_type": "team_poll_session",
            "source": "team_poll",
            "status": {"$ne": "completed"},
        },
    )
    created_or_updated = 0
    for project_id in active_project_ids:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            continue
        await repo.upsert_schedule(
            job_type="team_poll_cycle",
            project_id=normalized_project_id,
            schedule_spec={"mode": "interval", "minutes": interval_minutes},
            next_run_at=now,
            enabled=True,
        )
        created_or_updated += 1
    return created_or_updated


async def ensure_cadence_schedules_for_projects(db: AsyncIOMotorDatabase) -> int:
    """
    Ensure cadence schedules exist for all cadence-eligible projects.

    Eligibility: Slack connected.
    """
    repo = SchedulerRepository(db)
    await repo.ensure_indexes()

    created = 0
    projects = await db.projects.find(
        {"integrations.slack.status": "connected"},
        {"project_id": 1, "timezone": 1},
    ).to_list(length=None)
    now = datetime.utcnow()
    for project in projects:
        custom_project_id = str(project.get("project_id") or "").strip()
        if not custom_project_id:
            continue
        existing = await repo.get_schedule(job_type="cadence_reminder", project_id=custom_project_id)
        if existing:
            continue
        timezone = str(project.get("timezone") or "UTC").strip() or "UTC"
        spec = {
            "mode": "daily",
            "time": "09:00",
            "skip_weekends": False,
            "ignore_followup_for_owner": False,
        }
        next_run_at = compute_next_run_at(schedule_spec=spec, now_utc=now, project_timezone=timezone)
        await repo.upsert_schedule(
            job_type="cadence_reminder",
            project_id=custom_project_id,
            schedule_spec=spec,
            next_run_at=next_run_at,
            enabled=True,
        )
        created += 1
    return created


async def ensure_recommendation_schedules_for_projects(db: AsyncIOMotorDatabase) -> int:
    """
    Ensure recommendation schedules for projects with connected workitem integration.

    Creates:
    - recommendation_full (twice daily via project timezone)
    - recommendation_incremental (interval drain for dirty set)
    """
    from app.core.config import settings

    repo = SchedulerRepository(db)
    await repo.ensure_indexes()
    now = datetime.utcnow()

    project_rows = await db.projects.find(
        {},
        {"project_id": 1, "timezone": 1, "integrations": 1},
    ).to_list(length=None)
    daily_times = _parse_recommendation_daily_times(
        getattr(settings, "recommendation_full_daily_times", "09:00,18:00")
    )
    incremental_minutes = max(
        1, int(getattr(settings, "recommendation_incremental_interval_seconds", 120) or 120) // 60
    )

    def _has_connected_workitem_integration(project_doc: Dict[str, Any]) -> bool:
        integrations = project_doc.get("integrations")
        if not isinstance(integrations, dict) or not integrations:
            return False

        for provider_name, config in integrations.items():
            if not isinstance(config, dict):
                continue
            normalized_status = str(config.get("status") or "").strip().lower()
            normalized_category = str(config.get("category") or "").strip().lower()
            if normalized_status != "connected":
                continue
            if normalized_category == "workitem":
                return True
            # Backward compatibility for older docs without explicit category field.
            if str(provider_name or "").strip().lower() == "jira":
                return True
        return False

    changed = 0
    for project in project_rows:
        custom_project_id = str(project.get("project_id") or "").strip()
        if not custom_project_id:
            continue
        if not _has_connected_workitem_integration(project):
            continue
        timezone = str(project.get("timezone") or "UTC").strip() or "UTC"

        # One daily schedule per configured run time.
        for daily_time in daily_times:
            job_type = f"recommendation_full_{daily_time.replace(':', '')}"
            spec = {"mode": "daily", "time": daily_time}
            next_run_at = compute_next_run_at(
                schedule_spec=spec,
                now_utc=now,
                project_timezone=timezone,
            )
            await repo.upsert_schedule(
                job_type=job_type,
                project_id=custom_project_id,
                schedule_spec=spec,
                next_run_at=next_run_at,
                enabled=True,
            )
            changed += 1

        existing_incremental = await repo.get_schedule(
            job_type="recommendation_incremental",
            project_id=custom_project_id,
        )
        if not existing_incremental:
            await repo.upsert_schedule(
                job_type="recommendation_incremental",
                project_id=custom_project_id,
                schedule_spec={"mode": "interval", "minutes": incremental_minutes},
                next_run_at=now,
                enabled=True,
            )
            changed += 1

    return changed


async def ensure_action_item_schedules_for_projects(db: AsyncIOMotorDatabase) -> int:
    """
    Ensure action-item digest and follow-up escalation schedules for Slack-connected projects.

    - action_item_digest: daily at 12:30 project local time
    - action_item_followup_escalation: hourly interval scan
    """
    from app.core.config import settings

    if not bool(getattr(settings, "action_items_enabled", False)):
        return 0

    repo = SchedulerRepository(db)
    await repo.ensure_indexes()
    now = datetime.utcnow()

    projects = await db.projects.find(
        {"integrations.slack.status": "connected"},
        {"project_id": 1, "timezone": 1},
    ).to_list(length=None)

    changed = 0
    for project in projects:
        custom_project_id = str(project.get("project_id") or "").strip()
        if not custom_project_id:
            continue

        timezone = str(project.get("timezone") or "UTC").strip() or "UTC"
        existing_digest = await repo.get_schedule(
            job_type="action_item_digest",
            project_id=custom_project_id,
        )
        if not existing_digest:
            digest_spec = {
                "mode": "daily",
                "time": "12:30",
                "skip_weekends": False,
                "ignore_followup_for_owner": False,
            }
            digest_next = compute_next_run_at(
                schedule_spec=digest_spec,
                now_utc=now,
                project_timezone=timezone,
            )
            await repo.upsert_schedule(
                job_type="action_item_digest",
                project_id=custom_project_id,
                schedule_spec=digest_spec,
                next_run_at=digest_next,
                enabled=bool(getattr(settings, "action_items_digest_enabled", False)),
            )
            changed += 1

        existing_escalation = await repo.get_schedule(
            job_type="action_item_followup_escalation",
            project_id=custom_project_id,
        )
        if not existing_escalation:
            escalation_spec = {"mode": "interval", "minutes": 60}
            await repo.upsert_schedule(
                job_type="action_item_followup_escalation",
                project_id=custom_project_id,
                schedule_spec=escalation_spec,
                next_run_at=now,
                enabled=True,
            )
            changed += 1

    return changed


async def upsert_action_item_digest_schedule_for_project(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    enabled: bool,
    time_hhmm: str,
    skip_weekends: bool = False,
    ignore_followup_for_owner: bool = False,
) -> Dict[str, Any]:
    """Create or update action-item digest schedule row for a project."""
    custom_project_id = str(project_id or "").strip()
    if not custom_project_id:
        raise ValueError("project_id is required")

    project = await db.projects.find_one({"project_id": custom_project_id}, {"timezone": 1})
    if not project:
        raise ValueError("Project not found")

    timezone = str(project.get("timezone") or "UTC").strip() or "UTC"
    normalized_time = _normalize_hhmm(time_hhmm)
    schedule_spec = {
        "mode": "daily",
        "time": normalized_time,
        "skip_weekends": bool(skip_weekends),
        "ignore_followup_for_owner": bool(ignore_followup_for_owner),
    }

    now = datetime.utcnow()
    next_run_at = compute_next_run_at(
        schedule_spec=schedule_spec,
        now_utc=now,
        project_timezone=timezone,
    )

    repo = SchedulerRepository(db)
    await repo.ensure_indexes()
    schedule = await repo.upsert_schedule(
        job_type="action_item_digest",
        project_id=custom_project_id,
        schedule_spec=schedule_spec,
        next_run_at=next_run_at,
        enabled=enabled,
    )
    return schedule


async def get_or_create_action_item_digest_schedule_for_project(
    db: AsyncIOMotorDatabase,
    *,
    project_mongo_id: str,
) -> Dict[str, Any]:
    """Return action-item digest schedule for a project; create default when missing."""
    try:
        project = await db.projects.find_one({"_id": ObjectId(project_mongo_id)})
    except Exception:
        project = None
    if not project:
        raise ValueError("Project not found")

    custom_project_id = str(project.get("project_id") or "").strip()
    if not custom_project_id:
        raise ValueError("Project custom ID missing")

    repo = SchedulerRepository(db)
    await repo.ensure_indexes()
    existing = await repo.get_schedule(job_type="action_item_digest", project_id=custom_project_id)
    if existing:
        return existing

    from app.core.config import settings

    return await upsert_action_item_digest_schedule_for_project(
        db,
        project_id=custom_project_id,
        enabled=bool(getattr(settings, "action_items_digest_enabled", False)),
        time_hhmm="12:30",
        skip_weekends=False,
        ignore_followup_for_owner=False,
    )


async def upsert_cadence_schedule_for_project(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    enabled: bool,
    time_hhmm: str,
    skip_weekends: bool = False,
    ignore_followup_for_owner: bool = False,
) -> Dict[str, Any]:
    """Create or update cadence schedule row for a project."""
    custom_project_id = str(project_id or "").strip()
    if not custom_project_id:
        raise ValueError("project_id is required")

    project = await db.projects.find_one({"project_id": custom_project_id}, {"timezone": 1})
    if not project:
        raise ValueError("Project not found")

    timezone = str(project.get("timezone") or "UTC").strip() or "UTC"
    normalized_time = _normalize_hhmm(time_hhmm)
    schedule_spec = {
        "mode": "daily",
        "time": normalized_time,
        "skip_weekends": bool(skip_weekends),
        "ignore_followup_for_owner": bool(ignore_followup_for_owner),
    }

    now = datetime.utcnow()
    next_run_at = compute_next_run_at(
        schedule_spec=schedule_spec,
        now_utc=now,
        project_timezone=timezone,
    )

    repo = SchedulerRepository(db)
    await repo.ensure_indexes()
    schedule = await repo.upsert_schedule(
        job_type="cadence_reminder",
        project_id=custom_project_id,
        schedule_spec=schedule_spec,
        next_run_at=next_run_at,
        enabled=enabled,
    )
    return schedule


async def get_or_create_cadence_schedule_for_project(
    db: AsyncIOMotorDatabase,
    *,
    project_mongo_id: str,
) -> Dict[str, Any]:
    """Return cadence schedule for a project; create default when missing."""
    try:
        project = await db.projects.find_one({"_id": ObjectId(project_mongo_id)})
    except Exception:
        project = None
    if not project:
        raise ValueError("Project not found")

    custom_project_id = str(project.get("project_id") or "").strip()
    if not custom_project_id:
        raise ValueError("Project custom ID missing")

    repo = SchedulerRepository(db)
    await repo.ensure_indexes()
    existing = await repo.get_schedule(job_type="cadence_reminder", project_id=custom_project_id)
    if existing:
        return existing

    return await upsert_cadence_schedule_for_project(
        db,
        project_id=custom_project_id,
        enabled=True,
        time_hhmm="09:00",
        skip_weekends=False,
        ignore_followup_for_owner=False,
    )


async def realign_project_daily_schedules_for_timezone(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
) -> Dict[str, int]:
    """
    Recompute next_run_at for all project-scoped daily schedules using current project timezone.

    This is used after timezone updates so already-scheduled daily jobs do not fire once
    with stale timezone math.
    """
    custom_project_id = str(project_id or "").strip()
    if not custom_project_id:
        raise ValueError("project_id is required")

    project = await db.projects.find_one({"project_id": custom_project_id}, {"timezone": 1})
    if not project:
        raise ValueError("Project not found")

    timezone = str(project.get("timezone") or "UTC").strip() or "UTC"
    now = datetime.utcnow()

    schedules = await db.project_schedules.find(
        {
            "project_id": custom_project_id,
            "schedule_spec.mode": "daily",
        },
        {"schedule_id": 1, "schedule_spec": 1},
    ).to_list(length=None)

    realigned = 0
    failed = 0
    for schedule in schedules:
        schedule_id = str(schedule.get("schedule_id") or "").strip()
        if not schedule_id:
            failed += 1
            continue
        schedule_spec = dict(schedule.get("schedule_spec") or {})
        try:
            next_run_at = compute_next_run_at(
                schedule_spec=schedule_spec,
                now_utc=now,
                project_timezone=timezone,
            )
            await db.project_schedules.update_one(
                {"schedule_id": schedule_id},
                {
                    "$set": {
                        "next_run_at": next_run_at,
                        "updated_at": now,
                    }
                },
            )
            realigned += 1
        except Exception:
            failed += 1

    return {
        "project_daily_schedules_found": len(schedules),
        "project_daily_schedules_realigned": realigned,
        "project_daily_schedules_failed": failed,
    }
