"""Cron endpoints for scheduled tasks."""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Header, HTTPException

from app.core.config import settings
from app.core.database import get_database
from app.core.redis_queue import get_redis_connection
from app.scheduler.engine import SchedulerEngine
from app.scheduler.service import (
    ensure_action_item_schedules_for_projects,
    ensure_cadence_schedules_for_projects,
    ensure_global_schedules,
    ensure_project_summary_cleanup_schedules_for_projects,
    ensure_recommendation_schedules_for_projects,
    ensure_team_poll_schedules_for_active_projects,
)

router = APIRouter(prefix="/api/cron", tags=["cron"])
logger = logging.getLogger("app.routes.cron")
_DISPATCHER_TICK_LOCK_KEY = "scheduler:dispatcher:tick:lock"
_LAST_SEED_BUCKET_MINUTE: Optional[int] = None
_INPROCESS_TICK_LOCK = Lock()
_INPROCESS_TICK_LOCK_UNTIL_MONOTONIC = 0.0

PRODUCT_FAST_LANE_JOB_TYPES = (
    "cadence_expiry",
    "recommendation_incremental",
    "action_item_followup_escalation",
    "team_poll_cycle",
    "slack_hourly_reader",
)

DAILY_LANE_BASE_JOB_TYPES = (
    "cadence_reminder",
    "action_item_digest",
)

MAINTENANCE_LANE_JOB_TYPES = (
    "sessions_cleanup",
    "schedule_runs_cleanup",
    "channel_index_cleanup",
    "project_summary_cleanup",
)


