"""Scheduler hooks for team poll jobs."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from app.core.config import settings
from app.core.database import get_brain_collection
from app.core.security import encryption_service
from app.scheduler.repository import SchedulerRepository
from app.team_poll.formatter import build_poll_prompt
from app.team_poll.providers.slack import SlackTeamPollAdapter
from app.team_poll.schema import compute_member_due_at, parse_minutes_csv, session_outcome
from app.team_poll.summary import finalize_poll_if_ready


def _utcnow() -> datetime:
    return datetime.utcnow()


async def reconcile_team_poll_schedule(db, *, project_id: str) -> Dict[str, Any]:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return {"ok": False, "reason": "missing_project_id"}

    active_count = await db.session_index.count_documents(
        {
            "entity_type": "team_poll_session",
            "source": "team_poll",
            "project_id": normalized_project_id,
            "status": {"$ne": "completed"},
        },
        limit=1,
    )

    repo = SchedulerRepository(db)
    await repo.ensure_indexes()
    if int(active_count or 0) > 0:
        now = _utcnow()
        await repo.upsert_schedule(
            job_type="team_poll_cycle",
            project_id=normalized_project_id,
            schedule_spec={"mode": "interval", "minutes": int(getattr(settings, "team_poll_cycle_interval_minutes", 5) or 5)},
            next_run_at=now,
            enabled=True,
        )
        return {"ok": True, "active": True, "action": "upserted"}

    deleted = await repo.delete_schedule(job_type="team_poll_cycle", project_id=normalized_project_id)
    return {"ok": True, "active": False, "action": "deleted" if deleted > 0 else "unchanged"}


async def execute_team_poll_cycle(db, schedule: Dict[str, Any]) -> Dict[str, Any]:
    project_id = str(schedule.get("project_id") or "").strip()
    if not project_id:
        return {"skipped": True, "reason": "missing_project_id"}
    project = await db.projects.find_one({"project_id": project_id})
    if not isinstance(project, dict):
        return {"skipped": True, "reason": "project_not_found"}

    collection = get_brain_collection(project_id)
    now = _utcnow()
    batch_size = max(1, int(getattr(settings, "team_poll_nudge_batch_size", 500) or 500))
    max_pages = max(1, int(getattr(settings, "team_poll_max_pages_per_cron_run", 20) or 20))
    processed = 0
    nudged = 0
    delivered = 0
    expired = 0
    blocked = 0
    touched_polls: Set[str] = set()
    adapter = _build_slack_adapter_from_project(project)
    cycle_window_end = now
    anchor_updated_at: Optional[datetime] = None
    anchor_id: Any = None

    for page in range(max_pages):
        query: Dict[str, Any] = {
            "entity_type": "team_poll_session",
            "source": "team_poll",
            "status": {"$ne": "completed"},
            "updated_at": {"$lt": cycle_window_end},
        }
        if anchor_updated_at is not None and anchor_id is not None:
            query["$or"] = [
                {"updated_at": {"$gt": anchor_updated_at, "$lt": cycle_window_end}},
                {"updated_at": anchor_updated_at, "_id": {"$gt": anchor_id}},
            ]

        sessions = await collection.find(query).sort([("updated_at", 1), ("_id", 1)]).limit(batch_size).to_list(
            length=batch_size
        )
        if not sessions:
            break

        last_row = sessions[-1]
        anchor_updated_at = last_row.get("updated_at")
        anchor_id = last_row.get("_id")

        for session in sessions:
            processed += 1
            poll_id = str(session.get("poll_id") or "").strip()
            if poll_id:
                touched_polls.add(poll_id)
            updated = await _process_session_row(
                db=db,
                project=project,
                collection=collection,
                session=session,
                adapter=adapter,
                now=now,
            )
            nudged += int(updated.get("nudged", 0))
            delivered += int(updated.get("delivered", 0))
            expired += int(updated.get("expired", 0))
            blocked += int(updated.get("blocked", 0))
        if len(sessions) < batch_size:
            break

    finalized = 0
    for poll_id in sorted(touched_polls):
        result = await finalize_poll_if_ready(
            db,
            project=project,
            poll_id=poll_id,
            force=False,
        )
        if result.get("ok") and str(result.get("reason") or "") in {"finalized", "already_finalized"}:
            finalized += 1

    reconcile_result = await reconcile_team_poll_schedule(db, project_id=project_id)

    return {
        "project_id": project_id,
        "processed": processed,
        "delivered": delivered,
        "nudged": nudged,
        "expired": expired,
        "blocked": blocked,
        "finalized": finalized,
        "schedule_reconcile": reconcile_result,
    }


def _build_slack_adapter_from_project(project: Dict[str, Any]) -> Optional[SlackTeamPollAdapter]:
    encrypted = (((project.get("integrations") or {}).get("slack") or {}).get("access_token"))
    if not encrypted:
        return None
    try:
        token = encryption_service.decrypt(encrypted)
    except Exception:
        return None
    return SlackTeamPollAdapter(token)


async def _process_session_row(
    *,
    db,
    project: Dict[str, Any],
    collection,
    session: Dict[str, Any],
    adapter: Optional[SlackTeamPollAdapter],
    now: datetime,
) -> Dict[str, int]:
    result = {"nudged": 0, "delivered": 0, "expired": 0, "blocked": 0}
    member_due_at = session.get("expires_at")
    delivery_status = str((session.get("outcome") or {}).get("delivery_status") or "pending").strip().lower()
    member_slack_id = str(session.get("external_user_id") or "").strip()

    if _is_past_due(member_due_at, now):
        if delivery_status == "deferred_by_active_cadence":
            await _mark_completed(
                collection=collection,
                session=session,
                expired_reason="blocked_by_active_cadence_deadline",
                delivery_status="blocked_by_active_cadence_deadline",
                response_status="no_response_expired",
                reason_codes=["blocked_by_active_cadence_deadline"],
                now=now,
            )
            result["blocked"] += 1
            return result
        await _mark_completed(
            collection=collection,
            session=session,
            expired_reason="deadline_expired",
            delivery_status=delivery_status if delivery_status else "sent",
            response_status="no_response_expired",
            reason_codes=["deadline_expired"],
            now=now,
        )
        result["expired"] += 1
        return result

    if delivery_status == "deferred_by_active_cadence":
        if member_slack_id and not await _has_active_cadence_for_member(db=db, session=session):
            owner_prompt = str(session.get("user_id") or "").strip() == str(session.get("owner_user_id") or "").strip()
            prompt = build_poll_prompt(session=session, owner_prompt=owner_prompt)
            delivered = False
            if adapter:
                delivered = await adapter.send_prompt(
                    target_external_user_id=member_slack_id,
                    payload=prompt,
                    workspace_id=str(session.get("workspace_id") or "").strip() or None,
                )
            if delivered:
                deadline_at = session.get("deadline_at") or session.get("expires_at") or now
                effective_member_due_at = (
                    compute_member_due_at(delivery_time=now, deadline_at=deadline_at)
                    if isinstance(deadline_at, datetime)
                    else now
                )
                refreshed_for_nudge = dict(session)
                refreshed_for_nudge["expires_at"] = effective_member_due_at
                await collection.update_one(
                    {"_id": session.get("_id")},
                    {
                        "$set": {
                            "outcome.delivery_status": "sent",
                            "outcome.reason_codes": [],
                            "expires_at": effective_member_due_at,
                            "next_nudge_at": _compute_next_nudge_at(session=refreshed_for_nudge, from_time=now),
                            "updated_at": now,
                        }
                    },
                )
                result["delivered"] += 1
        return result

    next_nudge_at = session.get("next_nudge_at")
    if next_nudge_at and isinstance(next_nudge_at, datetime) and next_nudge_at <= now:
        if adapter and member_slack_id:
            ok = await adapter.send_nudge(
                target_external_user_id=member_slack_id,
                text=(
                    "Reminder: team poll is still awaiting your response.\n"
                    f"Question: {str(session.get('question_text') or '').strip()}"
                ),
                workspace_id=str(session.get("workspace_id") or "").strip() or None,
            )
            if ok:
                nudge_count = int(session.get("nudge_count") or 0) + 1
                await collection.update_one(
                    {"_id": session.get("_id")},
                    {
                        "$set": {
                            "nudge_count": nudge_count,
                            "last_nudged_at": now,
                            "next_nudge_at": _compute_next_nudge_at(session=session, from_time=now, nudge_count=nudge_count),
                            "updated_at": now,
                        }
                    },
                )
                result["nudged"] += 1
    return result


def _is_past_due(member_due_at: Any, now: datetime) -> bool:
    return isinstance(member_due_at, datetime) and member_due_at <= now


async def _has_active_cadence_for_member(*, db, session: Dict[str, Any]) -> bool:
    member_slack_id = str(session.get("external_user_id") or "").strip()
    workspace_id = str(session.get("workspace_id") or "").strip()
    if not member_slack_id or not workspace_id:
        return False
    try:
        from app.cadence.orchestrator import find_active_sessions_by_identity

        rows = await find_active_sessions_by_identity(
            db,
            provider="slack",
            workspace_id=workspace_id,
            external_user_id=member_slack_id,
            limit=1,
        )
        return bool(rows)
    except Exception:
        return False


def _compute_next_nudge_at(*, session: Dict[str, Any], from_time: datetime, nudge_count: Optional[int] = None) -> Optional[datetime]:
    offsets = parse_minutes_csv(
        ((session.get("nudge_policy") or {}).get("offsets_minutes") or ""),
        default=parse_minutes_csv(getattr(settings, "team_poll_nudge_offsets_minutes", "30,60"), default=[30, 60]),
    )
    if not offsets:
        return None
    count = int(session.get("nudge_count") if nudge_count is None else nudge_count)
    if count >= len(offsets):
        return None
    candidate = from_time + timedelta(minutes=max(1, int(offsets[count])))
    member_due_at = session.get("expires_at")
    if isinstance(member_due_at, datetime) and candidate >= member_due_at:
        return None
    return candidate


async def _mark_completed(
    *,
    collection,
    session: Dict[str, Any],
    expired_reason: str,
    delivery_status: str,
    response_status: str,
    reason_codes: List[str],
    now: datetime,
) -> None:
    await collection.update_one(
        {"_id": session.get("_id")},
        {
            "$set": {
                "status": "completed",
                "completed_at": now,
                "outcome": session_outcome(
                    delivery_status=delivery_status,
                    response_status=response_status,
                    reason_codes=reason_codes,
                    terminal=True,
                ),
                "updated_at": now,
            }
        },
    )
