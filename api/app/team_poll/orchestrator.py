"""Team poll orchestration and intent classification."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.security import encryption_service
from app.core.redis_queue import get_redis_connection
from app.team_poll.formatter import build_poll_prompt
from app.team_poll.schema import (
    normalize_question_text,
    parse_owner_question_and_mode,
    compute_deadline,
    compute_eod_deadline,
)
from app.team_poll.question_rewriter import resolve_poll_question
from app.team_poll.session_store import (
    create_poll_sessions,
    list_owner_active_sessions,
    mark_poll_closed_for_pending,
)
from app.team_poll.summary import build_poll_status, finalize_poll_if_ready
from app.team_poll.scheduler_hooks import reconcile_team_poll_schedule
from app.team_poll.providers.slack import SlackTeamPollAdapter


_CREATE_INTENT_RE = re.compile(
    r"\b(?:ask|check|collect|run|start)\b.*\b(?:team|everyone|members)\b",
    re.IGNORECASE,
)
_STATUS_INTENT_RE = re.compile(r"\b(?:status|update|progress)\b.*\b(?:poll|check|team)\b", re.IGNORECASE)
_CLOSE_INTENT_RE = re.compile(r"\b(?:close|end|stop)\b.*\b(?:poll|check)\b", re.IGNORECASE)


def classify_owner_free_text_intent(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return "none"
    if _STATUS_INTENT_RE.search(normalized):
        return "status"
    if _CLOSE_INTENT_RE.search(normalized):
        return "close"
    if _CREATE_INTENT_RE.search(normalized):
        return "create"
    return "none"


def extract_poll_question(text: str) -> str:
    return normalize_question_text(text)


def classify_owner_free_text_intent_with_confidence(text: str) -> Dict[str, Any]:
    normalized = str(text or "").strip()
    if not normalized:
        return {"intent": "none", "confidence": 0.0}
    intent = classify_owner_free_text_intent(normalized)
    confidence = 0.0
    if intent == "create":
        confidence = 0.85 if re.search(r"\b(team|everyone|members)\b", normalized, flags=re.IGNORECASE) else 0.55
    elif intent in {"status", "close"}:
        confidence = 0.8
    return {"intent": intent, "confidence": confidence}


async def create_team_poll(
    db,
    *,
    project: Dict[str, Any],
    owner_member: Dict[str, Any],
    question_text: str,
    workspace_id: str,
) -> Dict[str, Any]:
    if not bool(getattr(settings, "team_poll_enabled", True)):
        return {"ok": False, "reason": "disabled"}

    project_id = str(project.get("project_id") or "").strip()
    project_name = str(project.get("project_name") or project_id).strip()
    project_timezone = str(project.get("timezone") or "UTC").strip() or "UTC"
    if not project_id:
        return {"ok": False, "reason": "missing_project_id"}

    normalized_question, mode = parse_owner_question_and_mode(question_text)
    if not normalized_question:
        return {"ok": False, "reason": "missing_question"}
    resolved_question = await resolve_poll_question(
        owner_text=question_text,
        parsed_question=normalized_question,
        parsed_mode=mode,
        project_name=project_name,
        project_timezone=project_timezone,
    )
    final_question = str(resolved_question.get("question_text") or "").strip()
    if not final_question:
        return {"ok": False, "reason": "missing_question"}
    response_format = str(mode.get("response_format") or "free_text").strip().lower()
    options = [str(item).strip() for item in (mode.get("options") or []) if str(item).strip()]
    requires_comment = bool(mode.get("requires_comment"))
    deadline_mode = str(mode.get("deadline_mode") or "default").strip().lower()
    deadline_at = (
        compute_eod_deadline(timezone_name=project_timezone)
        if deadline_mode == "eod"
        else compute_deadline()
    )

    members = [
        item
        for item in (project.get("members") or [])
        if isinstance(item, dict) and str(item.get("status") or "").strip().lower() == "active"
    ]
    max_audience = max(1, int(getattr(settings, "team_poll_max_audience", 200) or 200))
    if len(members) > max_audience:
        return {"ok": False, "reason": "audience_too_large", "max_audience": max_audience}
    if not members:
        return {"ok": False, "reason": "no_active_members"}

    dedupe_key = _build_create_dedupe_key(
        project_id=project_id,
        owner_user_id=str(owner_member.get("user_id") or owner_member.get("email") or "").strip(),
        question_text=final_question,
        deadline_at=deadline_at,
    )
    acquired = _try_acquire_dedupe_slot(dedupe_key, ttl_seconds=120)
    if not acquired:
        return {"ok": False, "reason": "duplicate_create_inflight"}

    created = await create_poll_sessions(
        db,
        project_id=project_id,
        project_name=project_name,
        owner_member=owner_member,
        members=members,
        question_text=final_question,
        question_text_original=str(resolved_question.get("original_question_text") or normalized_question).strip(),
        question_rewrite_source=str(resolved_question.get("source") or "deterministic").strip().lower(),
        question_rewrite_confidence=float(resolved_question.get("confidence") or 1.0),
        question_rewrite_model=resolved_question.get("model"),
        workspace_id=workspace_id,
        response_format=response_format,
        options=options,
        requires_comment=requires_comment,
        deadline_at=deadline_at,
    )
    poll_id = str(created.get("poll_id") or "").strip()
    if not poll_id:
        return {"ok": False, "reason": "create_failed"}

    delivery = await _deliver_poll_prompts(
        project=project,
        poll_id=poll_id,
        owner_user_id=str(owner_member.get("user_id") or owner_member.get("email") or "").strip(),
    )
    try:
        await reconcile_team_poll_schedule(db, project_id=project_id)
    except Exception as schedule_exc:
        print(f"team_poll_schedule_reconcile_failed: project_id={project_id} error={schedule_exc}")

    return {
        "ok": True,
        "poll_id": poll_id,
        "question_text": final_question,
        "response_format": response_format,
        "delivery": delivery,
    }


async def get_owner_poll_status(
    db,
    *,
    project: Dict[str, Any],
    owner_user_id: str,
    poll_id: Optional[str] = None,
) -> Dict[str, Any]:
    project_id = str(project.get("project_id") or "").strip()
    resolved_poll_id = str(poll_id or "").strip()
    if not resolved_poll_id:
        owner_sessions = await list_owner_active_sessions(
            db,
            project_id=project_id,
            owner_user_id=owner_user_id,
        )
        if not owner_sessions:
            return {"ok": False, "reason": "no_active_poll"}
        unique_ids = sorted({str(item.get("poll_id") or "").strip() for item in owner_sessions if str(item.get("poll_id") or "").strip()})
        if len(unique_ids) > 1:
            return {"ok": False, "reason": "ambiguous_poll", "candidates": unique_ids}
        resolved_poll_id = unique_ids[0] if unique_ids else ""
    if not resolved_poll_id:
        return {"ok": False, "reason": "no_active_poll"}
    status_payload = await build_poll_status(db, project_id=project_id, poll_id=resolved_poll_id)
    if not status_payload:
        return {"ok": False, "reason": "poll_not_found"}
    return {"ok": True, "poll_id": resolved_poll_id, "status": status_payload}


async def close_owner_poll(
    db,
    *,
    project: Dict[str, Any],
    owner_user_id: str,
    poll_id: Optional[str] = None,
) -> Dict[str, Any]:
    project_id = str(project.get("project_id") or "").strip()
    resolved_poll_id = str(poll_id or "").strip()
    if not resolved_poll_id:
        owner_sessions = await list_owner_active_sessions(
            db,
            project_id=project_id,
            owner_user_id=owner_user_id,
        )
        if not owner_sessions:
            return {"ok": False, "reason": "no_active_poll"}
        unique_ids = sorted({str(item.get("poll_id") or "").strip() for item in owner_sessions if str(item.get("poll_id") or "").strip()})
        if len(unique_ids) > 1:
            return {"ok": False, "reason": "ambiguous_poll", "candidates": unique_ids}
        resolved_poll_id = unique_ids[0] if unique_ids else ""
    if not resolved_poll_id:
        return {"ok": False, "reason": "no_active_poll"}
    closed_count = await mark_poll_closed_for_pending(
        db,
        project_id=project_id,
        poll_id=resolved_poll_id,
        reason="closed_by_owner",
    )
    finalized = await finalize_poll_if_ready(
        db,
        project=project,
        poll_id=resolved_poll_id,
        force=True,
    )
    return {"ok": True, "poll_id": resolved_poll_id, "closed_count": closed_count, "finalized": finalized}


async def _deliver_poll_prompts(
    *,
    project: Dict[str, Any],
    poll_id: str,
    owner_user_id: str,
) -> Dict[str, Any]:
    project_id = str(project.get("project_id") or "").strip()
    from app.core.database import get_brain_collection

    collection = get_brain_collection(project_id)
    sessions = await collection.find(
        {"entity_type": "team_poll_session", "source": "team_poll", "poll_id": poll_id}
    ).to_list(length=1000)

    encrypted = (((project.get("integrations") or {}).get("slack") or {}).get("access_token"))
    if not encrypted:
        return {"sent": 0, "failed": len(sessions), "reason": "missing_slack_token"}
    try:
        token = encryption_service.decrypt(encrypted)
    except Exception:
        return {"sent": 0, "failed": len(sessions), "reason": "slack_token_decrypt_failed"}

    adapter = SlackTeamPollAdapter(token)
    sent = 0
    failed = 0
    for session in sessions:
        if str((session.get("outcome") or {}).get("delivery_status") or "").strip().lower() == "deferred_by_active_cadence":
            continue
        member_slack_id = str(session.get("external_user_id") or "").strip()
        if not member_slack_id:
            failed += 1
            continue
        is_owner_prompt = str(session.get("user_id") or "").strip() == str(owner_user_id or "").strip()
        prompt = build_poll_prompt(session=session, owner_prompt=is_owner_prompt)
        ok = await adapter.send_prompt(
            target_external_user_id=member_slack_id,
            payload=prompt,
            workspace_id=str(session.get("workspace_id") or "").strip() or None,
        )
        if ok:
            sent += 1
            await collection.update_one(
                {"_id": session.get("_id")},
                {"$set": {"outcome.delivery_status": "sent", "updated_at": datetime.utcnow()}},
            )
        else:
            failed += 1
            await collection.update_one(
                {"_id": session.get("_id")},
                {"$set": {"outcome.delivery_status": "failed", "updated_at": datetime.utcnow()}},
            )
    return {"sent": sent, "failed": failed}


def _build_create_dedupe_key(*, project_id: str, owner_user_id: str, question_text: str, deadline_at: datetime) -> str:
    canonical = re.sub(r"\s+", " ", str(question_text or "").strip().lower())
    bucket = int(deadline_at.timestamp() // 300)
    return f"team_poll:create:{project_id}:{owner_user_id}:{canonical}:{bucket}"


def _try_acquire_dedupe_slot(key: str, *, ttl_seconds: int) -> bool:
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return True
    try:
        created = redis_conn.set(key, b"1", nx=True, ex=max(5, int(ttl_seconds)))
        return bool(created)
    except Exception:
        return True