def _validate_cron_authorization(authorization: Optional[str]) -> None:
    """Validate Authorization header for cron endpoints."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authentication scheme")
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid authorization header format") from exc
    expected_secret = settings.cron_secret if hasattr(settings, "cron_secret") else None
    if not expected_secret or token != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid cron secret")


def _dispatcher_skip_response(endpoint_name: str) -> Dict[str, Any]:
    payload = {
        "status": "skipped",
        "skipped": True,
        "reason": "dispatcher_mode",
        "endpoint": endpoint_name,
    }
    logger.info(
        "cron_endpoint_skipped_dispatcher_mode endpoint=%s reason=dispatcher_mode",
        endpoint_name,
    )
    return payload


def _tick_lock_skip_response() -> Dict[str, Any]:
    payload = {
        "status": "skipped",
        "skipped": True,
        "reason": "tick_lock_active",
        "endpoint": "tick",
    }
    logger.info("cron_dispatcher_tick_skipped reason=tick_lock_active")
    return payload


def _should_skip_legacy_endpoint(endpoint_name: str) -> Optional[Dict[str, Any]]:
    if not bool(getattr(settings, "scheduler_use_dispatcher", False)):
        return None
    return _dispatcher_skip_response(endpoint_name)


def _recommendation_full_job_types() -> list[str]:
    raw_times = str(getattr(settings, "recommendation_full_daily_times", "09:00,18:00") or "09:00,18:00")
    full_job_types: list[str] = []
    for token in raw_times.split(","):
        cleaned = str(token or "").strip()
        if not cleaned:
            continue
        full_job_types.append(f"recommendation_full_{cleaned.replace(':', '')}")
    if not full_job_types:
        full_job_types = ["recommendation_full_0900", "recommendation_full_1800"]
    return full_job_types


def _load_job_type_limits() -> Dict[str, int]:
    raw = str(getattr(settings, "scheduler_job_type_limits_json", "") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        logger.warning("scheduler_job_type_limits_json_invalid error=%s", exc)
        return {}
    if not isinstance(parsed, dict):
        return {}
    limits: Dict[str, int] = {}
    for key, value in parsed.items():
        name = str(key or "").strip()
        if not name:
            continue
        try:
            limits[name] = max(1, int(value))
        except Exception:
            continue
    return limits


def _effective_limit_for_job(
    *,
    job_type: str,
    lane_default: int,
    job_limits: Dict[str, int],
) -> int:
    normalized = str(job_type or "").strip()
    if normalized in job_limits:
        return max(1, int(job_limits[normalized]))
    if normalized.startswith("recommendation_full_") and "recommendation_full" in job_limits:
        return max(1, int(job_limits["recommendation_full"]))
    if lane_default:
        return max(1, int(lane_default))
    return max(1, int(getattr(settings, "scheduler_max_due_per_tick", 100) or 100))


def _should_run_daily_lane(now_utc: datetime) -> bool:
    interval = max(1, int(getattr(settings, "scheduler_dispatcher_daily_interval_minutes", 10) or 10))
    return now_utc.minute % interval == 0


def _should_seed_schedules(now_utc: datetime) -> bool:
    global _LAST_SEED_BUCKET_MINUTE
    current_bucket = int(now_utc.timestamp() // 60)
    if _LAST_SEED_BUCKET_MINUTE is None:
        _LAST_SEED_BUCKET_MINUTE = current_bucket
        return True
    if _LAST_SEED_BUCKET_MINUTE == current_bucket:
        return False
    interval = max(1, int(getattr(settings, "scheduler_dispatcher_seed_interval_minutes", 10) or 10))
    if now_utc.minute % interval == 0:
        _LAST_SEED_BUCKET_MINUTE = current_bucket
        return True
    return False


def _try_acquire_dispatcher_tick_lock() -> Tuple[bool, Optional[str]]:
    def _try_inprocess_fallback_lock(ttl_seconds: int) -> Tuple[bool, Optional[str]]:
        global _INPROCESS_TICK_LOCK_UNTIL_MONOTONIC
        now_mono = time.monotonic()
        with _INPROCESS_TICK_LOCK:
            if _INPROCESS_TICK_LOCK_UNTIL_MONOTONIC > now_mono:
                return False, None
            _INPROCESS_TICK_LOCK_UNTIL_MONOTONIC = now_mono + max(1, ttl_seconds)
            return True, f"mem:{uuid.uuid4()}"

    redis_conn = get_redis_connection()
    ttl_seconds = max(5, int(getattr(settings, "scheduler_dispatcher_tick_lock_ttl_seconds", 55) or 55))
    if redis_conn is None:
        return _try_inprocess_fallback_lock(ttl_seconds)
    token = str(uuid.uuid4())
    try:
        created = redis_conn.set(_DISPATCHER_TICK_LOCK_KEY, token.encode("utf-8"), nx=True, ex=ttl_seconds)
        return bool(created), token
    except Exception as exc:
        logger.warning("cron_dispatcher_tick_lock_acquire_failed error=%s", exc)
        return _try_inprocess_fallback_lock(ttl_seconds)


def _release_dispatcher_tick_lock(token: Optional[str]) -> None:
    if not token:
        return
    if str(token).startswith("mem:"):
        global _INPROCESS_TICK_LOCK_UNTIL_MONOTONIC
        with _INPROCESS_TICK_LOCK:
            _INPROCESS_TICK_LOCK_UNTIL_MONOTONIC = 0.0
        return
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    script = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  redis.call('del', KEYS[1])
  return 1
end
return 0
"""
    try:
        redis_conn.eval(script, 1, _DISPATCHER_TICK_LOCK_KEY, token)
    except Exception as exc:
        logger.warning("cron_dispatcher_tick_lock_release_failed error=%s", exc)


async def _run_tick(engine: SchedulerEngine, job_type: str, *, limit: int) -> Dict[str, Any]:
    started_at = time.perf_counter()
    result = await engine.tick(job_type=job_type, limit=max(1, int(limit)))
    logger.info(
        "Cron dispatcher tick completed | job_type=%s processed=%s success=%s failed=%s skipped=%s duration_ms=%s",
        job_type,
        result.get("processed", 0),
        result.get("success", 0),
        result.get("failed", 0),
        result.get("skipped", 0),
        int((time.perf_counter() - started_at) * 1000),
    )
    return result


