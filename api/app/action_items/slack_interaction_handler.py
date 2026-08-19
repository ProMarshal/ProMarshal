"""Slack interaction handlers for action-item digest quick actions."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import httpx

from app.action_items.service import ActionItemService
from app.core.database import get_database
from app.integrations.slack.assignment_notifications import (
    resolve_project_member_for_slack_actor,
)


ACTION_ITEM_DIGEST_DONE_ACTION_ID = "action_item_digest_mark_done"
ACTION_ITEM_DIGEST_CANCEL_ACTION_ID = "action_item_digest_mark_cancelled"

ACTION_ITEM_DIGEST_ACTION_IDS = {
    ACTION_ITEM_DIGEST_DONE_ACTION_ID,
    ACTION_ITEM_DIGEST_CANCEL_ACTION_ID,
}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_parse_action_value(raw_value: Any) -> Dict[str, Any]:
    value = _normalize_text(raw_value)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _post_ephemeral_response(payload: Dict[str, Any], text: str) -> None:
    response_url = _normalize_text(payload.get("response_url"))
    if not response_url:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                response_url,
                json={
                    "response_type": "ephemeral",
                    "replace_original": False,
                    "text": text,
                },
            )
    except Exception:
        # Non-blocking feedback path; action completion is authoritative.
        return


async def handle_action_item_digest_block_action(payload: Dict[str, Any]) -> None:
    """Handle digest quick actions: done, cancelled, and open-list hint."""
    actions = payload.get("actions") or []
    action = actions[0] if actions and isinstance(actions[0], dict) else {}
    action_id = _normalize_text(action.get("action_id"))
    if action_id not in ACTION_ITEM_DIGEST_ACTION_IDS:
        return

    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    slack_user_id = _normalize_text(user.get("id"))
    team = payload.get("team") if isinstance(payload.get("team"), dict) else {}
    team_id = _normalize_text(team.get("id"))

    value = _safe_parse_action_value(action.get("value"))
    value_project_id = _normalize_text(value.get("project_id"))
    action_item_id = _normalize_text(value.get("action_item_id"))
    if not value_project_id or not action_item_id:
        await _post_ephemeral_response(payload, "Action payload is invalid.")
        return

    project, member, _slack_client = await resolve_project_member_for_slack_actor(
        db=get_database(),
        project_id=value_project_id,
        team_id=team_id,
        slack_user_id=slack_user_id,
    )
    if not project or not member:
        await _post_ephemeral_response(
            payload,
            "Could not verify your project membership for this action. Please use `/promarshal actionitem list`.",
        )
        return

    project_id = _normalize_text(project.get("project_id"))
    actor_user_id = _normalize_text(member.get("user_id"))
    if not actor_user_id:
        await _post_ephemeral_response(
            payload,
            "Could not resolve your ProMarshal user id for this action.",
        )
        return

    if not project_id or not action_item_id:
        await _post_ephemeral_response(payload, "Action payload is invalid.")
        return

    close_status = "done" if action_id == ACTION_ITEM_DIGEST_DONE_ACTION_ID else "cancelled"
    try:
        updated = await ActionItemService.close(
            project_id,
            action_item_id,
            {"user_id": actor_user_id, "status": close_status},
        )
        display_key = _normalize_text(updated.get("display_key")) or action_item_id
        await _post_ephemeral_response(payload, f"Updated {display_key} as {close_status}.")
    except Exception as exc:
        await _post_ephemeral_response(payload, f"Action item update failed: {exc}")
