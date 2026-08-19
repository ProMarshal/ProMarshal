"""Post-cadence reminder hook for unresolved Team Poll pending responses."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.pending_interactions import list_pending_interactions_by_external_identity
from app.core.redis_queue import get_redis_connection
from app.team_poll.schema import build_member_prompt_text
from app.team_poll.session_store import get_session as get_team_poll_session

logger = logging.getLogger("app.team_poll.post_cadence_reminder")


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except Exception:
            return None
    return None


def _is_cadence_completed(cadence_session: Dict[str, Any]) -> bool:
    outcome = cadence_session.get("outcome") or {}
    response_status = _normalize_text((outcome or {}).get("response_status")).lower()
    return response_status == "responded"


def _effective_cooldown_seconds(*, remaining_poll_ttl_seconds: Optional[int]) -> Optional[int]:
    configured = max(60, int(getattr(settings, "team_poll_post_cadence_reminder_cooldown_seconds", 900) or 900))
    if remaining_poll_ttl_seconds is None:
        return configured
    if int(remaining_poll_ttl_seconds) <= 0:
        return None
    return max(1, min(configured, int(remaining_poll_ttl_seconds)))


async def _select_pending_candidate(
    db: Any,
    *,
    interactions: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for interaction in interactions:
        context_payload = interaction.get("context_payload") or {}
        session_id = _normalize_text(context_payload.get("session_id"))
        project_id = _normalize_text(context_payload.get("project_id")) or None
        if not session_id:
            continue
        session = await get_team_poll_session(db, session_id=session_id, project_id=project_id)
        if not isinstance(session, dict):
            continue
        if _normalize_text(session.get("status")).lower() == "completed":
            continue
        if not _normalize_text(session.get("question_text")):
            continue
        poll_id = _normalize_text(
            context_payload.get("poll_id")
            or session.get("poll_id")
        )
        created_at = _as_datetime(interaction.get("created_at")) or datetime.max.replace(tzinfo=UTC)
        candidates.append(
            {
                "interaction": interaction,
                "session": session,
                "poll_id": poll_id,
                "created_at": created_at,
            }
        )

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item["created_at"], _normalize_text(item.get("poll_id"))))
    if len(candidates) > 1:
        logger.info(
            "post_cadence_reminder_multiple_pending_polls=true selected_poll_id=%s candidate_count=%s",
            _normalize_text(candidates[0].get("poll_id")) or "unknown",
            len(candidates),
        )
    return candidates[0]


async def maybe_send_post_cadence_pending_poll_reminder(
    db: Any,
    *,
    cadence_session: Dict[str, Any],
    slack_user_id: str,
    slack_client: Any,
) -> bool:
    if not bool(getattr(settings, "team_poll_post_cadence_reminder_enabled", True)):
        return False
    if not isinstance(cadence_session, dict):
        return False
    if not _is_cadence_completed(cadence_session):
        return False

    workspace_id = _normalize_text(cadence_session.get("workspace_id"))
    external_user_id = _normalize_text(slack_user_id or cadence_session.get("external_user_id"))
    if not workspace_id or not external_user_id or not slack_client:
        return False

    interactions = await list_pending_interactions_by_external_identity(
        provider="slack",
        workspace_id=workspace_id,
        external_user_id=external_user_id,
        interaction_type="team_poll",
        limit=10,
    )
    if not interactions:
        return False

    selected = await _select_pending_candidate(db, interactions=interactions)
    if not selected:
        return False

    interaction = selected["interaction"]
    session = selected["session"]
    poll_id = _normalize_text(selected["poll_id"]) or "unknown"
    expires_at = _as_datetime(interaction.get("expires_at"))
    remaining_poll_ttl_seconds: Optional[int] = None
    if expires_at is not None:
        remaining_poll_ttl_seconds = int((expires_at - _utcnow()).total_seconds())
    cooldown_seconds = _effective_cooldown_seconds(remaining_poll_ttl_seconds=remaining_poll_ttl_seconds)
    if cooldown_seconds is None:
        logger.info(
            "post_cadence_reminder_skipped reason=poll_ttl_expired poll_id=%s",
            poll_id,
        )
        return False

    gate_key = f"team_poll:post_cadence_remind:{workspace_id}:{external_user_id}:{poll_id}"
    redis_conn = get_redis_connection()
    if redis_conn is not None:
        try:
            created = redis_conn.set(gate_key, b"1", ex=int(cooldown_seconds), nx=True)
            if not bool(created):
                return False
        except Exception as exc:
            logger.info("post_cadence_reminder_redis_unavailable fail_open=true error=%s", str(exc))

    owner_prompt = _normalize_text(session.get("owner_user_id")) == _normalize_text(session.get("user_id"))
    prompt_text = build_member_prompt_text(
        member_name=_normalize_text(session.get("member_name")) or "there",
        project_name=_normalize_text(session.get("project_name")) or "your project",
        question_text=_normalize_text(session.get("question_text")),
        owner_prompt=owner_prompt,
    )
    reminder_text = (
        "Cadence check-in complete. You still have one pending team poll response.\n\n"
        f"{prompt_text}"
    )
    try:
        await slack_client.send_message(external_user_id, text=reminder_text)
        return True
    except Exception as exc:
        logger.info("post_cadence_reminder_send_failed poll_id=%s error=%s", poll_id, str(exc))
        return False
