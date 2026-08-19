"""Slack slash command handlers for ProMarshal control commands (DM-only)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from app.action_items.service import ActionItemService
from app.core.database import get_brain_collection, get_database
from app.core.identity_resolver import build_identity_followup_message, resolve_project_member_identity
from app.integrations.slack.member_sync import sync_project_slack_member_ids_detailed
from app.integrations.slack import slack_bot_service
from app.integrations.slack.project_resolver import (
    list_slack_member_projects,
    persist_slack_active_project,
    resolve_slack_project,
)
from app.team_poll.orchestrator import (
    create_team_poll,
    get_owner_poll_status,
    close_owner_poll,
)
from app.team_poll.session_store import (
    list_owner_active_sessions,
    mark_owner_skipped,
)
from app.team_poll.summary import finalize_poll_if_ready
from app.team_poll.response_writer import compose_team_poll_response
from app.core.config import settings


def _normalize_text(value: Optional[str]) -> str:
    return str(value or "").strip()


def _canonicalize_command_text(value: Optional[str]) -> str:
    """
    Normalize command text for `/promarshal ...` slash payloads.

    Accepted inputs:
    - help
    - project list
    - legacy prefixed text like "promarshal help"
    """
    raw = _normalize_text(value)
    if raw.startswith("/"):
        raw = raw[1:].strip()
    lowered = raw.lower()
    if lowered == "promarshal":
        return ""
    if lowered.startswith("promarshal "):
        raw = raw[len("promarshal ") :].strip()
    return raw


def _normalize_lookup_key(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", _normalize_text(value).lower())


def _find_selected_project_from_hint(
    *,
    hint: str,
    candidate_projects: List[Dict[str, str]],
) -> Optional[Dict[str, str]]:
    clean_hint = _normalize_text(hint)
    if not clean_hint:
        return None

    if clean_hint.isdigit():
        idx = int(clean_hint) - 1
        if 0 <= idx < len(candidate_projects):
            return candidate_projects[idx]
        return None

    hint_key = _normalize_lookup_key(clean_hint)
    by_id = [
        item
        for item in candidate_projects
        if _normalize_lookup_key(item.get("project_id", "")) == hint_key
    ]
    if len(by_id) == 1:
        return by_id[0]
    if len(by_id) > 1:
        return None

    by_name = [
        item
        for item in candidate_projects
        if _normalize_lookup_key(item.get("project_name", "")) == hint_key
    ]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        return None

    prefix_matches = [
        item
        for item in candidate_projects
        if _normalize_lookup_key(item.get("project_id", "")).startswith(hint_key)
        or _normalize_lookup_key(item.get("project_name", "")).startswith(hint_key)
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    return None


async def authenticate_user(
    *,
    slack_user_id: str,
    team_id: str,
    channel_id: Optional[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str], str]:
    """
    Verify Slack user is a ProMarshal project member in the resolved context.

    Returns:
        (project, member, slack_access_token, reason)
    """
    try:
        db = get_database()
        resolution = await resolve_slack_project(
            db=db,
            team_id=team_id,
            slack_user_id=slack_user_id,
            channel_id=channel_id,
        )
        if not resolution.ok:
            return None, None, None, resolution.reason
        return resolution.project, resolution.member, resolution.slack_access_token, resolution.reason
    except Exception as exc:
        print(f"Error authenticating slash command user: {exc}")
        return None, None, None, "auth_exception"


def _ephemeral(text: str) -> Dict[str, str]:
    return {"response_type": "ephemeral", "text": text}


def _build_project_switch_modal_view(
    *,
    candidate_projects: List[Dict[str, str]],
    active_project_id: Optional[str],
) -> Dict[str, Any]:
    options: List[Dict[str, Any]] = []
    initial_option: Optional[Dict[str, Any]] = None
    for item in candidate_projects[:100]:
        project_id = _normalize_text(item.get("project_id"))
        project_name = _normalize_text(item.get("project_name")) or project_id
        if not project_id:
            continue
        option = {
            "text": {"type": "plain_text", "text": f"{project_name} ({project_id})"},
            "value": project_id,
        }
        options.append(option)
        if active_project_id and project_id == active_project_id:
            initial_option = option

    return {
        "type": "modal",
        "callback_id": "project_switch_modal",
        "title": {"type": "plain_text", "text": "Switch Project"},
        "submit": {"type": "plain_text", "text": "Switch"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Choose the active project for this DM context.",
                },
            },
            {
                "type": "input",
                "block_id": "project_block",
                "label": {"type": "plain_text", "text": "Project"},
                "element": {
                    "type": "static_select",
                    "action_id": "project_select",
                    "placeholder": {"type": "plain_text", "text": "Select a project"},
                    "options": options,
                    **({"initial_option": initial_option} if initial_option else {}),
                },
            },
        ],
    }


async def _open_project_switch_modal(
    *,
    db,
    team_id: str,
    trigger_id: str,
    candidate_projects: List[Dict[str, str]],
    active_project_id: Optional[str],
) -> bool:
    if not trigger_id:
        return False
    if not candidate_projects:
        return False
    project_ids = [
        _normalize_text(item.get("project_id"))
        for item in candidate_projects
        if _normalize_text(item.get("project_id"))
    ]
    project_doc = None
    if project_ids:
        project_doc = await db.projects.find_one(
            {
                "integrations.slack.team_id": _normalize_text(team_id),
                "integrations.slack.status": "connected",
                "project_id": {"$in": project_ids},
            }
        )
    if not project_doc:
        project_doc = await db.projects.find_one(
            {
                "integrations.slack.team_id": _normalize_text(team_id),
                "integrations.slack.status": "connected",
            }
        )
    encrypted_token = (((project_doc or {}).get("integrations") or {}).get("slack") or {}).get("access_token")
    if not encrypted_token:
        return False
    try:
        slack_client = slack_bot_service.get_slack_client(encrypted_token)
        await slack_client.views_open(
            trigger_id=trigger_id,
            view=_build_project_switch_modal_view(
                candidate_projects=candidate_projects,
                active_project_id=active_project_id,
            ),
        )
        return True
    except Exception as exc:
        print(
            "Slack project switch modal open failed: "
            f"team_id={team_id} trigger_id_present={bool(trigger_id)} error={exc}"
        )
        return False


async def open_project_switch_modal_for_user(
    *,
    team_id: str,
    slack_user_id: str,
    trigger_id: str,
) -> bool:
    """Open project switch modal for a user by resolving their project list."""
    candidate_projects, active_project_id, _user_email, error = await _list_member_projects(
        team_id=_normalize_text(team_id),
        slack_user_id=_normalize_text(slack_user_id),
    )
    if error:
        return False
    db = get_database()
    return await _open_project_switch_modal(
        db=db,
        team_id=_normalize_text(team_id),
        trigger_id=_normalize_text(trigger_id),
        candidate_projects=candidate_projects or [],
        active_project_id=active_project_id,
    )


def _build_auth_failure_response(reason: str) -> Dict[str, str]:
    if reason == "no_project_for_team":
        return _ephemeral(
            "ProMarshal is not configured for this workspace.\n\n"
            "Ask the project owner to connect Slack in ProMarshal."
        )
    if reason == "ambiguous_project":
        return _ephemeral(
            "I found multiple projects for your Slack user.\n\n"
            "Run:\n"
            "`/promarshal project list`\n"
            "`/promarshal project switch <project_id|project name|number>`"
        )
    return _ephemeral(
        "You're not authorized for ProMarshal commands in this workspace.\n\n"
        "Ask the project owner to add you as an active project member."
    )


def _build_project_list_text(
    *,
    candidate_projects: List[Dict[str, str]],
    active_project_id: Optional[str],
    heading: str,
) -> str:
    lines: List[str] = [heading, ""]
    if not candidate_projects:
        lines.append("No available projects found for your user in this workspace.")
        return "\n".join(lines)
    lines.append("Available projects:")
    for idx, item in enumerate(candidate_projects, start=1):
        project_id = _normalize_text(item.get("project_id")) or "unknown"
        project_name = _normalize_text(item.get("project_name")) or project_id
        active_marker = " [active]" if active_project_id and project_id == active_project_id else ""
        lines.append(f"{idx}. {project_name} ({project_id}){active_marker}")
    lines.append("")
    lines.append("Switch syntax: `/promarshal project switch <project_id|project name|number>`")
    lines.append("Quick picker: `/promarshal switch`")
    return "\n".join(lines)


async def _list_member_projects(
    *,
    team_id: str,
    slack_user_id: str,
) -> Tuple[Optional[List[Dict[str, str]]], Optional[str], Optional[str], Optional[Dict[str, str]]]:
    db = get_database()
    listing = await list_slack_member_projects(
        db=db,
        team_id=team_id,
        slack_user_id=slack_user_id,
    )
    if not listing.ok:
        if listing.reason == "no_project_for_team":
            return None, None, None, _ephemeral(
                "I couldn't find any ProMarshal projects connected to this Slack workspace."
            )
        if listing.reason == "user_not_member":
            return None, None, None, _ephemeral(
                "I couldn't find any active ProMarshal project memberships for your Slack user."
            )
        return None, None, None, _ephemeral(
            "I couldn't resolve your project list right now. Please try again."
        )

    candidate_projects = [item for item in listing.projects if isinstance(item, dict)]
    active_project_id = _normalize_text(listing.active_project_id) or None
    return candidate_projects, active_project_id, listing.user_email, None


async def handle_help_command(payload: Dict[str, Any]) -> Dict[str, str]:
    """
    Show slash command catalog.

    Task/action requests are intentionally excluded from slash command surface.
    """
    project, member, _token, reason = await authenticate_user(
        slack_user_id=_normalize_text(payload.get("user_id")),
        team_id=_normalize_text(payload.get("team_id")),
        channel_id=_normalize_text(payload.get("channel_id")),
    )

    if not project or not member:
        if reason == "ambiguous_project":
            # Still provide command reference when user needs to switch first.
            return _ephemeral(
                "*ProMarshal Commands (DM only)*\n\n"
                "Member commands:\n"
                "- `/promarshal help`\n"
                "- `/promarshal project list`\n"
                "- `/promarshal project current`\n"
                "- `/promarshal switch` (opens project picker)\n"
                "- `/promarshal project switch <project_id|project name|number>`\n\n"
                "Action item commands:\n"
                "- `/promarshal actionitem create <title> | owner:<user_id|name> [| related:<text>]`\n"
                "- `/promarshal actionitem list [status:<open|done|cancelled>] [owner:<user_id>]`\n"
                "- `/promarshal actionitem assign <action_item_id|ACTION-<n>> <owner_user_id|name>`\n"
                "- `/promarshal actionitem close <action_item_id|ACTION-<n>> [done|cancelled]`\n\n"
                "First set active project:\n"
                "`/promarshal project list`\n"
                "`/promarshal switch`\n"
                "`/promarshal project switch <...>`\n\n"
                "For tasks/questions, send normal text (no slash command)."
            )
        return _build_auth_failure_response(reason)

    role = _normalize_text((member or {}).get("role")).lower()
    lines: List[str] = [
        "*ProMarshal Commands (DM only)*",
        "",
        "Member commands:",
        "- `/promarshal help`",
        "- `/promarshal project list`",
        "- `/promarshal project current`",
        "- `/promarshal switch` (opens project picker)",
        "- `/promarshal project switch <project_id|project name|number>`",
        "- `/promarshal actionitem create <title> | owner:<user_id|name> [| related:<text>]`",
        "- `/promarshal actionitem list [status:<open|done|cancelled>] [owner:<user_id>]`",
        "- `/promarshal actionitem assign <action_item_id|ACTION-<n>> <owner_user_id|name>`",
        "- `/promarshal actionitem close <action_item_id|ACTION-<n>> [done|cancelled]`",
    ]
    if role == "owner":
        lines.extend(
            [
                "",
                "Owner commands:",
                "- `/promarshal integration status`",
                "- `/promarshal jira sync`",
                "- `/promarshal slack sync-members`",
                "- `/promarshal team_poll create <question>`",
                "- `/promarshal team_poll status [poll_id]`",
                "- `/promarshal team_poll close [poll_id]`",
                "- `/promarshal team_poll skip my response [poll_id]`",
            ]
        )
    lines.extend(
        [
            "",
            "For tasks/questions, send normal text (no slash command).",
        ]
    )
    return _ephemeral("\n".join(lines))


async def handle_project_list_command(payload: Dict[str, Any]) -> Dict[str, str]:
    candidate_projects, active_project_id, _user_email, error = await _list_member_projects(
        team_id=_normalize_text(payload.get("team_id")),
        slack_user_id=_normalize_text(payload.get("user_id")),
    )
    if error:
        return error
    return _ephemeral(
        _build_project_list_text(
            candidate_projects=candidate_projects or [],
            active_project_id=active_project_id,
            heading="Here are your available projects for this workspace.",
        )
    )


async def handle_project_current_command(payload: Dict[str, Any]) -> Dict[str, str]:
    candidate_projects, active_project_id, _user_email, error = await _list_member_projects(
        team_id=_normalize_text(payload.get("team_id")),
        slack_user_id=_normalize_text(payload.get("user_id")),
    )
    if error:
        return error
    if active_project_id:
        selected = next(
            (
                item
                for item in (candidate_projects or [])
                if _normalize_text(item.get("project_id")) == active_project_id
            ),
            None,
        )
        project_name = _normalize_text((selected or {}).get("project_name")) or active_project_id
        return _ephemeral(f"Current active project: {project_name} ({active_project_id}).")

    return _ephemeral(
        _build_project_list_text(
            candidate_projects=candidate_projects or [],
            active_project_id=None,
            heading="No active project is set for this DM yet.",
        )
    )


async def handle_project_switch_command(payload: Dict[str, Any], *, target: Optional[str]) -> Dict[str, str]:
    candidate_projects, active_project_id, user_email, error = await _list_member_projects(
        team_id=_normalize_text(payload.get("team_id")),
        slack_user_id=_normalize_text(payload.get("user_id")),
    )
    if error:
        return error

    if not _normalize_text(target):
        opened = await open_project_switch_modal_for_user(
            team_id=_normalize_text(payload.get("team_id")),
            slack_user_id=_normalize_text(payload.get("user_id")),
            trigger_id=_normalize_text(payload.get("trigger_id")),
        )
        if opened:
            return _ephemeral("Opening project switcher...")
        return _ephemeral(
            _build_project_list_text(
                candidate_projects=candidate_projects or [],
                active_project_id=active_project_id,
                heading="Project switch requested. Choose a project for this DM.",
            )
        )

    selected = _find_selected_project_from_hint(
        hint=_normalize_text(target),
        candidate_projects=candidate_projects or [],
    )
    if not selected:
        return _ephemeral(
            _build_project_list_text(
                candidate_projects=candidate_projects or [],
                active_project_id=active_project_id,
                heading=f"I couldn't match `{_normalize_text(target)}` to your project list.",
            )
        )

    db = get_database()
    selected_project_id = _normalize_text(selected.get("project_id"))
    selected_project_name = _normalize_text(selected.get("project_name")) or selected_project_id
    persisted = await persist_slack_active_project(
        db=db,
        team_id=_normalize_text(payload.get("team_id")),
        slack_user_id=_normalize_text(payload.get("user_id")),
        user_email=user_email,
        project_id=selected_project_id,
        project_name=selected_project_name,
        updated_by="slash_command_user_selection",
    )
    if persisted:
        return _ephemeral(f"Active project set to {selected_project_name} ({selected_project_id}).")
    return _ephemeral(
        "Active project selected, but persistence is temporarily unavailable. "
        "Please run the command again if routing appears inconsistent."
    )


async def _require_owner_context(
    payload: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, str]]]:
    project, member, _token, reason = await authenticate_user(
        slack_user_id=_normalize_text(payload.get("user_id")),
        team_id=_normalize_text(payload.get("team_id")),
        channel_id=_normalize_text(payload.get("channel_id")),
    )
    if not project or not member:
        return None, None, _build_auth_failure_response(reason)
    if _normalize_text((member or {}).get("role")).lower() != "owner":
        return None, None, _ephemeral("This command is owner-only.")
    return project, member, None


async def handle_integration_status_command(payload: Dict[str, Any]) -> Dict[str, str]:
    project, _member, error = await _require_owner_context(payload)
    if error:
        return error

    integrations = (project or {}).get("integrations") or {}
    slack_status = _normalize_text(((integrations.get("slack") or {}).get("status"))) or "not_connected"
    jira_status = _normalize_text(((integrations.get("jira") or {}).get("status"))) or "not_connected"
    project_name = _normalize_text((project or {}).get("project_name")) or _normalize_text(
        (project or {}).get("project_id")
    )
    project_id = _normalize_text((project or {}).get("project_id"))
    return _ephemeral(
        f"Integration status for {project_name} ({project_id}):\n"
        f"- Slack: {slack_status}\n"
        f"- Jira: {jira_status}"
    )


async def handle_slack_sync_members_command(payload: Dict[str, Any]) -> Dict[str, str]:
    project, _member, error = await _require_owner_context(payload)
    if error:
        return error

    db = get_database()
    result = await sync_project_slack_member_ids_detailed(
        db=db,
        project_id=str((project or {}).get("_id")),
        overwrite=False,
    )
    if result.get("status") != "ok":
        return _ephemeral(f"Slack member sync failed: {result.get('status')}")

    counts = result.get("counts") or {}
    return _ephemeral(
        "Slack member sync completed.\n"
        f"- Members checked: {result.get('member_count', 0)}\n"
        f"- Mapped: {counts.get('mapped', 0)}\n"
        f"- Already mapped: {counts.get('already_mapped', 0)}\n"
        f"- Not found in workspace: {counts.get('not_found_in_slack_workspace', 0)}\n"
        f"- Conflicts: {counts.get('conflict', 0)}"
    )


async def handle_jira_sync_command(payload: Dict[str, Any]) -> Dict[str, str]:
    project, _member, error = await _require_owner_context(payload)
    if error:
        return error

    project_id = _normalize_text((project or {}).get("project_id"))
    if not project_id:
        return _ephemeral("Project ID is missing; cannot run Jira sync.")

    try:
        # Reuse existing sync path to preserve current Jira sync behavior.
        from app.integrations.router import sync_jira_tasks

        result = await sync_jira_tasks(project_id=project_id)
        if isinstance(result, dict):
            return _ephemeral(
                "Jira sync completed.\n"
                f"- Project key: {result.get('project_key', 'unknown')}\n"
                f"- Tasks synced: {result.get('tasks_synced', 0)}"
            )
        return _ephemeral("Jira sync completed.")
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return _ephemeral(f"Jira sync failed: {detail}")
    except Exception as exc:
        return _ephemeral(f"Jira sync failed: {exc}")


async def handle_team_poll_create_command(payload: Dict[str, Any], *, question: str) -> Dict[str, str]:
    project, member, error = await _require_owner_context(payload)
    if error:
        return error
    workspace_id = _normalize_text(payload.get("team_id"))
    db = get_database()
    result = await create_team_poll(
        db,
        project=project or {},
        owner_member=member or {},
        question_text=question,
        workspace_id=workspace_id,
    )
    if not result.get("ok"):
        reason = str(result.get("reason") or "failed")
        if reason == "disabled":
            return _ephemeral(
                await compose_team_poll_response(
                    action="create_error",
                    user_message=question,
                    outcome_payload={"ok": False, "reason": reason},
                    fallback_text="Team poll is currently disabled.",
                )
            )
        if reason == "missing_question":
            return _ephemeral(
                await compose_team_poll_response(
                    action="create_error",
                    user_message=question,
                    outcome_payload={"ok": False, "reason": reason},
                    fallback_text="Please provide a question. Example: `/promarshal team_poll create Is everyone ready for cutover?`",
                )
            )
        return _ephemeral(
            await compose_team_poll_response(
                action="create_error",
                user_message=question,
                outcome_payload={"ok": False, "reason": reason},
                fallback_text=f"Team poll create failed: {reason}",
            )
        )
    fallback = (
        "Team poll started.\n"
        f"- Poll ID: {result.get('poll_id')}\n"
        f"- Question: {result.get('question_text')}\n"
        "Use `/promarshal team_poll status <poll_id>` for progress."
    )
    text = await compose_team_poll_response(
        action="create_success",
        user_message=question,
        outcome_payload=result,
        fallback_text=fallback,
    )
    return _ephemeral(text)


async def handle_team_poll_status_command(payload: Dict[str, Any], *, poll_id: Optional[str]) -> Dict[str, str]:
    if not bool(getattr(settings, "team_poll_enabled", True)):
        return _ephemeral(
            await compose_team_poll_response(
                action="status_error",
                user_message=str(poll_id or ""),
                outcome_payload={"ok": False, "reason": "disabled"},
                fallback_text="Team poll is currently disabled.",
            )
        )
    project, member, error = await _require_owner_context(payload)
    if error:
        return error
    owner_user_id = _normalize_text((member or {}).get("user_id")) or _normalize_text((member or {}).get("email"))
    db = get_database()
    result = await get_owner_poll_status(
        db,
        project=project or {},
        owner_user_id=owner_user_id,
        poll_id=poll_id,
    )
    if not result.get("ok"):
        reason = str(result.get("reason") or "failed")
        if reason == "no_active_poll":
            return _ephemeral(
                await compose_team_poll_response(
                    action="status_error",
                    user_message=str(poll_id or ""),
                    outcome_payload={"ok": False, "reason": reason},
                    fallback_text="No active team poll found for this project.",
                )
            )
        if reason == "ambiguous_poll":
            candidates = [str(item) for item in (result.get("candidates") or []) if str(item).strip()]
            if candidates:
                return _ephemeral(
                    await compose_team_poll_response(
                        action="status_ambiguous",
                        user_message=str(poll_id or ""),
                        outcome_payload=result,
                        fallback_text="Multiple active polls matched. Specify poll id:\n- " + "\n- ".join(candidates),
                    )
                )
            return _ephemeral(
                await compose_team_poll_response(
                    action="status_ambiguous",
                    user_message=str(poll_id or ""),
                    outcome_payload=result,
                    fallback_text="Multiple active polls matched. Specify poll id.",
                )
            )
        return _ephemeral(
            await compose_team_poll_response(
                action="status_error",
                user_message=str(poll_id or ""),
                outcome_payload={"ok": False, "reason": reason},
                fallback_text=f"Unable to fetch team poll status: {reason}",
            )
        )
    return _ephemeral(
        await compose_team_poll_response(
            action="status_success",
            user_message=str(poll_id or ""),
            outcome_payload=result,
            fallback_text=str((result.get("status") or {}).get("text") or "Team poll status unavailable."),
        )
    )


async def handle_team_poll_close_command(payload: Dict[str, Any], *, poll_id: Optional[str]) -> Dict[str, str]:
    if not bool(getattr(settings, "team_poll_enabled", True)):
        return _ephemeral(
            await compose_team_poll_response(
                action="close_error",
                user_message=str(poll_id or ""),
                outcome_payload={"ok": False, "reason": "disabled"},
                fallback_text="Team poll is currently disabled.",
            )
        )
    project, member, error = await _require_owner_context(payload)
    if error:
        return error
    owner_user_id = _normalize_text((member or {}).get("user_id")) or _normalize_text((member or {}).get("email"))
    db = get_database()
    result = await close_owner_poll(
        db,
        project=project or {},
        owner_user_id=owner_user_id,
        poll_id=poll_id,
    )
    if not result.get("ok"):
        reason = str(result.get("reason") or "failed")
        if reason == "no_active_poll":
            return _ephemeral(
                await compose_team_poll_response(
                    action="close_error",
                    user_message=str(poll_id or ""),
                    outcome_payload={"ok": False, "reason": reason},
                    fallback_text="No active team poll found to close.",
                )
            )
        if reason == "ambiguous_poll":
            candidates = [str(item) for item in (result.get("candidates") or []) if str(item).strip()]
            if candidates:
                return _ephemeral(
                    await compose_team_poll_response(
                        action="close_ambiguous",
                        user_message=str(poll_id or ""),
                        outcome_payload=result,
                        fallback_text="Multiple active polls matched. Specify poll id to close:\n- " + "\n- ".join(candidates),
                    )
                )
            return _ephemeral(
                await compose_team_poll_response(
                    action="close_ambiguous",
                    user_message=str(poll_id or ""),
                    outcome_payload=result,
                    fallback_text="Multiple active polls matched. Specify poll id to close.",
                )
            )
        return _ephemeral(
            await compose_team_poll_response(
                action="close_error",
                user_message=str(poll_id or ""),
                outcome_payload={"ok": False, "reason": reason},
                fallback_text=f"Unable to close team poll: {reason}",
            )
        )
    fallback = (
        "Team poll closed.\n"
        f"- Poll ID: {result.get('poll_id')}\n"
        f"- Sessions closed now: {result.get('closed_count', 0)}"
    )
    return _ephemeral(
        await compose_team_poll_response(
            action="close_success",
            user_message=str(poll_id or ""),
            outcome_payload=result,
            fallback_text=fallback,
        )
    )


async def handle_team_poll_owner_skip_command(payload: Dict[str, Any], *, poll_id: Optional[str]) -> Dict[str, str]:
    if not bool(getattr(settings, "team_poll_enabled", True)):
        return _ephemeral(
            await compose_team_poll_response(
                action="owner_skip_error",
                user_message=str(poll_id or ""),
                outcome_payload={"ok": False, "reason": "disabled"},
                fallback_text="Team poll is currently disabled.",
            )
        )
    project, member, error = await _require_owner_context(payload)
    if error:
        return error
    project_id = _normalize_text((project or {}).get("project_id"))
    owner_user_id = _normalize_text((member or {}).get("user_id")) or _normalize_text((member or {}).get("email"))
    db = get_database()
    sessions = await list_owner_active_sessions(
        db,
        project_id=project_id,
        owner_user_id=owner_user_id,
    )
    target = None
    if poll_id:
        for item in sessions:
            if _normalize_text(item.get("poll_id")) == _normalize_text(poll_id):
                target = item
                break
    elif sessions:
        target = sessions[0]
    if not isinstance(target, dict):
        return _ephemeral(
            await compose_team_poll_response(
                action="owner_skip_error",
                user_message=str(poll_id or ""),
                outcome_payload={"ok": False, "reason": "no_active_owner_session"},
                fallback_text="No active owner poll session found to skip.",
            )
        )

    await mark_owner_skipped(
        db,
        session_id=_normalize_text(target.get("session_id")),
        project_id=project_id,
    )
    await finalize_poll_if_ready(
        db,
        project=project or {},
        poll_id=_normalize_text(target.get("poll_id")),
        force=False,
    )
    return _ephemeral(
        await compose_team_poll_response(
            action="owner_skip_success",
            user_message=str(poll_id or ""),
            outcome_payload={"ok": True, "poll_id": _normalize_text(target.get("poll_id"))},
            fallback_text=f"Owner response skipped for poll {_normalize_text(target.get('poll_id'))}.",
        )
    )


def _resolve_member_actor_user_id(project: Dict[str, Any], member: Dict[str, Any]) -> Optional[str]:
    member_user_id = _normalize_text((member or {}).get("user_id"))
    if member_user_id:
        return member_user_id
    role = _normalize_text((member or {}).get("role")).lower()
    if role == "owner":
        return _normalize_text((project or {}).get("user_id")) or None
    return None


def _resolve_action_item_owner_label(project: Dict[str, Any], owner_user_id: Optional[str]) -> str:
    owner_id = _normalize_text(owner_user_id)
    if not owner_id:
        return "Unassigned"

    for member in (project or {}).get("members") or []:
        if not isinstance(member, dict):
            continue
        if _normalize_text(member.get("user_id")) != owner_id:
            continue
        name = _normalize_text(member.get("name"))
        if name:
            return name
        email = _normalize_text(member.get("email"))
        if email:
            return email
        break

    if _normalize_text((project or {}).get("user_id")) == owner_id:
        owner_name = _normalize_text((project or {}).get("owner_name"))
        if owner_name:
            return owner_name
    return owner_id


def _parse_pipe_key_values(value: str) -> Dict[str, str]:
    """
    Parse key:value arguments from free-form slash command text.

    Supports:
    - `key:value`
    - `key: value with spaces`
    - `key:"quoted value"`
    - separators by space and/or `|`
    """
    raw = str(value or "")
    pattern = re.compile(r"(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*:\s*")
    matches = list(pattern.finditer(raw))
    items: Dict[str, str] = {}
    if not matches:
        return items

    for idx, match in enumerate(matches):
        norm_key = _normalize_text(match.group("key")).lower().replace("-", "_")
        value_start = match.end()
        value_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        raw_value = raw[value_start:value_end].strip().strip("|").strip()
        if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {"'", '"'}:
            raw_value = raw_value[1:-1].strip()
        norm_value = _normalize_text(raw_value)
        if norm_key and norm_value:
            items[norm_key] = norm_value
    return items


def _parse_due_inputs(raw_due: Optional[str]) -> Tuple[str, Optional[datetime], Optional[str]]:
    due_text = _normalize_text(raw_due).lower()
    if not due_text:
        return "no_due_date", None, None
    if due_text in {"today", "this_week", "no_due_date"}:
        return due_text, None, None
    try:
        due_date = datetime.fromisoformat(due_text).replace(tzinfo=timezone.utc)
        return "by_date", due_date, None
    except Exception:
        return "no_due_date", None, "Invalid due value. Use today, this_week, no_due_date, or YYYY-MM-DD."


async def _resolve_action_item_by_ref(
    *,
    custom_project_id: str,
    action_item_ref: str,
) -> Optional[str]:
    ref = _normalize_text(action_item_ref)
    if not ref:
        return None
    if ref.lower().startswith("action-"):
        suffix = ref.split("-", 1)[1] if "-" in ref else ""
        collection = get_brain_collection(custom_project_id)
        doc = await collection.find_one(
            {
                "entity_type": "action_item",
                "display_key": f"ACTION-{suffix}".upper(),
            },
            {"action_item_id": 1},
        )
        return _normalize_text((doc or {}).get("action_item_id")) or None
    return ref


async def handle_action_item_create_command(payload: Dict[str, Any], *, raw_args: str) -> Dict[str, str]:
    if not bool(getattr(settings, "action_items_enabled", False)):
        return _ephemeral("Action items are currently disabled.")

    project, member, _token, reason = await authenticate_user(
        slack_user_id=_normalize_text(payload.get("user_id")),
        team_id=_normalize_text(payload.get("team_id")),
        channel_id=_normalize_text(payload.get("channel_id")),
    )
    if not project or not member:
        return _build_auth_failure_response(reason)

    actor_user_id = _resolve_member_actor_user_id(project, member)
    if not actor_user_id:
        return _ephemeral("I couldn't map your membership to a ProMarshal user id.")

    raw = _normalize_text(raw_args)
    if not raw:
        return _ephemeral(
            "Missing title.\nExample: `/promarshal actionitem create Send update | owner:u-123 | related:Ops follow-up`"
        )
    options = _parse_pipe_key_values(raw)
    segments = [segment.strip() for segment in raw.split("|")]
    head = _normalize_text(segments[0]) if segments else ""
    head_is_kv = bool(re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:", head))
    title = options.get("title") or ("" if head_is_kv else head)
    if not title:
        return _ephemeral("Title is required.")

    due_type, due_date, due_error = _parse_due_inputs(options.get("due") or options.get("due_type"))
    if due_error:
        return _ephemeral(due_error)

    owner_hint = options.get("owner") or options.get("owner_user_id")
    resolved_owner_user_id: Optional[str] = None
    if not owner_hint:
        return _ephemeral(
            "Owner is required before I create this action item.\n"
            "Please retry with `owner:<user_id|name>`.\n"
            "Example: `/promarshal actionitem create Capture quick reminder | owner:prabhu | related:Ops follow-up`"
        )

    owner_resolution = resolve_project_member_identity(project, owner_hint)
    resolved_owner_user_id = owner_resolution.promarshal_user_id
    if owner_resolution.status != "resolved" or not resolved_owner_user_id:
        return _ephemeral(
            build_identity_followup_message(
                owner_resolution,
                subject="owner",
                project=project,
            )
        )

    custom_project_id = _normalize_text((project or {}).get("project_id"))
    try:
        created = await ActionItemService.create(
            custom_project_id,
            {
                "user_id": actor_user_id,
                "title": title,
                "owner_user_id": resolved_owner_user_id,
                "related_to": options.get("related") or options.get("related_to"),
                "related_type": options.get("related_type") or "other",
                "related_id": options.get("related_id"),
                "source": "manual",
                "due_type": due_type,
                "due_date": due_date,
            },
        )
    except PermissionError as exc:
        return _ephemeral(str(exc))
    except ValueError as exc:
        return _ephemeral(str(exc))
    except Exception as exc:
        return _ephemeral(f"Action item create failed: {exc}")

    display_key = _normalize_text(created.get("display_key")) or _normalize_text(created.get("action_item_id"))
    followup_flag = bool(created.get("needs_followup"))
    suffix = " (needs follow-up)" if followup_flag else ""
    return _ephemeral(f"Created action item {display_key}.{suffix}")


async def handle_action_item_list_command(payload: Dict[str, Any], *, raw_args: str) -> Dict[str, str]:
    if not bool(getattr(settings, "action_items_enabled", False)):
        return _ephemeral("Action items are currently disabled.")

    project, member, _token, reason = await authenticate_user(
        slack_user_id=_normalize_text(payload.get("user_id")),
        team_id=_normalize_text(payload.get("team_id")),
        channel_id=_normalize_text(payload.get("channel_id")),
    )
    if not project or not member:
        return _build_auth_failure_response(reason)

    actor_user_id = _resolve_member_actor_user_id(project, member)
    if not actor_user_id:
        return _ephemeral("I couldn't map your membership to a ProMarshal user id.")

    options = _parse_pipe_key_values(raw_args)
    custom_project_id = _normalize_text((project or {}).get("project_id"))
    status_filter = _normalize_text(options.get("status"))
    if status_filter:
        status_filter = status_filter.lower()
        if status_filter in {"all", "any", "*"}:
            status_filter = None
    else:
        status_filter = "open"

    try:
        result = await ActionItemService.list(
            custom_project_id,
            {
                "user_id": actor_user_id,
                "status": status_filter,
                "owner_user_id": options.get("owner") or options.get("owner_user_id"),
                "due_type": options.get("due_type"),
                "source": options.get("source"),
                "needs_followup": (
                    options.get("needs_followup", "").lower() == "true"
                    if "needs_followup" in options
                    else None
                ),
                "limit": int(options.get("limit") or 20),
                "offset": int(options.get("offset") or 0),
            },
        )
    except PermissionError as exc:
        return _ephemeral(str(exc))
    except ValueError as exc:
        return _ephemeral(str(exc))
    except Exception as exc:
        return _ephemeral(f"Action item list failed: {exc}")

    items = result.get("items") or []
    if not items:
        return _ephemeral("No action items found for the selected filters.")
    lines: List[str] = []
    for item in items[:20]:
        key = _normalize_text(item.get("display_key")) or _normalize_text(item.get("action_item_id")) or "N/A"
        title = _normalize_text(item.get("title")) or "(untitled)"
        status = _normalize_text(item.get("status")) or "open"
        owner_label = _resolve_action_item_owner_label(project, item.get("owner_user_id"))
        followup = " [follow-up]" if bool(item.get("needs_followup")) else ""
        lines.append(f"- {key} [{status}]{followup} (owner: {owner_label}): {title}")
    total = int(result.get("total") or len(items))
    return _ephemeral("Action items:\n" + "\n".join(lines) + f"\nTotal: {total}")


async def handle_action_item_update_command(
    payload: Dict[str, Any],
    *,
    action_item_ref: str,
    raw_args: str,
) -> Dict[str, str]:
    if not bool(getattr(settings, "action_items_enabled", False)):
        return _ephemeral("Action items are currently disabled.")

    project, member, _token, reason = await authenticate_user(
        slack_user_id=_normalize_text(payload.get("user_id")),
        team_id=_normalize_text(payload.get("team_id")),
        channel_id=_normalize_text(payload.get("channel_id")),
    )
    if not project or not member:
        return _build_auth_failure_response(reason)

    actor_user_id = _resolve_member_actor_user_id(project, member)
    if not actor_user_id:
        return _ephemeral("I couldn't map your membership to a ProMarshal user id.")

    options = _parse_pipe_key_values(raw_args)
    custom_project_id = _normalize_text((project or {}).get("project_id"))
    action_item_id = await _resolve_action_item_by_ref(
        custom_project_id=custom_project_id,
        action_item_ref=action_item_ref,
    )
    if not action_item_id:
        return _ephemeral(f"Action item `{action_item_ref}` not found.")

    update_payload: Dict[str, Any] = {"user_id": actor_user_id}
    for src, dst in (
        ("title", "title"),
        ("owner", "owner_user_id"),
        ("owner_user_id", "owner_user_id"),
        ("related", "related_to"),
        ("related_to", "related_to"),
        ("related_type", "related_type"),
        ("related_id", "related_id"),
        ("status", "status"),
        ("due_type", "due_type"),
    ):
        if src in options:
            update_payload[dst] = options.get(src)
    if "needs_followup" in options:
        update_payload["needs_followup"] = options.get("needs_followup", "").lower() == "true"
    if "due" in options:
        due_type, due_date, due_error = _parse_due_inputs(options.get("due"))
        if due_error:
            return _ephemeral(due_error)
        update_payload["due_type"] = due_type
        update_payload["due_date"] = due_date

    if len(update_payload) <= 1:
        return _ephemeral("No update fields provided.")

    try:
        updated = await ActionItemService.update(custom_project_id, action_item_id, update_payload)
    except PermissionError as exc:
        return _ephemeral(str(exc))
    except ValueError as exc:
        return _ephemeral(str(exc))
    except Exception as exc:
        return _ephemeral(f"Action item update failed: {exc}")

    key = _normalize_text(updated.get("display_key")) or _normalize_text(updated.get("action_item_id")) or action_item_ref
    return _ephemeral(f"Updated action item {key}.")


async def handle_action_item_close_command(
    payload: Dict[str, Any],
    *,
    action_item_ref: str,
    status: str,
) -> Dict[str, str]:
    if not bool(getattr(settings, "action_items_enabled", False)):
        return _ephemeral("Action items are currently disabled.")

    project, member, _token, reason = await authenticate_user(
        slack_user_id=_normalize_text(payload.get("user_id")),
        team_id=_normalize_text(payload.get("team_id")),
        channel_id=_normalize_text(payload.get("channel_id")),
    )
    if not project or not member:
        return _build_auth_failure_response(reason)

    actor_user_id = _resolve_member_actor_user_id(project, member)
    if not actor_user_id:
        return _ephemeral("I couldn't map your membership to a ProMarshal user id.")

    custom_project_id = _normalize_text((project or {}).get("project_id"))
    action_item_id = await _resolve_action_item_by_ref(
        custom_project_id=custom_project_id,
        action_item_ref=action_item_ref,
    )
    if not action_item_id:
        return _ephemeral(f"Action item `{action_item_ref}` not found.")

    close_status = _normalize_text(status).lower() or "done"
    if close_status not in {"done", "cancelled"}:
        return _ephemeral("Close status must be `done` or `cancelled`.")

    try:
        closed = await ActionItemService.close(
            custom_project_id,
            action_item_id,
            {"user_id": actor_user_id, "status": close_status},
        )
    except PermissionError as exc:
        return _ephemeral(str(exc))
    except ValueError as exc:
        return _ephemeral(str(exc))
    except Exception as exc:
        return _ephemeral(f"Action item close failed: {exc}")

    key = _normalize_text(closed.get("display_key")) or _normalize_text(closed.get("action_item_id")) or action_item_ref
    return _ephemeral(f"Marked action item {key} as {close_status}.")


async def handle_action_item_assign_command(
    payload: Dict[str, Any],
    *,
    action_item_ref: str,
    owner_user_id: str,
) -> Dict[str, str]:
    if not bool(getattr(settings, "action_items_enabled", False)):
        return _ephemeral("Action items are currently disabled.")

    project, member, _token, reason = await authenticate_user(
        slack_user_id=_normalize_text(payload.get("user_id")),
        team_id=_normalize_text(payload.get("team_id")),
        channel_id=_normalize_text(payload.get("channel_id")),
    )
    if not project or not member:
        return _build_auth_failure_response(reason)

    actor_user_id = _resolve_member_actor_user_id(project, member)
    if not actor_user_id:
        return _ephemeral("I couldn't map your membership to a ProMarshal user id.")

    normalized_owner = _normalize_text(owner_user_id)
    if not normalized_owner:
        return _ephemeral("Owner user id is required. Example: `/promarshal actionitem assign ACTION-7 u-456`")
    owner_resolution = resolve_project_member_identity(project, normalized_owner)
    resolved_owner_user_id = owner_resolution.promarshal_user_id
    if owner_resolution.status != "resolved" or not resolved_owner_user_id:
        return _ephemeral(
            build_identity_followup_message(
                owner_resolution,
                subject="assignee",
                project=project,
            )
        )

    custom_project_id = _normalize_text((project or {}).get("project_id"))
    action_item_id = await _resolve_action_item_by_ref(
        custom_project_id=custom_project_id,
        action_item_ref=action_item_ref,
    )
    if not action_item_id:
        return _ephemeral(f"Action item `{action_item_ref}` not found.")

    try:
        updated = await ActionItemService.update(
            custom_project_id,
            action_item_id,
            {"user_id": actor_user_id, "owner_user_id": resolved_owner_user_id},
        )
    except PermissionError as exc:
        return _ephemeral(str(exc))
    except ValueError as exc:
        return _ephemeral(str(exc))
    except Exception as exc:
        return _ephemeral(f"Action item assign failed: {exc}")

    key = _normalize_text(updated.get("display_key")) or _normalize_text(updated.get("action_item_id")) or action_item_ref
    return _ephemeral(f"Reassigned action item {key} to {resolved_owner_user_id}.")


async def dispatch_command(payload: Dict[str, Any]) -> Dict[str, str]:
    """Route `/promarshal ...` control command text to deterministic handlers."""
    raw_text = _canonicalize_command_text(payload.get("text"))
    normalized = " ".join(raw_text.lower().split())

    if not normalized or normalized in {"help", "commands"}:
        return await handle_help_command(payload)

    if normalized in {"project list", "projects", "list projects"}:
        return await handle_project_list_command(payload)

    if normalized in {"project current", "current project", "active project"}:
        return await handle_project_current_command(payload)

    switch_match = re.match(
        r"^\s*(?:project\s+switch|switch\s+project|switch)\s*(.*)\s*$",
        raw_text,
        flags=re.IGNORECASE,
    )
    if switch_match:
        target = _normalize_text(switch_match.group(1))
        return await handle_project_switch_command(payload, target=target)

    if normalized in {"jira sync", "sync jira"}:
        return await handle_jira_sync_command(payload)

    if normalized in {"slack sync-members", "slack sync members", "sync slack members"}:
        return await handle_slack_sync_members_command(payload)

    if normalized in {"integration status", "integrations status", "status integrations"}:
        return await handle_integration_status_command(payload)

    action_item_create_match = re.match(
        r"^\s*action(?:item|-item)\s+create\s+(.+?)\s*$",
        raw_text,
        flags=re.IGNORECASE,
    )
    if action_item_create_match:
        return await handle_action_item_create_command(
            payload,
            raw_args=_normalize_text(action_item_create_match.group(1)),
        )

    action_item_list_match = re.match(
        r"^\s*action(?:item|-item)\s+list(?:\s+(.+))?\s*$",
        raw_text,
        flags=re.IGNORECASE,
    )
    if action_item_list_match:
        return await handle_action_item_list_command(
            payload,
            raw_args=_normalize_text(action_item_list_match.group(1)),
        )

    action_item_assign_match = re.match(
        r"^\s*action(?:item|-item)\s+assign\s+([A-Za-z0-9\-]+)\s+(.+?)\s*$",
        raw_text,
        flags=re.IGNORECASE,
    )
    if action_item_assign_match:
        return await handle_action_item_assign_command(
            payload,
            action_item_ref=_normalize_text(action_item_assign_match.group(1)),
            owner_user_id=_normalize_text(action_item_assign_match.group(2)),
        )

    action_item_close_match = re.match(
        r"^\s*action(?:item|-item)\s+close\s+([A-Za-z0-9\-]+)(?:\s+(done|cancelled))?\s*$",
        raw_text,
        flags=re.IGNORECASE,
    )
    if action_item_close_match:
        return await handle_action_item_close_command(
            payload,
            action_item_ref=_normalize_text(action_item_close_match.group(1)),
            status=_normalize_text(action_item_close_match.group(2)) or "done",
        )

    team_poll_create_match = re.match(r"^\s*team_poll\s+create\s+(.+?)\s*$", raw_text, flags=re.IGNORECASE)
    if team_poll_create_match:
        return await handle_team_poll_create_command(payload, question=_normalize_text(team_poll_create_match.group(1)))

    team_poll_status_match = re.match(r"^\s*team_poll\s+status(?:\s+([A-Za-z0-9\-]+))?\s*$", raw_text, flags=re.IGNORECASE)
    if team_poll_status_match:
        return await handle_team_poll_status_command(payload, poll_id=_normalize_text(team_poll_status_match.group(1)))

    team_poll_close_match = re.match(r"^\s*team_poll\s+close(?:\s+([A-Za-z0-9\-]+))?\s*$", raw_text, flags=re.IGNORECASE)
    if team_poll_close_match:
        return await handle_team_poll_close_command(payload, poll_id=_normalize_text(team_poll_close_match.group(1)))

    team_poll_skip_match = re.match(
        r"^\s*team_poll\s+skip\s+my\s+response(?:\s+([A-Za-z0-9\-]+))?\s*$",
        raw_text,
        flags=re.IGNORECASE,
    )
    if team_poll_skip_match:
        return await handle_team_poll_owner_skip_command(payload, poll_id=_normalize_text(team_poll_skip_match.group(1)))

    return _ephemeral(
        "Unknown ProMarshal command.\n\n"
        "Run `/promarshal help` to view available commands.\n"
        "For tasks/questions, send normal text (no slash command)."
    )