@router.post("/tick")
async def trigger_scheduler_dispatcher_tick(authorization: Optional[str] = Header(None)):
    """
    Central dispatcher cron endpoint.

    Fast lane runs every tick.
    Daily lane runs at scheduler_dispatcher_daily_interval_minutes cadence.
    """
    _validate_cron_authorization(authorization)
    lock_acquired, lock_token = _try_acquire_dispatcher_tick_lock()
    if not lock_acquired:
        return _tick_lock_skip_response()
    try:
        started_at = time.perf_counter()
        now_utc = datetime.now(timezone.utc)
        db = get_database()
        seeded = {
            "cadence": 0,
            "team_poll": 0,
            "recommendation": 0,
            "action_items": 0,
            "project_summary_cleanup": 0,
        }
        if _should_seed_schedules(now_utc):
            await ensure_global_schedules(db)
            seeded = {
                "cadence": await ensure_cadence_schedules_for_projects(db),
                "team_poll": await ensure_team_poll_schedules_for_active_projects(db),
                "recommendation": await ensure_recommendation_schedules_for_projects(db),
                "action_items": await ensure_action_item_schedules_for_projects(db),
                "project_summary_cleanup": await ensure_project_summary_cleanup_schedules_for_projects(db),
            }

        engine = SchedulerEngine(db)
        fast_budget = max(1, int(getattr(settings, "scheduler_dispatcher_fast_lane_budget", 70) or 70))
        daily_budget = max(1, int(getattr(settings, "scheduler_dispatcher_daily_lane_budget", 30) or 30))
        job_limits = _load_job_type_limits()

        fast_results: Dict[str, Any] = {}
        for job_type in PRODUCT_FAST_LANE_JOB_TYPES:
            fast_results[job_type] = await _run_tick(
                engine,
                job_type,
                limit=_effective_limit_for_job(
                    job_type=job_type,
                    lane_default=fast_budget,
                    job_limits=job_limits,
                ),
            )

        run_daily = _should_run_daily_lane(now_utc)
        daily_results: Dict[str, Any] = {}
        if run_daily:
            for job_type in DAILY_LANE_BASE_JOB_TYPES:
                daily_results[job_type] = await _run_tick(
                    engine,
                    job_type,
                    limit=_effective_limit_for_job(
                        job_type=job_type,
                        lane_default=daily_budget,
                        job_limits=job_limits,
                    ),
                )
            for job_type in _recommendation_full_job_types():
                daily_results[job_type] = await _run_tick(
                    engine,
                    job_type,
                    limit=_effective_limit_for_job(
                        job_type=job_type,
                        lane_default=daily_budget,
                        job_limits=job_limits,
                    ),
                )

        maintenance_results: Dict[str, Any] = {}
        for job_type in MAINTENANCE_LANE_JOB_TYPES:
            maintenance_results[job_type] = await _run_tick(
                engine,
                job_type,
                limit=_effective_limit_for_job(
                    job_type=job_type,
                    lane_default=daily_budget,
                    job_limits=job_limits,
                ),
            )

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "Cron dispatcher cycle completed | run_daily=%s duration_ms=%s seeded=%s",
            run_daily,
            duration_ms,
            seeded,
        )
        return {
            "status": "success",
            "dispatcher_mode": True,
            "run_daily_lane": run_daily,
            "seeded_schedules": seeded,
            "fast_lane_results": fast_results,
            "daily_lane_results": daily_results,
            "maintenance_lane_results": maintenance_results,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        logger.exception("Cron dispatcher tick failed")
        raise HTTPException(status_code=500, detail=f"Cron dispatcher tick failed: {str(exc)}") from exc
    finally:
        _release_dispatcher_tick_lock(lock_token)


@router.post("/cadence-reminders")
async def trigger_cadence_reminders(authorization: Optional[str] = Header(None)):
    """
    Trigger cadence reminders cron job.

    Called by GitHub Actions every 30 minutes.

    Requires: Authorization: Bearer <CRON_SECRET>
    """
    _validate_cron_authorization(authorization)
    skip_payload = _should_skip_legacy_endpoint("cadence-reminders")
    if skip_payload:
        return skip_payload

    try:
        started_at = time.perf_counter()
        db = get_database()
        seeded = await ensure_cadence_schedules_for_projects(db)
        engine = SchedulerEngine(db)
        result = await engine.tick(job_type="cadence_reminder")
        logger.info(
            "Cadence reminders scheduler tick completed | "
            f"seeded={seeded} processed={result.get('processed', 0)} "
            f"success={result.get('success', 0)} failed={result.get('failed', 0)} "
            f"duration_ms={int((time.perf_counter() - started_at) * 1000)}"
        )

        return {
            "status": "success",
            "message": "Cadence reminders scheduler tick executed successfully",
            "seeded_schedules": seeded,
            "result": result,
        }

    except Exception as exc:
        logger.exception("Cadence reminders scheduler tick failed")
        raise HTTPException(status_code=500, detail=f"Cron execution failed: {str(exc)}") from exc


@router.post("/slack-messages/hourly")
async def trigger_slack_message_reader(authorization: Optional[str] = Header(None)):
    """
    Trigger hourly Slack message reading cron job.

    Called by GitHub Actions every hour.

    Requires: Authorization: Bearer <CRON_SECRET>
    """
    _validate_cron_authorization(authorization)
    skip_payload = _should_skip_legacy_endpoint("slack-messages/hourly")
    if skip_payload:
        return skip_payload

    try:
        started_at = time.perf_counter()
        db = get_database()
        await ensure_global_schedules(db)
        engine = SchedulerEngine(db)
        result = await engine.tick(job_type="slack_hourly_reader")
        logger.info(
            "Slack message reader scheduler tick completed | "
            f"processed={result.get('processed', 0)} success={result.get('success', 0)} "
            f"failed={result.get('failed', 0)} "
            f"duration_ms={int((time.perf_counter() - started_at) * 1000)}"
        )

        return {
            "status": "success",
            "message": "Slack message reader scheduler tick executed successfully",
            "result": result,
        }

    except Exception as exc:
        logger.exception("Slack message reader scheduler tick failed")
        raise HTTPException(status_code=500, detail=f"Slack message reader execution failed: {str(exc)}") from exc


@router.post("/calculate-project-health")
async def calculate_project_health_cron(authorization: Optional[str] = Header(None)):
    """
    Deprecated: live health is computed on-demand and cached in Redis.
    This endpoint is kept for backward compatibility with existing cron schedules.

    Requires: Authorization: Bearer <CRON_SECRET>
    """
    _validate_cron_authorization(authorization)
    skip_payload = _should_skip_legacy_endpoint("calculate-project-health")
    if skip_payload:
        return skip_payload
    return {
        "status": "noop",
        "message": "Health snapshots are disabled; PM health is computed live with Redis caching.",
    }


@router.post("/sessions-cleanup")
async def trigger_sessions_cleanup(authorization: Optional[str] = Header(None)):
    """
    Trigger unified cleanup for Cadence/Team Poll session artifacts.

    Requires: Authorization: Bearer <CRON_SECRET>
    """
    _validate_cron_authorization(authorization)
    skip_payload = _should_skip_legacy_endpoint("sessions-cleanup")
    if skip_payload:
        return skip_payload

    try:
        started_at = time.perf_counter()
        db = get_database()
        await ensure_global_schedules(db)
        engine = SchedulerEngine(db)
        result = await engine.tick(job_type="sessions_cleanup")
        logger.info(
            "Unified sessions cleanup scheduler tick completed | "
            f"processed={result.get('processed', 0)} success={result.get('success', 0)} "
            f"failed={result.get('failed', 0)} "
            f"duration_ms={int((time.perf_counter() - started_at) * 1000)}"
        )
        return result
    except Exception as exc:
        logger.exception("Unified sessions cleanup scheduler tick failed")
        raise HTTPException(status_code=500, detail=f"Unified sessions cleanup failed: {str(exc)}") from exc


@router.post("/cadence-expiry")
async def trigger_cadence_expiry(authorization: Optional[str] = Header(None)):
    """
    Trigger cadence timeout finalization cycle.

    Requires: Authorization: Bearer <CRON_SECRET>
    """
    _validate_cron_authorization(authorization)
    skip_payload = _should_skip_legacy_endpoint("cadence-expiry")
    if skip_payload:
        return skip_payload

    try:
        started_at = time.perf_counter()
        db = get_database()
        await ensure_global_schedules(db)
        engine = SchedulerEngine(db)
        result = await engine.tick(job_type="cadence_expiry")
        logger.info(
            "Cadence expiry scheduler tick completed | "
            f"processed={result.get('processed', 0)} success={result.get('success', 0)} "
            f"failed={result.get('failed', 0)} "
            f"duration_ms={int((time.perf_counter() - started_at) * 1000)}"
        )
        return {
            "status": "success",
            "result": result,
        }
    except Exception as exc:
        logger.exception("Cadence expiry scheduler tick failed")
        raise HTTPException(status_code=500, detail=f"Cadence expiry scheduler tick failed: {str(exc)}") from exc


@router.post("/team-poll-cycle")
async def trigger_team_poll_cycle(authorization: Optional[str] = Header(None)):
    """
    Trigger team poll scheduler cycle.

    Requires: Authorization: Bearer <CRON_SECRET>
    """
    _validate_cron_authorization(authorization)
    skip_payload = _should_skip_legacy_endpoint("team-poll-cycle")
    if skip_payload:
        return skip_payload
    try:
        started_at = time.perf_counter()
        db = get_database()
        await ensure_global_schedules(db)
        seeded = await ensure_team_poll_schedules_for_active_projects(db)
        engine = SchedulerEngine(db)
        result = await engine.tick(job_type="team_poll_cycle")
        logger.info(
            "Team poll scheduler tick completed | "
            f"seeded={seeded} processed={result.get('processed', 0)} "
            f"success={result.get('success', 0)} failed={result.get('failed', 0)} "
            f"duration_ms={int((time.perf_counter() - started_at) * 1000)}"
        )
        return {
            "status": "success",
            "seeded_schedules": seeded,
            "result": result,
        }
    except Exception as exc:
        logger.exception("Team poll scheduler tick failed")
        raise HTTPException(status_code=500, detail=f"Team poll scheduler tick failed: {str(exc)}") from exc


@router.post("/recommendations/full")
async def trigger_recommendation_full(authorization: Optional[str] = Header(None)):
    """
    Trigger full recommendation generation scheduler ticks.

    Requires: Authorization: Bearer <CRON_SECRET>
    """
    _validate_cron_authorization(authorization)
    skip_payload = _should_skip_legacy_endpoint("recommendations/full")
    if skip_payload:
        return skip_payload
    try:
        started_at = time.perf_counter()
        db = get_database()
        seeded = await ensure_recommendation_schedules_for_projects(db)
        engine = SchedulerEngine(db)
        full_job_types = _recommendation_full_job_types()
        results = {}
        for job_type in full_job_types:
            results[job_type] = await engine.tick(job_type=job_type)
        logger.info(
            "Recommendation full scheduler tick completed | "
            f"seeded={seeded} duration_ms={int((time.perf_counter() - started_at) * 1000)}"
        )
        return {
            "status": "success",
            "seeded_schedules": seeded,
            "results": results,
        }
    except Exception as exc:
        logger.exception("Recommendation full scheduler tick failed")
        raise HTTPException(status_code=500, detail=f"Recommendation full scheduler tick failed: {str(exc)}") from exc


@router.post("/recommendations/incremental")
async def trigger_recommendation_incremental(authorization: Optional[str] = Header(None)):
    """
    Trigger incremental recommendation recompute scheduler tick.

    Requires: Authorization: Bearer <CRON_SECRET>
    """
    _validate_cron_authorization(authorization)
    skip_payload = _should_skip_legacy_endpoint("recommendations/incremental")
    if skip_payload:
        return skip_payload
    try:
        started_at = time.perf_counter()
        db = get_database()
        seeded = await ensure_recommendation_schedules_for_projects(db)
        engine = SchedulerEngine(db)
        result = await engine.tick(job_type="recommendation_incremental")
        logger.info(
            "Recommendation incremental scheduler tick completed | "
            f"seeded={seeded} processed={result.get('processed', 0)} "
            f"success={result.get('success', 0)} failed={result.get('failed', 0)} "
            f"duration_ms={int((time.perf_counter() - started_at) * 1000)}"
        )
        return {
            "status": "success",
            "seeded_schedules": seeded,
            "result": result,
        }
    except Exception as exc:
        logger.exception("Recommendation incremental scheduler tick failed")
        raise HTTPException(
            status_code=500,
            detail=f"Recommendation incremental scheduler tick failed: {str(exc)}",
        ) from exc


@router.post("/action-items/digest")
async def trigger_action_item_digest(authorization: Optional[str] = Header(None)):
    """Trigger action-item digest scheduler tick."""
    _validate_cron_authorization(authorization)
    skip_payload = _should_skip_legacy_endpoint("action-items/digest")
    if skip_payload:
        return skip_payload
    try:
        started_at = time.perf_counter()
        db = get_database()
        seeded = await ensure_action_item_schedules_for_projects(db)
        engine = SchedulerEngine(db)
        result = await engine.tick(job_type="action_item_digest")
        logger.info(
            "Action-item digest scheduler tick completed | "
            f"seeded={seeded} processed={result.get('processed', 0)} "
            f"success={result.get('success', 0)} failed={result.get('failed', 0)} "
            f"duration_ms={int((time.perf_counter() - started_at) * 1000)}"
        )
        return {"status": "success", "seeded_schedules": seeded, "result": result}
    except Exception as exc:
        logger.exception("Action-item digest scheduler tick failed")
        raise HTTPException(status_code=500, detail=f"Action-item digest scheduler tick failed: {str(exc)}") from exc


@router.post("/action-items/followup-escalation")
async def trigger_action_item_followup_escalation(authorization: Optional[str] = Header(None)):
    """Trigger action-item follow-up escalation scheduler tick."""
    _validate_cron_authorization(authorization)
    skip_payload = _should_skip_legacy_endpoint("action-items/followup-escalation")
    if skip_payload:
        return skip_payload
    try:
        started_at = time.perf_counter()
        db = get_database()
        seeded = await ensure_action_item_schedules_for_projects(db)
        engine = SchedulerEngine(db)
        result = await engine.tick(job_type="action_item_followup_escalation")
        logger.info(
            "Action-item followup escalation scheduler tick completed | "
            f"seeded={seeded} processed={result.get('processed', 0)} "
            f"success={result.get('success', 0)} failed={result.get('failed', 0)} "
            f"duration_ms={int((time.perf_counter() - started_at) * 1000)}"
        )
        return {"status": "success", "seeded_schedules": seeded, "result": result}
    except Exception as exc:
        logger.exception("Action-item followup escalation scheduler tick failed")
        raise HTTPException(
            status_code=500,
            detail=f"Action-item followup escalation scheduler tick failed: {str(exc)}",
        ) from exc
