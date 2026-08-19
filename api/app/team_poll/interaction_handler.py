"""Inbound Team Poll reply handling."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.core.database import get_database
from app.core.security import encryption_service
from app.integrations.slack.project_resolver import resolve_slack_project
from app.team_poll.schema import is_owner_skip_text
from app.team_poll.schema import normalize_response_for_mode
from app.team_poll.schema import build_member_prompt_text
from app.team_poll.response_writer import compose_team_poll_response
from app.team_poll.relevance import evaluate_pending_poll_relevance
from app.team_poll.session_store import (
    get_session,
    mark_owner_skipped,
    mark_session_responded,
)
from app.team_poll.summary import finalize_poll_if_ready
from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)


async def handle_team_poll_dm(
    db,
    *,
    session: Dict[str, Any],
    text: str,
    slack_client: Any,
    channel: str,
) -> bool:
    session_id = str(session.get("session_id") or "").strip()
    project_id = str(session.get("project_id") or "").strip()
    poll_id = str(session.get("poll_id") or "").strip()
    owner_user_id = str(session.get("owner_user_id") or "").strip()
    actor_user_id = str(session.get("user_id") or "").strip()

    if not session_id or not project_id or not poll_id:
        return False

    if str(session.get("status") or "").strip().lower() == "completed":
        return True

    if owner_user_id and owner_user_id == actor_user_id:
        from app.team_poll.orchestrator import classify_owner_free_text_intent, get_owner_poll_status, close_owner_poll

        owner_intent = classify_owner_free_text_intent(text)
        if owner_intent == "status":
            project = await db.projects.find_one({"project_id": project_id})
            if isinstance(project, dict):
                status_result = await get_owner_poll_status(
                    db,
                    project=project,
                    owner_user_id=owner_user_id,
                    poll_id=poll_id,
                )
                await _safe_send(
                    slack_client,
                    channel=channel,
                    text=await compose_team_poll_response(
                        action="status_success" if status_result.get("ok") else "status_error",
                        user_message=text,
                        outcome_payload=status_result,
                        fallback_text=(
                            str((status_result.get("status") or {}).get("text") or "")
                            if status_result.get("ok")
                            else "Unable to fetch poll status."
                        ),
                    ),
                )
                return True
        if owner_intent == "close":
            project = await db.projects.find_one({"project_id": project_id})
            if isinstance(project, dict):
                close_result = await close_owner_poll(
                    db,
                    project=project,
                    owner_user_id=owner_user_id,
                    poll_id=poll_id,
                )
                await _safe_send(
                    slack_client,
                    channel=channel,
                    text=await compose_team_poll_response(
                        action="close_success" if close_result.get("ok") else "close_error",
                        user_message=text,
                        outcome_payload=close_result,
                        fallback_text=(
                            f"Team poll closed. Poll ID: {close_result.get('poll_id')}"
                            if close_result.get("ok")
                            else "Unable to close poll right now."
                        ),
                    ),
                )
                return True

    if owner_user_id and owner_user_id == actor_user_id and is_owner_skip_text(text):
        await mark_owner_skipped(db, session_id=session_id, project_id=project_id)
        await _safe_send(
            slack_client,
            channel=channel,
            text=await compose_team_poll_response(
                action="owner_skip_success",
                user_message=text,
                outcome_payload={"ok": True, "poll_id": poll_id},
                fallback_text="Your response was skipped for this poll.",
            ),
        )
    else:
        response_format = str(session.get("response_format") or "free_text").strip().lower()
        options = [str(item).strip() for item in (session.get("options") or []) if str(item).strip()]
        is_valid, normalized_response = normalize_response_for_mode(
            response_format=response_format,
            text=text,
            options=options,
        )
        if not is_valid:
            await _safe_send(
                slack_client,
                channel=channel,
                text=await compose_team_poll_response(
                    action="member_validation_error",
                    user_message=text,
                    outcome_payload={
                        "ok": False,
                        "response_format": response_format,
                        "options": options,
                    },
                    fallback_text=_build_response_format_error(response_format=response_format, options=options),
                ),
            )
            return True
        if response_format == "free_text":
            relevance = await evaluate_pending_poll_relevance(
                question_text=str(session.get("question_text") or "").strip(),
                reply_text=text,
                poll_id=poll_id,
                is_owner=bool(owner_user_id and owner_user_id == actor_user_id),
            )
            logger.info(
                "team_poll_relevance_decision session_id=%s project_id=%s action=%s score=%.4f reason=%s path=%s",
                session_id,
                project_id,
                relevance.action,
                relevance.score,
                relevance.reason,
                relevance.path,
            )
            if relevance.action == "pass_to_cortex":
                return False
            if relevance.action == "remind_pending_poll":
                prompt_text = build_member_prompt_text(
                    member_name=str(session.get("member_name") or "there"),
                    project_name=str(session.get("project_name") or "your project"),
                    question_text=str(session.get("question_text") or "").strip(),
                    owner_prompt=bool(owner_user_id and owner_user_id == actor_user_id),
                )
                await _safe_send(
                    slack_client,
                    channel=channel,
                    text=await compose_team_poll_response(
                        action="member_pending_poll_reminder",
                        user_message=text,
                        outcome_payload={
                            "ok": False,
                            "poll_id": poll_id,
                            "reason": relevance.reason,
                            "score": relevance.score,
                        },
                        fallback_text=(
                            "You have a pending team poll response. "
                            "Please answer the poll question below first.\n\n"
                            f"{prompt_text}"
                        ),
                    ),
                )
                return True
        await mark_session_responded(
            db,
            session_id=session_id,
            response_text=text,
            project_id=project_id,
            normalized_response=normalized_response,
        )
        await _safe_send(
            slack_client,
            channel=channel,
            text=await compose_team_poll_response(
                action="member_response_recorded",
                user_message=text,
                outcome_payload={"ok": True, "poll_id": poll_id},
                fallback_text="Thanks, your response has been recorded.",
            ),
        )

    refreshed = await get_session(db, session_id=session_id, project_id=project_id)
    if not isinstance(refreshed, dict):
        return True

    project = await db.projects.find_one({"project_id": project_id})
    if isinstance(project, dict):
        await finalize_poll_if_ready(
            db,
            project=project,
            poll_id=poll_id,
            force=False,
        )
    return True


async def handle_owner_skip_button_action(payload: Dict[str, Any]) -> bool:
    actions = payload.get("actions") or []
    if not actions:
        return False
    value = str(actions[0].get("value") or "").strip()
    parts = value.split("|")
    if len(parts) < 3:
        return False
    session_id, poll_id, project_id = parts[0], parts[1], parts[2]
    db = get_database()
    session = await get_session(db, session_id=session_id, project_id=project_id)
    if not isinstance(session, dict):
        return False
    await mark_owner_skipped(db, session_id=session_id, project_id=project_id)

    team_id = str(payload.get("team", {}).get("id") or "").strip()
    channel_id = str(payload.get("channel", {}).get("id") or "").strip()
    slack_user_id = str(payload.get("user", {}).get("id") or "").strip()
    if not team_id or not channel_id or not slack_user_id:
        return True

    resolution = await resolve_slack_project(
        db=db,
        team_id=team_id,
        slack_user_id=slack_user_id,
        channel_id=channel_id,
    )
    project = resolution.project if resolution.ok else await db.projects.find_one({"project_id": project_id})
    if isinstance(project, dict):
        try:
            slack_token_encrypted = (((project.get("integrations") or {}).get("slack") or {}).get("access_token"))
            if slack_token_encrypted:
                slack_token = encryption_service.decrypt(slack_token_encrypted)
                slack_client = AsyncWebClient(token=slack_token)
                confirmation_text = await compose_team_poll_response(
                    action="owner_skip_success",
                    user_message="skip",
                    outcome_payload={"ok": True, "poll_id": poll_id},
                    fallback_text="Your response was skipped for this poll.",
                )
                await slack_client.chat_postMessage(
                    channel=channel_id,
                    text=confirmation_text,
                )
        except Exception as notify_exc:
            print(f"Team poll owner skip acknowledgement failed: {notify_exc}")
    if isinstance(project, dict):
        await finalize_poll_if_ready(
            db,
            project=project,
            poll_id=poll_id,
            force=False,
        )
    return True


async def _safe_send(slack_client: Any, *, channel: str, text: str) -> None:
    if not slack_client:
        return
    try:
        await slack_client.chat_postMessage(channel=channel, text=text)
    except Exception:
        return


def _build_response_format_error(*, response_format: str, options: list[str]) -> str:
    mode = str(response_format or "free_text").strip().lower()
    if mode == "yes_no":
        return "Please respond with yes or no."
    if mode == "single_choice":
        if options:
            return "Please choose one option: " + ", ".join(options)
        return "Please choose one valid option."
    if mode == "multi_choice":
        if options:
            return "Please choose one or more options (comma-separated): " + ", ".join(options)
        return "Please choose valid options (comma-separated)."
    if mode == "scale":
        return "Please respond with a number from 1 to 5."
    return "Please share a valid response for this poll."
