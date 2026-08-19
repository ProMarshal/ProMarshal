"""Session store for Team Poll sessions."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import uuid

from app.sessions.repository import SessionRepository
from app.core.config import settings
from app.team_poll.schema import (
    compute_deadline,
    compute_member_due_at,
    parse_minutes_csv,
    session_outcome,
)
from app.core.database import get_brain_collection
from app.core.pending_interactions import (
    create_pending_interaction,
    delete_pending_interactions_by_context,
)

try:
    from motor.motor_asyncio import AsyncIOMotorDatabase
except Exception:  # pragma: no cover
    AsyncIOMotorDatabase = Any  # type: ignore[misc,assignment]


SOURCE = "team_poll"
ENTITY_TYPE = "team_poll_session"
RESULT_ENTITY_TYPE = "team_poll_result"


def _repo(db: AsyncIOMotorDatabase) -> SessionRepository:
    return SessionRepository(db=db)


def _utcnow() -> datetime:
    return datetime.utcnow()


async def create_poll_sessions(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    project_name: str,
    owner_member: Dict[str, Any],
    members: List[Dict[str, Any]],
    question_text: str,
    question_text_original: Optional[str] = None,
    question_rewrite_source: str = "deterministic",
    question_rewrite_confidence: float = 1.0,
    question_rewrite_model: Optional[str] = None,
    workspace_id: str,
    response_format: str = "free_text",
    options: Optional[List[str]] = None,
    requires_comment: bool = False,
    deadline_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = _utcnow()
    poll_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())
    deadline_at = deadline_at or compute_deadline(now=now)
    nudge_offsets = parse_minutes_csv(getattr(settings, "team_poll_nudge_offsets_minutes", "30,60"), default=[30, 60])
    expected_count = len(members)
    created = 0
    skipped_no_mapping = 0

    owner_email = str(owner_member.get("email") or "").strip().lower()
    owner_user_id = str(owner_member.get("user_id") or owner_email).strip()
    owner_name = str(owner_member.get("name") or "").strip()

    for member in members:
        member_email = str(member.get("email") or "").strip().lower()
        member_user_id = str(member.get("user_id") or member_email).strip()
        member_name = str(member.get("name") or member_email).strip()
        member_slack_id = str((member.get("integration_ids") or {}).get("slack_user_id") or "").strip()
        cadence_deferred = False
        if member_slack_id:
            try:
                from app.cadence.orchestrator import find_active_sessions_by_identity

                cadence_active = await find_active_sessions_by_identity(
                    db,
                    provider="slack",
                    workspace_id=workspace_id,
                    external_user_id=member_slack_id,
                    limit=1,
                )
                cadence_deferred = bool(cadence_active)
            except Exception:
                cadence_deferred = False
        owner_skip_allowed = bool(member_email and member_email == owner_email)
        session_id = str(uuid.uuid4())
        member_due_at = compute_member_due_at(delivery_time=now, deadline_at=deadline_at)
        delivery_status = "pending"
        response_status = "pending"
        reason_codes: List[str] = []
        next_nudge_at = None
        if member_slack_id and cadence_deferred:
            delivery_status = "deferred_by_active_cadence"
            response_status = "pending"
            reason_codes = ["deferred_by_active_cadence"]
        elif member_slack_id:
            if nudge_offsets:
                next_nudge_at = now + timedelta(minutes=max(1, int(nudge_offsets[0])))
                if next_nudge_at >= member_due_at:
                    next_nudge_at = None
        else:
            delivery_status = "not_sent"
            response_status = "missing_identity_mapping"
            reason_codes = ["slack_mapping_missing"]

        session_doc: Dict[str, Any] = {
            "session_id": session_id,
            "session_key": f"team-poll-{session_id}",
            "source": SOURCE,
            "project_id": project_id,
            "poll_id": poll_id,
            "project_name": project_name,
            "question_text": question_text,
            "response_format": str(response_format or "free_text").strip().lower(),
            "options": [str(item).strip() for item in (options or []) if str(item).strip()],
            "owner_user_id": owner_user_id,
            "user_id": member_user_id,
            "member_name": member_name,
            "provider": "slack",
            "workspace_id": workspace_id,
            "external_user_id": member_slack_id or None,
            "status": "awaiting_response",
            "response_payload": None,
            "correlation_id": correlation_id,
            "created_at": now,
            "updated_at": now,
            "expires_at": member_due_at,  # per-member deadline (single source of truth)
            "next_nudge_at": next_nudge_at,
            "nudge_count": 0,
            "last_nudged_at": None,
            "outcome": session_outcome(
                delivery_status=delivery_status,
                response_status=response_status,
                reason_codes=reason_codes,
                terminal=not bool(member_slack_id),
            ),
        }
        if not member_slack_id:
            session_doc["status"] = "completed"
            session_doc["completed_at"] = now
            skipped_no_mapping += 1

        await _repo(db).create_session(
            entity_type=ENTITY_TYPE,
            project_id=project_id,
            payload=session_doc,
        )
        if member_slack_id and session_doc.get("status") != "completed":
            try:
                await create_pending_interaction(
                    interaction_type="team_poll",
                    project_id=project_id,
                    target_user_id=member_user_id,
                    priority=100,
                    source="team_poll",
                    expires_at=member_due_at,
                    provider="slack",
                    workspace_id=workspace_id,
                    external_user_id=member_slack_id,
                    context_payload={
                        "session_id": session_id,
                        "poll_id": poll_id,
                        "project_id": project_id,
                    },
                )
            except Exception:
                pass
        created += 1

    return {
        "poll_id": poll_id,
        "correlation_id": correlation_id,
        "expected_count": expected_count,
        "created_count": created,
        "skipped_no_mapping": skipped_no_mapping,
        "deadline_at": deadline_at,
    }


async def get_session(db: AsyncIOMotorDatabase, session_id: str, *, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return await _repo(db).get_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        project_id=project_id,
    )


async def list_poll_sessions(db: AsyncIOMotorDatabase, *, project_id: str, poll_id: str) -> List[Dict[str, Any]]:
    rows = await _repo(db).list_session_index(
        query={
            "entity_type": ENTITY_TYPE,
            "source": SOURCE,
            "project_id": project_id,
            "poll_id": poll_id,
        },
        limit=1000,
    )
    sessions: List[Dict[str, Any]] = []
    for row in rows:
        session_id = str(row.get("session_id") or "").strip()
        if not session_id:
            continue
        doc = await get_session(db, session_id, project_id=project_id)
        if isinstance(doc, dict):
            sessions.append(doc)
    return sessions


async def list_owner_active_sessions(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    owner_user_id: str,
) -> List[Dict[str, Any]]:
    collection = get_brain_collection(project_id)
    sessions = await collection.find(
        {
            "entity_type": ENTITY_TYPE,
            "source": SOURCE,
            "project_id": project_id,
            "owner_user_id": owner_user_id,
            "status": {"$ne": "completed"},
        }
    ).sort([("created_at", -1)]).limit(200).to_list(length=200)
    sessions.sort(key=lambda item: item.get("created_at") or datetime.min, reverse=True)
    return sessions


async def mark_session_responded(
    db: AsyncIOMotorDatabase,
    *,
    session_id: str,
    response_text: str,
    project_id: Optional[str] = None,
    normalized_response: Optional[Dict[str, Any]] = None,
) -> bool:
    now = _utcnow()
    ok = await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        project_id=project_id,
        update_doc={
            "$set": {
                "status": "completed",
                "response_payload": {
                    "text": str(response_text or "").strip(),
                    "normalized": dict(normalized_response or {}),
                },
                "responded_at": now,
                "completed_at": now,
                "outcome": session_outcome(
                    delivery_status="sent",
                    response_status="responded",
                    reason_codes=["responded"],
                    terminal=True,
                ),
            }
        },
    )
    if ok:
        try:
            await delete_pending_interactions_by_context(
                project_id=str(project_id or ""),
                interaction_type="team_poll",
                context_key="session_id",
                context_value=session_id,
            )
        except Exception:
            pass
    return ok


async def mark_owner_skipped(
    db: AsyncIOMotorDatabase,
    *,
    session_id: str,
    project_id: Optional[str] = None,
) -> bool:
    now = _utcnow()
    ok = await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        project_id=project_id,
        update_doc={
            "$set": {
                "status": "completed",
                "response_payload": None,
                "responded_at": now,
                "completed_at": now,
                "outcome": session_outcome(
                    delivery_status="sent",
                    response_status="skipped_by_owner",
                    reason_codes=["skipped_by_owner"],
                    terminal=True,
                ),
            }
        },
    )
    if ok:
        try:
            await delete_pending_interactions_by_context(
                project_id=str(project_id or ""),
                interaction_type="team_poll",
                context_key="session_id",
                context_value=session_id,
            )
        except Exception:
            pass
    return ok


async def mark_poll_closed_for_pending(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    poll_id: str,
    reason: str = "closed_by_owner",
) -> int:
    now = _utcnow()
    sessions = await list_poll_sessions(db, project_id=project_id, poll_id=poll_id)
    updated = 0
    for session in sessions:
        if str(session.get("status") or "").strip().lower() == "completed":
            continue
        ok = await _repo(db).update_session_by_id(
            entity_type=ENTITY_TYPE,
            source=SOURCE,
            session_id=str(session.get("session_id") or ""),
            project_id=project_id,
            update_doc={
                "$set": {
                    "status": "completed",
                    "completed_at": now,
                    "outcome": session_outcome(
                        delivery_status=str((session.get("outcome") or {}).get("delivery_status") or "sent"),
                        response_status="no_response_expired",
                        reason_codes=[reason],
                        terminal=True,
                    ),
                }
            },
        )
        if ok:
            try:
                await delete_pending_interactions_by_context(
                    project_id=project_id,
                    interaction_type="team_poll",
                    context_key="session_id",
                    context_value=str(session.get("session_id") or ""),
                )
            except Exception:
                pass
            updated += 1
    return updated
