"""Team poll summary and finalization."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core.database import get_brain_collection
from app.core.config import settings
from app.core.security import encryption_service
from app.core.redis_queue import get_redis_connection
from app.team_poll.formatter import build_owner_summary_text, build_owner_status_text
from app.team_poll.llm_service import summarize_team_poll_responses
from app.team_poll.providers.slack import SlackTeamPollAdapter
from app.team_poll.session_store import (
    ENTITY_TYPE,
    RESULT_ENTITY_TYPE,
    list_poll_sessions,
)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _is_terminal(session: Dict[str, Any]) -> bool:
    return bool((session.get("outcome") or {}).get("terminal")) or str(session.get("status") or "").strip().lower() == "completed"


def _normalize_grouped_lines(raw: Any) -> List[str]:
    if isinstance(raw, str):
        value = str(raw).strip()
        return [value] if value else []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


async def build_poll_status(
    db,
    *,
    project_id: str,
    poll_id: str,
) -> Optional[Dict[str, Any]]:
    sessions = await list_poll_sessions(db, project_id=project_id, poll_id=poll_id)
    if not sessions:
        return None
    question_text = str(sessions[0].get("question_text") or "").strip()
    responded = 0
    pending = 0
    skipped_owner = 0
    deferred_by_cadence = 0
    blocked_by_cadence = 0
    total_nudges_sent = 0
    last_nudged_at_dt: Optional[datetime] = None
    now = _utcnow()
    soonest_due_at: Optional[datetime] = None
    for session in sessions:
        response_status = str((session.get("outcome") or {}).get("response_status") or "").strip().lower()
        delivery_status = str((session.get("outcome") or {}).get("delivery_status") or "").strip().lower()
        if delivery_status == "deferred_by_active_cadence":
            deferred_by_cadence += 1
        if delivery_status == "blocked_by_active_cadence_deadline":
            blocked_by_cadence += 1
        if response_status == "responded":
            responded += 1
        elif response_status == "skipped_by_owner":
            skipped_owner += 1
        else:
            pending += 1
        total_nudges_sent += int(session.get("nudge_count") or 0)
        last_nudged_at = session.get("last_nudged_at")
        if isinstance(last_nudged_at, datetime):
            if last_nudged_at_dt is None or last_nudged_at > last_nudged_at_dt:
                last_nudged_at_dt = last_nudged_at
        member_due_at = session.get("member_due_at") or session.get("deadline_at") or session.get("expires_at")
        if isinstance(member_due_at, datetime) and str(session.get("status") or "").strip().lower() != "completed":
            if soonest_due_at is None or member_due_at < soonest_due_at:
                soonest_due_at = member_due_at

    if soonest_due_at is None:
        time_remaining = "completed"
    elif soonest_due_at <= now:
        time_remaining = "expired"
    else:
        remaining = soonest_due_at - now
        total_minutes = max(1, int(remaining.total_seconds() // 60))
        hours = total_minutes // 60
        minutes = total_minutes % 60
        time_remaining = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

    last_nudged_at_text = last_nudged_at_dt.isoformat() if isinstance(last_nudged_at_dt, datetime) else "never"
    return {
        "poll_id": poll_id,
        "question_text": question_text,
        "responded": responded,
        "pending": pending,
        "skipped_owner": skipped_owner,
        "deferred_by_cadence": deferred_by_cadence,
        "blocked_by_cadence": blocked_by_cadence,
        "total_nudges_sent": total_nudges_sent,
        "last_nudged_at": last_nudged_at_text,
        "time_remaining": time_remaining,
        "text": build_owner_status_text(
            question_text=question_text,
            responded=responded,
            pending=pending,
            skipped_owner=skipped_owner,
            deferred_by_cadence=deferred_by_cadence,
            blocked_by_cadence=blocked_by_cadence,
            total_nudges_sent=total_nudges_sent,
            last_nudged_at=last_nudged_at_text,
            time_remaining=time_remaining,
            poll_id=poll_id,
        ),
    }


async def finalize_poll_if_ready(
    db,
    *,
    project: Dict[str, Any],
    poll_id: str,
    force: bool = False,
) -> Dict[str, Any]:
    project_id = str(project.get("project_id") or "").strip()
    if not project_id:
        return {"ok": False, "reason": "missing_project_id"}

    # Pre-lock readiness check — avoids holding the lock on early "not_ready" exits,
    # which would block the last member's finalize call for the full TTL duration.
    sessions = await list_poll_sessions(db, project_id=project_id, poll_id=poll_id)
    if not sessions:
        return {"ok": False, "reason": "poll_not_found"}

    terminal_count = sum(1 for item in sessions if _is_terminal(item))
    if not force and terminal_count < len(sessions):
        return {"ok": False, "reason": "not_ready", "terminal_count": terminal_count, "total": len(sessions)}

    # Acquire the lock only once all sessions are confirmed terminal.
    lock_key = f"team_poll:finalize:{project_id}:{poll_id}"
    if not _try_acquire_lock(lock_key, ttl_seconds=60):
        return {"ok": False, "reason": "finalize_locked"}

    try:
        # Re-check under lock to guard against concurrent callers who both passed
        # the pre-lock readiness check before either acquired the lock.
        sessions = await list_poll_sessions(db, project_id=project_id, poll_id=poll_id)
        if not sessions:
            return {"ok": False, "reason": "poll_not_found"}

        terminal_count = sum(1 for item in sessions if _is_terminal(item))
        if not force and terminal_count < len(sessions):
            return {"ok": False, "reason": "not_ready", "terminal_count": terminal_count, "total": len(sessions)}

        collection = get_brain_collection(project_id)
        existing = await collection.find_one({"entity_type": RESULT_ENTITY_TYPE, "poll_id": poll_id})
        if existing:
            return {"ok": True, "reason": "already_finalized"}

        question_text = str(sessions[0].get("question_text") or "").strip()
        owner_user_id = str(sessions[0].get("owner_user_id") or "").strip()
        responses: List[str] = []
        responded_members: List[str] = []
        pending_members: List[str] = []
        skipped_owner_members: List[str] = []
        delivery_failed_members: List[str] = []
        blocked_by_cadence_members: List[str] = []
        for session in sessions:
            member_name = str(session.get("member_name") or session.get("member_email") or "unknown").strip()
            response_status = str((session.get("outcome") or {}).get("response_status") or "").strip().lower()
            delivery_status = str((session.get("outcome") or {}).get("delivery_status") or "").strip().lower()
            if response_status == "responded":
                responded_members.append(member_name)
                response_text = str(((session.get("response_payload") or {}).get("text")) or "").strip()
                if response_text:
                    responses.append(f"{member_name}: {response_text}")
            elif response_status == "skipped_by_owner":
                skipped_owner_members.append(member_name)
            elif delivery_status in {"failed", "not_sent", "missing_identity_mapping"}:
                delivery_failed_members.append(member_name)
            elif delivery_status == "blocked_by_active_cadence_deadline":
                blocked_by_cadence_members.append(member_name)
            else:
                pending_members.append(member_name)

        llm_summary_payload = await summarize_team_poll_responses(
            question_text=question_text,
            responses=responses,
        )
        heading = str((llm_summary_payload or {}).get("heading") or question_text).strip() or question_text
        grouped_lines = _normalize_grouped_lines((llm_summary_payload or {}).get("grouped_lines") or [])
        summary_text = build_owner_summary_text(
            heading=heading,
            responded_count=len(responded_members),
            total_count=len(sessions),
            responded_members=responded_members,
            pending_members=pending_members,
            grouped_summary_lines=grouped_lines,
        )

        now = _utcnow()
        retention_days = max(1, int(getattr(settings, "team_poll_result_retention_days", 7) or 7))
        result_doc = {
            "entity_type": RESULT_ENTITY_TYPE,
            "schema_version": "1.0",
            "project_id": project_id,
            "poll_id": poll_id,
            "correlation_id": str(sessions[0].get("correlation_id") or "").strip() or None,
            "owner_user_id": owner_user_id,
            "question_text": question_text,
            "responded_count": len(responded_members),
            "pending_count": len(pending_members),
            "delivery_failed_count": len(delivery_failed_members),
            "blocked_by_cadence_count": len(blocked_by_cadence_members),
            "owner_skipped_count": len(skipped_owner_members),
            "responses": responses,
            "non_responders": pending_members,
            "delivery_failures": delivery_failed_members,
            "blocked_by_cadence": blocked_by_cadence_members,
            "owner_skipped": skipped_owner_members,
            "summary": {
                "heading": heading,
                "grouped_lines": grouped_lines,
                "source": str((llm_summary_payload or {}).get("source") or "deterministic"),
                "model": (llm_summary_payload or {}).get("model"),
            },
            "generated_at": now,
            "owner_notified": False,
            "retention_delete_at": now + timedelta(days=retention_days),
        }
        await collection.insert_one(result_doc)

        notified = await _send_owner_summary(project=project, owner_user_id=owner_user_id, text=summary_text)
        if notified:
            await collection.update_one(
                {"entity_type": RESULT_ENTITY_TYPE, "poll_id": poll_id},
                {"$set": {"owner_notified": True, "owner_notified_at": _utcnow()}},
            )
        try:
            from app.team_poll.scheduler_hooks import reconcile_team_poll_schedule
            await reconcile_team_poll_schedule(db, project_id=project_id)
        except Exception as reconcile_exc:
            print(
                "team_poll_schedule_reconcile_failed_post_finalize: "
                f"project_id={project_id} poll_id={poll_id} error={reconcile_exc}"
            )
        return {"ok": True, "reason": "finalized", "owner_notified": notified}
    finally:
        _release_lock(lock_key)


async def _send_owner_summary(*, project: Dict[str, Any], owner_user_id: str, text: str) -> bool:
    slack_info = ((project.get("integrations") or {}).get("slack") or {})
    encrypted = slack_info.get("access_token")
    if not encrypted:
        return False
    try:
        token = encryption_service.decrypt(encrypted)
    except Exception:
        return False
    members = [item for item in (project.get("members") or []) if isinstance(item, dict)]
    member_lookup = {str(item.get("user_id") or "").strip(): item for item in members}
    owner_member = member_lookup.get(owner_user_id)
    if not isinstance(owner_member, dict):
        owner_member = next(
            (
                item
                for item in members
                if str(item.get("email") or "").strip().lower() == str(owner_user_id or "").strip().lower()
            ),
            None,
        )
    owner_slack_id = str(((owner_member or {}).get("integration_ids") or {}).get("slack_user_id") or "").strip()
    if not owner_slack_id:
        return False
    adapter = SlackTeamPollAdapter(token)
    workspace_id = str(((project.get("integrations") or {}).get("slack") or {}).get("team_id") or "").strip() or None
    return await adapter.send_owner_summary(
        owner_external_user_id=owner_slack_id,
        text=text,
        workspace_id=workspace_id,
    )


def _try_acquire_lock(key: str, *, ttl_seconds: int) -> bool:
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return True
    try:
        created = redis_conn.set(key, b"1", ex=max(5, int(ttl_seconds)), nx=True)
        return bool(created)
    except Exception:
        return True


def _release_lock(key: str) -> None:
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    try:
        redis_conn.delete(key)
    except Exception:
        pass
