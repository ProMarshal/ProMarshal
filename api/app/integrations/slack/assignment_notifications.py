"""Slack assignment notification helpers for work items and action items."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

import httpx
from fastapi.responses import JSONResponse
from app.cadence.slack_identity import resolve_slack_user_id_for_member
from app.cadence.transition_fetcher import fetch_available_transitions
from app.core.database import get_brain_collection, get_database
from app.core.identity_resolver import resolve_project_member_identity
from app.core.redis_queue import get_redis_connection
from app.core.security import encryption_service
from app.integrations.slack.client import SlackClient

WORK_ITEM_ASSIGNMENT_OPEN_MODAL_ACTION_ID = "work_item_assignment_open_modal"
WORK_ITEM_ASSIGNMENT_UPDATE_CALLBACK_ID = "work_item_assignment_update_modal"
ACTION_ITEM_ASSIGNMENT_NOTIFICATION_TTL_SECONDS = 24 * 60 * 60

_SEEN_ASSIGNMENT_NOTIFICATION_KEYS: set[str] = set()


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_lookup_key(value: Any) -> str:
    return "".join(ch for ch in _normalize_text(value).lower() if ch.isalnum())


def _safe_parse_json_dict(value: Any) -> Dict[str, Any]:
    normalized = _normalize_text(value)
    if not normalized:
        return {}
    try:
        parsed = json.loads(normalized)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _active_members(project: Dict[str, Any]) -> list[Dict[str, Any]]:
    members = project.get("members") or []
    if not isinstance(members, list):
        return []
    return [
        member
        for member in members
        if isinstance(member, dict) and _normalize_text(member.get("status")).lower() == "active"
    ]


def _active_member_by_user_id(project: Dict[str, Any], user_id: str) -> Optional[Dict[str, Any]]:
    normalized_user_id = _normalize_text(user_id)
    if not normalized_user_id:
        return None
    for member in _active_members(project):
        if _normalize_text(member.get("user_id")) == normalized_user_id:
            return member
    return None


def _resolve_actor_user_id_from_hint(project: Dict[str, Any], actor_hint: Optional[str]) -> Optional[str]:
    normalized_hint = _normalize_text(actor_hint)
    if not normalized_hint:
        return None
    direct = _active_member_by_user_id(project, normalized_hint)
    if direct:
        return _normalize_text(direct.get("user_id")) or None
    resolution = resolve_project_member_identity(
        project,
        normalized_hint,
        actor_aliases_enabled=False,
    )
    if resolution.status == "resolved":
        return _normalize_text(resolution.promarshal_user_id) or None
    return None


def _member_matches_provider_value(member: Dict[str, Any], value: str) -> bool:
    normalized_value = _normalize_text(value)
    if not normalized_value:
        return False
    integration_ids = member.get("integration_ids") or {}
    if not isinstance(integration_ids, dict):
        return False
    for provider_value in integration_ids.values():
        if _normalize_text(provider_value) == normalized_value:
            return True
    return False


def _member_jira_account_id(member: Dict[str, Any]) -> str:
    integration_ids = member.get("integration_ids") or {}
    if not isinstance(integration_ids, dict):
        integration_ids = {}
    return _normalize_text(
        integration_ids.get("jira_account_id")
        or integration_ids.get("jira_user_id")
        or member.get("jira_account_id")
    )


def _resolve_project_member_by_jira_account_id(
    project: Dict[str, Any],
    account_id: str,
) -> Optional[Dict[str, Any]]:
    normalized_account_id = _normalize_text(account_id)
    if not normalized_account_id:
        return None
    matches = [
        member for member in _active_members(project)
        if _member_jira_account_id(member) == normalized_account_id
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _resolve_work_item_assignee_identity(
    project: Dict[str, Any],
    task_payload: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    payload = task_payload or {}
    provider = _normalize_text(
        payload.get("provider")
        or payload.get("integration_type")
        or payload.get("source")
    ).lower()
    assignee = payload.get("assignee") if isinstance(payload.get("assignee"), dict) else {}
    account_id = (
        _normalize_text(payload.get("assignee_account_id"))
        or _normalize_text((assignee or {}).get("account_id"))
    )
    email = _normalize_text(payload.get("assignee_email")) or _normalize_text((assignee or {}).get("email"))
    name = _normalize_text(payload.get("assignee_name")) or _normalize_text((assignee or {}).get("name"))

    if provider == "jira" and account_id:
        member = _resolve_project_member_by_jira_account_id(project, account_id)
        if member:
            user_id = _normalize_text(member.get("user_id")) or None
            return user_id, account_id, account_id
        return None, account_id, account_id

    members = _active_members(project)
    if account_id:
        matches = [member for member in members if _member_matches_provider_value(member, account_id)]
        if len(matches) == 1:
            user_id = _normalize_text(matches[0].get("user_id")) or None
            return user_id, user_id, account_id

    if email:
        matches = [member for member in members if _normalize_text(member.get("email")).lower() == email.lower()]
        if len(matches) == 1:
            user_id = _normalize_text(matches[0].get("user_id")) or None
            return user_id, user_id, email.lower()

    if name:
        exact_name_matches = [
            member for member in members if _normalize_lookup_key(member.get("name")) == _normalize_lookup_key(name)
        ]
        if len(exact_name_matches) == 1:
            user_id = _normalize_text(exact_name_matches[0].get("user_id")) or None
            return user_id, user_id, _normalize_lookup_key(name)
        prefix_name_matches = [
            member
            for member in members
            if _normalize_lookup_key(member.get("name")).startswith(_normalize_lookup_key(name))
        ]
        unique_prefix_ids = {_normalize_text(member.get("user_id")) for member in prefix_name_matches}
        if len(prefix_name_matches) == 1 and len(unique_prefix_ids) == 1:
            user_id = _normalize_text(prefix_name_matches[0].get("user_id")) or None
            return user_id, user_id, _normalize_lookup_key(name)

    fallback_identity = account_id or (email.lower() if email else None) or _normalize_lookup_key(name) or None
    display_label = email or name or account_id or None
    return None, fallback_identity, display_label


def _should_skip_assignment_notification(
    *,
    old_identity: Optional[str],
    new_identity: Optional[str],
    actor_user_id: Optional[str],
    new_user_id: Optional[str],
) -> bool:
    normalized_new = _normalize_text(new_identity)
    if not normalized_new:
        return True
    normalized_old = _normalize_text(old_identity)
    if normalized_old and normalized_old == normalized_new:
        return True
    normalized_actor = _normalize_text(actor_user_id)
    normalized_new_user = _normalize_text(new_user_id)
    if normalized_actor and normalized_new_user and normalized_actor == normalized_new_user:
        return True
    return False


def _assignment_dedupe_key(
    *,
    entity_type: str,
    project_id: str,
    item_ref: str,
    assignee_identity: str,
    version_token: str,
) -> str:
    return (
        "assignment_notify:v1:"
        f"{_normalize_text(entity_type)}:{_normalize_text(project_id)}:{_normalize_text(item_ref)}:"
        f"{_normalize_text(assignee_identity)}:{_normalize_text(version_token)}"
    )


def _work_item_version_token(task: Dict[str, Any]) -> str:
    return (
        _normalize_text(task.get("provider_updated_at"))
        or _normalize_text(task.get("source_updated_at"))
        or _normalize_text(task.get("updated_at"))
        or _normalize_text(task.get("created_at"))
        or "latest"
    )


def _acquire_assignment_notification_slot(key: str) -> bool:
    normalized_key = _normalize_text(key)
    if not normalized_key:
        return False
    redis_conn = get_redis_connection()
    if redis_conn:
        try:
            return bool(redis_conn.set(normalized_key, "1", ex=ACTION_ITEM_ASSIGNMENT_NOTIFICATION_TTL_SECONDS, nx=True))
        except Exception as exc:
            print(f"Assignment notification dedupe redis error: {exc}")
    if normalized_key in _SEEN_ASSIGNMENT_NOTIFICATION_KEYS:
        return False
    _SEEN_ASSIGNMENT_NOTIFICATION_KEYS.add(normalized_key)
    return True


def _work_item_assignment_text(task: Dict[str, Any], project: Dict[str, Any]) -> str:
    project_name = _normalize_text(project.get("project_name")) or _normalize_text(project.get("project_id")) or "Project"
    task_key = _normalize_text(task.get("external_id")) or "Work item"
    title = _normalize_text(task.get("title")) or "Untitled"
    return f"You have a new work item assignment in {project_name}: [{task_key}] {title}"


async def _post_ephemeral_response(payload: Dict[str, Any], text: str) -> None:
    response_url = _normalize_text(payload.get("response_url"))
    if not response_url or not text:
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
        return


def _build_work_item_assignment_blocks(task: Dict[str, Any], project: Dict[str, Any]) -> list[Dict[str, Any]]:
    project_name = _normalize_text(project.get("project_name")) or _normalize_text(project.get("project_id")) or "Project"
    project_id = _normalize_text(project.get("project_id"))
    task_key = _normalize_text(task.get("external_id")) or "N/A"
    title = _normalize_text(task.get("title")) or "Untitled"
    status_display = (
        _normalize_text(task.get("status_display"))
        or _normalize_text(task.get("status_raw"))
        or _normalize_text(task.get("status")).replace("_", " ").title()
        or "Unknown"
    )
    provider_type = _normalize_text(task.get("provider_type")) or "Work Item"
    url = _normalize_text(task.get("url"))
    action_value = json.dumps({"project_id": project_id, "task_id": task_key})
    blocks: list[Dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*New work item assigned*\n"
                    f"*Project:* {project_name}\n"
                    f"*Key:* `{task_key}`\n"
                    f"*Type:* {provider_type}\n"
                    f"*Title:* {title}\n"
                    f"*Status:* {status_display}"
                ),
            },
        }
    ]
    if url:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"<{url}|View in Jira>"},
            }
        )
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Update Work Item"},
                    "action_id": WORK_ITEM_ASSIGNMENT_OPEN_MODAL_ACTION_ID,
                    "value": action_value,
                }
            ],
        }
    )
    return blocks


def build_work_item_assignment_modal(
    *,
    task: Dict[str, Any],
    project_id: str,
    actor_user_id: str,
    actor_slack_user_id: str,
    transitions: list[Dict[str, str]],
) -> Dict[str, Any]:
    task_key = _normalize_text(task.get("external_id")) or "N/A"
    title = _normalize_text(task.get("title")) or "Untitled"
    status_display = (
        _normalize_text(task.get("status_display"))
        or _normalize_text(task.get("status_raw"))
        or _normalize_text(task.get("status")).replace("_", " ").title()
    )
    provider_type = _normalize_text(task.get("provider_type")) or "Work Item"
    url = _normalize_text(task.get("url"))
    status_options = [
        {
            "text": {"type": "plain_text", "text": _normalize_text(item.get("name"))},
            "value": _normalize_text(item.get("id")),
        }
        for item in (transitions or [])
        if _normalize_text(item.get("id")) and _normalize_text(item.get("name"))
    ]
    if not status_options:
        raise ValueError("No available transitions found for this work item.")
    initial_option = status_options[0]
    private_metadata = json.dumps(
        {
            "project_id": _normalize_text(project_id),
            "task_id": task_key,
            "actor_user_id": _normalize_text(actor_user_id),
            "actor_slack_user_id": _normalize_text(actor_slack_user_id),
            "transition_map": {
                _normalize_text(item.get("id")): _normalize_text(item.get("name"))
                for item in (transitions or [])
                if _normalize_text(item.get("id")) and _normalize_text(item.get("name"))
            },
        }
    )
    blocks: list[Dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Task #:* `{task_key}`\n"
                    f"*Type:* {provider_type}\n"
                    f"*Title:* {title}\n"
                    f"*Status:* {status_display}"
                ),
            },
        },
    ]
    if url:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"<{url}|View in Jira>"},
            }
        )
    blocks.extend(
        [
            {"type": "divider"},
            {
                "type": "input",
                "block_id": "status_input",
                "label": {"type": "plain_text", "text": "New Status"},
                "element": {
                    "type": "static_select",
                    "action_id": "status_value",
                    "initial_option": initial_option,
                    "options": status_options,
                },
            },
            {
                "type": "input",
                "block_id": "comment_input",
                "label": {"type": "plain_text", "text": "Comment"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "comment_value",
                    "multiline": True,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Any notes, blockers, or updates...",
                    },
                },
                "optional": False,
            },
        ]
    )
    return {
        "type": "modal",
        "callback_id": WORK_ITEM_ASSIGNMENT_UPDATE_CALLBACK_ID,
        "title": {"type": "plain_text", "text": "Update Work Item"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "private_metadata": private_metadata,
        "blocks": blocks,
    }


def _build_action_item_assignment_blocks(item: Dict[str, Any], project: Dict[str, Any]) -> list[Dict[str, Any]]:
    from app.action_items.slack_interaction_handler import (
        ACTION_ITEM_DIGEST_CANCEL_ACTION_ID,
        ACTION_ITEM_DIGEST_DONE_ACTION_ID,
    )

    project_name = _normalize_text(project.get("project_name")) or _normalize_text(project.get("project_id")) or "Project"
    project_id = _normalize_text(project.get("project_id"))
    display_key = _normalize_text(item.get("display_key")) or _normalize_text(item.get("action_item_id")) or "ACTION"
    title = _normalize_text(item.get("title")) or "Untitled"
    action_value = json.dumps(
        {
            "project_id": project_id,
            "action_item_id": _normalize_text(item.get("action_item_id")),
        }
    )
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*New action item assigned*\n"
                    f"*Project:* {project_name}\n"
                    f"*Ref:* `{display_key}`\n"
                    f"*Title:* {title}"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Done"},
                    "style": "primary",
                    "action_id": ACTION_ITEM_DIGEST_DONE_ACTION_ID,
                    "value": action_value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Cancelled"},
                    "style": "danger",
                    "action_id": ACTION_ITEM_DIGEST_CANCEL_ACTION_ID,
                    "value": action_value,
                },
            ],
        },
    ]


def _project_slack_client(project: Dict[str, Any]) -> Optional[SlackClient]:
    encrypted_token = _normalize_text((((project.get("integrations") or {}).get("slack") or {}).get("access_token")))
    if not encrypted_token:
        return None
    try:
        decrypted = encryption_service.decrypt(encrypted_token)
        return SlackClient(decrypted)
    except Exception as exc:
        print(f"Assignment notification Slack client init failed: {exc}")
        return None


def _notification_db(db: Any, project: Dict[str, Any]):
    if db is not None:
        return db
    resolved = get_database()
    if resolved is not None:
        return resolved
    project_id = _normalize_text(project.get("project_id"))
    if project_id:
        try:
            return get_brain_collection(project_id).database
        except Exception:
            return None
    return None


async def send_action_item_assignment_notification(
    *,
    db,
    project: Dict[str, Any],
    item: Dict[str, Any],
    old_owner_user_id: Optional[str],
    actor_user_id: Optional[str],
) -> bool:
    new_owner_user_id = _normalize_text(item.get("owner_user_id"))
    if _should_skip_assignment_notification(
        old_identity=old_owner_user_id,
        new_identity=new_owner_user_id,
        actor_user_id=actor_user_id,
        new_user_id=new_owner_user_id,
    ):
        return False
    member = _active_member_by_user_id(project, new_owner_user_id)
    if not member:
        return False
    version_token = _normalize_text(item.get("updated_at")) or _normalize_text(item.get("created_at")) or "latest"
    dedupe_key = _assignment_dedupe_key(
        entity_type="action_item",
        project_id=_normalize_text(project.get("project_id")),
        item_ref=_normalize_text(item.get("action_item_id")) or _normalize_text(item.get("display_key")),
        assignee_identity=new_owner_user_id,
        version_token=version_token,
    )
    if not _acquire_assignment_notification_slot(dedupe_key):
        return False
    slack_client = _project_slack_client(project)
    if not slack_client:
        return False
    db = _notification_db(db, project)
    if db is None:
        return False
    slack_user_id = await resolve_slack_user_id_for_member(
        db=db,
        project=project,
        member=member,
        slack_client=slack_client,
    )
    if not slack_user_id:
        return False
    return await slack_client.send_message(
        slack_user_id,
        text=f"You have a new action item assignment: {_normalize_text(item.get('display_key')) or _normalize_text(item.get('action_item_id'))}",
        blocks=_build_action_item_assignment_blocks(item, project),
    )


async def send_work_item_assignment_notification(
    *,
    db,
    project: Dict[str, Any],
    task: Dict[str, Any],
    old_task: Optional[Dict[str, Any]],
    actor_hint: Optional[str],
) -> bool:
    actor_user_id = _resolve_actor_user_id_from_hint(project, actor_hint)
    _old_user_id, old_identity, _old_label = _resolve_work_item_assignee_identity(project, old_task or {})
    new_user_id, new_identity, _new_label = _resolve_work_item_assignee_identity(project, task)
    if _should_skip_assignment_notification(
        old_identity=old_identity,
        new_identity=new_identity,
        actor_user_id=actor_user_id,
        new_user_id=new_user_id,
    ):
        return False
    if not new_user_id:
        return False
    member = _active_member_by_user_id(project, new_user_id)
    if not member:
        return False
    version_token = _work_item_version_token(task)
    dedupe_key = _assignment_dedupe_key(
        entity_type="work_item",
        project_id=_normalize_text(project.get("project_id")),
        item_ref=_normalize_text(task.get("external_id")),
        assignee_identity=new_identity or new_user_id or "assignee",
        version_token=version_token,
    )
    if not _acquire_assignment_notification_slot(dedupe_key):
        return False
    slack_client = _project_slack_client(project)
    if not slack_client:
        return False
    db = _notification_db(db, project)
    if db is None:
        return False
    slack_user_id = await resolve_slack_user_id_for_member(
        db=db,
        project=project,
        member=member,
        slack_client=slack_client,
    )
    if not slack_user_id:
        return False
    return await slack_client.send_message(
        slack_user_id,
        text=_work_item_assignment_text(task, project),
        blocks=_build_work_item_assignment_blocks(task, project),
    )


async def load_existing_work_item(
    *,
    project_id: str,
    task_id: str,
) -> Optional[Dict[str, Any]]:
    normalized_project_id = _normalize_text(project_id)
    normalized_task_id = _normalize_text(task_id)
    if not normalized_project_id or not normalized_task_id:
        return None
    collection = get_brain_collection(normalized_project_id)
    existing = await collection.find_one({"external_id": normalized_task_id})
    return dict(existing or {}) if isinstance(existing, dict) else None


async def resolve_project_member_for_slack_actor(
    *,
    db,
    project_id: str,
    team_id: str,
    slack_user_id: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[SlackClient]]:
    normalized_project_id = _normalize_text(project_id)
    normalized_team_id = _normalize_text(team_id)
    normalized_slack_user_id = _normalize_text(slack_user_id)
    if not normalized_project_id or not normalized_team_id or not normalized_slack_user_id:
        return None, None, None
    project = await db.projects.find_one(
        {
            "project_id": normalized_project_id,
            "integrations.slack.team_id": normalized_team_id,
            "integrations.slack.status": "connected",
        }
    )
    if not isinstance(project, dict):
        return None, None, None
    slack_client = _project_slack_client(project)
    members = _active_members(project)
    direct_member = next(
        (
            member
            for member in members
            if _normalize_text((member.get("integration_ids") or {}).get("slack_user_id")) == normalized_slack_user_id
        ),
        None,
    )
    if direct_member:
        return project, direct_member, slack_client
    if not slack_client:
        return project, None, None
    user_email = await slack_client.get_user_email(normalized_slack_user_id)
    normalized_email = _normalize_text(user_email).lower()
    if normalized_email:
        email_member = next(
            (
                member
                for member in members
                if _normalize_text(member.get("email")).lower() == normalized_email
            ),
            None,
        )
        if email_member:
            return project, email_member, slack_client
    return project, None, slack_client


async def handle_work_item_assignment_block_action(payload: Dict[str, Any]) -> bool:
    action = ((payload.get("actions") or [None])[0]) or {}
    value = _safe_parse_json_dict(action.get("value"))
    project_id = _normalize_text(value.get("project_id"))
    task_id = _normalize_text(value.get("task_id"))
    team_id = _normalize_text(((payload.get("team") or {}).get("id")))
    slack_user_id = _normalize_text(((payload.get("user") or {}).get("id")))
    trigger_id = _normalize_text(payload.get("trigger_id"))
    if not project_id or not task_id or not team_id or not slack_user_id or not trigger_id:
        await _post_ephemeral_response(
            payload,
            "I couldn't open the work-item update modal because the notification payload was incomplete.",
        )
        return False
    db = get_brain_collection(project_id).database
    project, member, slack_client = await resolve_project_member_for_slack_actor(
        db=db,
        project_id=project_id,
        team_id=team_id,
        slack_user_id=slack_user_id,
    )
    if not project or not member or not slack_client:
        await _post_ephemeral_response(
            payload,
            "I couldn't verify your project membership for this work item.",
        )
        return False
    task = await load_existing_work_item(project_id=project_id, task_id=task_id)
    if not task:
        await _post_ephemeral_response(
            payload,
            "I couldn't find the latest work-item details. Please retry from a fresh notification.",
        )
        return False
    actor_user_id = _normalize_text(member.get("user_id"))
    if not actor_user_id:
        await _post_ephemeral_response(
            payload,
            "I couldn't resolve your ProMarshal identity for this work item.",
        )
        return False
    transitions = await fetch_available_transitions(
        db=db,
        project=project,
        task_key=task_id,
        task_provider=_normalize_text(
            task.get("provider")
            or task.get("integration_type")
            or task.get("source")
            or "jira"
        ),
    )
    if not transitions:
        await _post_ephemeral_response(
            payload,
            "I couldn't load the available Jira transitions for this work item. Please open it in Jira or retry shortly.",
        )
        return False
    modal = build_work_item_assignment_modal(
        task=task,
        project_id=project_id,
        actor_user_id=actor_user_id,
        actor_slack_user_id=slack_user_id,
        transitions=transitions,
    )
    opened = await slack_client.open_modal(trigger_id, modal)
    if not opened:
        await _post_ephemeral_response(
            payload,
            "I couldn't open the work-item update modal right now. Please retry shortly.",
        )
    return opened


async def handle_work_item_assignment_modal_submission(payload: Dict[str, Any]) -> JSONResponse:
    metadata = _safe_parse_json_dict(((payload.get("view") or {}).get("private_metadata")))
    project_id = _normalize_text(metadata.get("project_id"))
    task_id = _normalize_text(metadata.get("task_id"))
    expected_actor_user_id = _normalize_text(metadata.get("actor_user_id"))
    expected_actor_slack_user_id = _normalize_text(metadata.get("actor_slack_user_id"))
    transition_map = metadata.get("transition_map") if isinstance(metadata.get("transition_map"), dict) else {}
    actual_slack_user_id = _normalize_text(((payload.get("user") or {}).get("id")))
    team_id = _normalize_text(((payload.get("team") or {}).get("id")))
    if (
        not project_id
        or not task_id
        or not expected_actor_slack_user_id
        or actual_slack_user_id != expected_actor_slack_user_id
        or not team_id
    ):
        return JSONResponse(
            content={
                "response_action": "errors",
                "errors": {"status_input": "Unable to verify assignment update context. Please retry from the notification."},
            },
            status_code=200,
        )

    values = (((payload.get("view") or {}).get("state") or {}).get("values")) or {}
    status_block = (values.get("status_input") or {}).get("status_value") or {}
    selected_option = status_block.get("selected_option") or {}
    transition_id = _normalize_text(selected_option.get("value"))
    transition_name = _normalize_text(transition_map.get(transition_id))
    comment_value = _normalize_text((((values.get("comment_input") or {}).get("comment_value") or {}).get("value")))
    if not transition_id or not transition_name:
        return JSONResponse(
            content={
                "response_action": "errors",
                "errors": {"status_input": "Please choose a valid Jira transition."},
            },
            status_code=200,
        )

    db = get_brain_collection(project_id).database
    project, member, _slack_client = await resolve_project_member_for_slack_actor(
        db=db,
        project_id=project_id,
        team_id=team_id,
        slack_user_id=actual_slack_user_id,
    )
    if not project or not member:
        return JSONResponse(
            content={
                "response_action": "errors",
                "errors": {"status_input": "Could not verify your project membership for this action."},
            },
            status_code=200,
        )

    actor_user_id = _normalize_text(member.get("user_id")) or expected_actor_user_id
    if not actor_user_id:
        return JSONResponse(
            content={
                "response_action": "errors",
                "errors": {"status_input": "Could not resolve your ProMarshal identity for this action."},
            },
            status_code=200,
        )

    from app.tasks.schemas import UpdateTaskDTO
    from app.tasks.service import TaskService

    try:
        await TaskService.mutate_task(
            task_data=UpdateTaskDTO(
                task_id=task_id,
                status=transition_name,
                transition_id=transition_id,
                comment=comment_value or None,
                project_id=project_id,
            ),
            project=project,
            updated_by=_normalize_text(member.get("email")) or actor_user_id,
            source_label="slack_assignment_notification",
        )
    except Exception:
        return JSONResponse(
            content={
                "response_action": "errors",
                "errors": {"status_input": "Failed to update this work item. Please try again."},
            },
            status_code=200,
        )

    return JSONResponse(content={"response_action": "clear"}, status_code=200)
