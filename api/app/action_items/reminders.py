"""Action item reminder and escalation executors."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.database import get_brain_collection
from app.core.identity_resolver import format_project_member_candidates
from app.action_items.slack_interaction_handler import (
    ACTION_ITEM_DIGEST_CANCEL_ACTION_ID,
    ACTION_ITEM_DIGEST_DONE_ACTION_ID,
)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _active_member_by_user_id(project: Dict[str, Any], user_id: str) -> Optional[Dict[str, Any]]:
    normalized_user_id = _normalize_text(user_id)
    if not normalized_user_id:
        return None
    for member in project.get("members") or []:
        if not isinstance(member, dict):
            continue
        if _normalize_text(member.get("status")).lower() != "active":
            continue
        if _normalize_text(member.get("user_id")) == normalized_user_id:
            return member
    return None


def _top_open_action_items_text(
    project_name: str,
    project_id: str,
    owner_name: str,
    items: List[Dict[str, Any]],
) -> str:
    lines: List[str] = [
        f"*Project:* {project_name or 'Unknown Project'} ({project_id})",
        f"*Action items digest for {owner_name or 'you'}:*",
    ]
    for item in items[:10]:
        key = _normalize_text(item.get("display_key")) or _normalize_text(item.get("action_item_id")) or "ACTION"
        title = _normalize_text(item.get("title")) or "(untitled)"
        followup = " [follow-up]" if bool(item.get("needs_followup")) else ""
        lines.append(f"- {key}{followup}: {title}")
    if len(items) > 10:
        lines.append(f"...and {len(items) - 10} more open items.")
    return "\n".join(lines)


def _build_action_item_digest_blocks(
    *,
    project_name: str,
    project_id: str,
    owner_name: str,
    items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Project:* {project_name or 'Unknown Project'} ({project_id})",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Action items digest for {owner_name or 'you'}*",
            },
        }
    ]

    for item in items[:5]:
        key = _normalize_text(item.get("display_key")) or _normalize_text(item.get("action_item_id")) or "ACTION"
        title = _normalize_text(item.get("title")) or "(untitled)"
        due_hint = _normalize_text(item.get("due_type")) or "no_due_date"
        if item.get("due_date"):
            due_hint = str(item.get("due_date"))
        followup = " [follow-up]" if bool(item.get("needs_followup")) else ""
        action_item_id = _normalize_text(item.get("action_item_id"))
        value_payload = json.dumps(
            {
                "project_id": _normalize_text(project_id),
                "action_item_id": action_item_id,
            }
        )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{key}*{followup}\n{title}\n`due: {due_hint}`",
                },
            }
        )
        if action_item_id:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": ACTION_ITEM_DIGEST_DONE_ACTION_ID,
                            "text": {"type": "plain_text", "text": "Mark done"},
                            "style": "primary",
                            "value": value_payload,
                        },
                        {
                            "type": "button",
                            "action_id": ACTION_ITEM_DIGEST_CANCEL_ACTION_ID,
                            "text": {"type": "plain_text", "text": "Cancel"},
                            "style": "danger",
                            "value": value_payload,
                        },
                    ],
                }
            )

    if len(items) > 5:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"...and {len(items) - 5} more open items.",
                    }
                ],
            }
        )

    return blocks


async def execute_action_item_digest(
    db: AsyncIOMotorDatabase,
    schedule: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Send per-owner open action-item digest reminders.

    Scheduler-level execution: separate from cadence reminders.
    """
    if not bool(getattr(settings, "action_items_enabled", False)):
        return {"skipped": True, "reason": "action_items_disabled"}
    if not bool(getattr(settings, "action_items_digest_enabled", False)):
        return {"skipped": True, "reason": "action_items_digest_disabled"}

    project_id = _normalize_text(schedule.get("project_id"))
    if not project_id:
        return {"skipped": True, "reason": "missing_project_id"}

    project = await db.projects.find_one({"project_id": project_id})
    if not isinstance(project, dict):
        return {"skipped": True, "reason": "project_not_found"}
    project_name = _normalize_text(project.get("project_name")) or "Unknown Project"

    slack_integration = (project.get("integrations") or {}).get("slack") or {}
    encrypted_token = slack_integration.get("access_token")
    if not encrypted_token:
        return {"skipped": True, "reason": "slack_not_connected"}

    from app.cadence.slack_identity import resolve_slack_user_id_for_member
    from app.core.security import encryption_service
    from app.integrations.slack.client import SlackClient

    token = encryption_service.decrypt(encrypted_token)
    slack_client = SlackClient(token)
    collection = get_brain_collection(project_id)
    open_items = await collection.find(
        {
            "entity_type": "action_item",
            "status": "open",
            "owner_user_id": {"$exists": True, "$ne": None},
        }
    ).sort([("updated_at", -1), ("created_at", -1)]).to_list(length=500)

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in open_items:
        owner_user_id = _normalize_text(item.get("owner_user_id"))
        if not owner_user_id:
            continue
        grouped.setdefault(owner_user_id, []).append(item)

    delivered = 0
    failed = 0
    skipped_owner = 0
    for owner_user_id, items in grouped.items():
        member = _active_member_by_user_id(project, owner_user_id)
        if not isinstance(member, dict):
            skipped_owner += 1
            continue
        slack_user_id = await resolve_slack_user_id_for_member(
            db=db,
            project=project,
            member=member,
            slack_client=slack_client,
        )
        if not slack_user_id:
            skipped_owner += 1
            continue
        owner_name = _normalize_text(member.get("name")) or _normalize_text(member.get("email")) or owner_user_id
        text = _top_open_action_items_text(project_name, project_id, owner_name, items)
        blocks = _build_action_item_digest_blocks(
            project_name=project_name,
            project_id=project_id,
            owner_name=owner_name,
            items=items,
        )
        sent = await slack_client.send_message(slack_user_id, text=text, blocks=blocks)
        if sent:
            delivered += 1
        else:
            failed += 1

    return {
        "project_id": project_id,
        "owner_groups": len(grouped),
        "delivered": delivered,
        "failed": failed,
        "skipped_owner": skipped_owner,
    }


async def execute_followup_escalation(
    db: AsyncIOMotorDatabase,
    schedule: Dict[str, Any],
) -> Dict[str, Any]:
    """Send actor-first reminder and PM escalation for unresolved follow-up items."""
    if not bool(getattr(settings, "action_items_enabled", False)):
        return {"skipped": True, "reason": "action_items_disabled"}

    project_id = _normalize_text(schedule.get("project_id"))
    if not project_id:
        return {"skipped": True, "reason": "missing_project_id"}

    project = await db.projects.find_one({"project_id": project_id})
    if not isinstance(project, dict):
        return {"skipped": True, "reason": "project_not_found"}
    project_owner_user_id = _normalize_text(project.get("user_id"))

    ignore_followup_for_owner = bool(
        (schedule.get("schedule_spec") or {}).get("ignore_followup_for_owner", False)
    )
    if not ignore_followup_for_owner:
        digest_schedule = None
        try:
            project_schedules = getattr(db, "project_schedules", None)
            if project_schedules is not None:
                digest_schedule = await project_schedules.find_one(
                    {"job_type": "action_item_digest", "project_id": project_id},
                    {"schedule_spec": 1},
                )
        except Exception:
            digest_schedule = None
        digest_spec = dict((digest_schedule or {}).get("schedule_spec") or {})
        ignore_followup_for_owner = bool(digest_spec.get("ignore_followup_for_owner", False))

    slack_integration = (project.get("integrations") or {}).get("slack") or {}
    encrypted_token = slack_integration.get("access_token")
    if not encrypted_token:
        return {"skipped": True, "reason": "slack_not_connected"}

    from app.cadence.slack_identity import resolve_slack_user_id_for_member
    from app.core.security import encryption_service
    from app.integrations.slack.client import SlackClient

    token = encryption_service.decrypt(encrypted_token)
    slack_client = SlackClient(token)
    collection = get_brain_collection(project_id)
    now = datetime.utcnow()

    reminder_hours = max(1, int(getattr(settings, "action_items_followup_actor_reminder_hours", 48)))
    reminder_cutoff = now - timedelta(hours=reminder_hours)
    reminder_candidates = await collection.find(
        {
            "entity_type": "action_item",
            "status": "open",
            "needs_followup": True,
            "needs_followup_set_at": {"$lte": reminder_cutoff},
            "$or": [
                {"followup_actor_reminded_at": {"$exists": False}},
                {"followup_actor_reminded_at": None},
            ],
        }
    ).to_list(length=500)

    reminded_action_item_ids: List[str] = []
    reminder_failed = 0
    reminder_skipped = 0
    grouped_by_actor: Dict[str, List[Dict[str, Any]]] = {}
    for item in reminder_candidates:
        actor_user_id = _normalize_text(item.get("created_by_user_id"))
        if not actor_user_id:
            reminder_skipped += 1
            continue
        if ignore_followup_for_owner and actor_user_id == project_owner_user_id:
            reminder_skipped += 1
            continue
        grouped_by_actor.setdefault(actor_user_id, []).append(item)

    for actor_user_id, items in grouped_by_actor.items():
        actor_member = _active_member_by_user_id(project, actor_user_id)
        if not actor_member:
            reminder_skipped += len(items)
            continue
        actor_slack_id = await resolve_slack_user_id_for_member(
            db=db,
            project=project,
            member=actor_member,
            slack_client=slack_client,
        )
        if not actor_slack_id:
            reminder_skipped += len(items)
            continue

        lines = [
            "*Action items follow-up reminder:*",
            "",
            f"{len(items)} item(s) still need assignee clarification.",
        ]
        for item in items[:10]:
            key = _normalize_text(item.get("display_key")) or _normalize_text(item.get("action_item_id")) or "ACTION"
            title = _normalize_text(item.get("title")) or "(untitled)"
            lines.append(f"- {key}: {title}")
        if len(items) > 10:
            lines.append(f"...and {len(items) - 10} more.")
        candidate_examples = format_project_member_candidates(project, limit=3)
        if candidate_examples:
            lines.extend(
                [
                    "",
                    "Reply with `assign <ACTION-id> to <owner>` for assignment.",
                    f"Suggestions: {candidate_examples}",
                ]
            )

        sent = await slack_client.send_message(actor_slack_id, text="\n".join(lines))
        if not sent:
            reminder_failed += len(items)
            continue
        for item in items:
            action_item_id = _normalize_text(item.get("action_item_id"))
            if action_item_id:
                reminded_action_item_ids.append(action_item_id)

    if reminded_action_item_ids:
        await collection.update_many(
            {
                "entity_type": "action_item",
                "action_item_id": {"$in": reminded_action_item_ids},
            },
            {"$set": {"followup_actor_reminded_at": now}},
        )

    escalation_hours = max(1, int(getattr(settings, "action_items_followup_pm_escalation_hours", 4)))
    cutoff = now - timedelta(hours=escalation_hours)
    unresolved_items = await collection.find(
        {
            "entity_type": "action_item",
            "status": "open",
            "needs_followup": True,
            "needs_followup_set_at": {"$lte": cutoff},
            "$or": [
                {"followup_escalated_at": {"$exists": False}},
                {"followup_escalated_at": None},
            ],
        }
    ).to_list(length=500)

    if not unresolved_items:
        return {
            "project_id": project_id,
            "actor_reminded": len(reminded_action_item_ids),
            "actor_reminder_failed": reminder_failed,
            "actor_reminder_skipped": reminder_skipped,
            "escalated": 0,
            "notified": False,
        }

    owner_user_id = project_owner_user_id
    if ignore_followup_for_owner:
        return {
            "project_id": project_id,
            "actor_reminded": len(reminded_action_item_ids),
            "actor_reminder_failed": reminder_failed,
            "actor_reminder_skipped": reminder_skipped,
            "escalated": 0,
            "notified": False,
            "reason": "owner_followup_ignored_by_policy",
        }

    owner_member = _active_member_by_user_id(project, owner_user_id)
    if not owner_member:
        return {"project_id": project_id, "escalated": 0, "notified": False, "reason": "owner_not_resolved"}

    owner_slack_id = await resolve_slack_user_id_for_member(
        db=db,
        project=project,
        member=owner_member,
        slack_client=slack_client,
    )
    if not owner_slack_id:
        return {"project_id": project_id, "escalated": 0, "notified": False, "reason": "owner_slack_not_resolved"}

    lines = [
        f"*Action items follow-up escalation:* {len(unresolved_items)} unresolved for {escalation_hours}h+.",
    ]
    for item in unresolved_items[:10]:
        key = _normalize_text(item.get("display_key")) or _normalize_text(item.get("action_item_id")) or "ACTION"
        title = _normalize_text(item.get("title")) or "(untitled)"
        owner_user_id = _normalize_text(item.get("owner_user_id"))
        reason = "unassigned" if not owner_user_id else "follow-up pending"
        lines.append(f"- {key}: {title} (reason: {reason})")
    if len(unresolved_items) > 10:
        lines.append(f"...and {len(unresolved_items) - 10} more.")
    candidate_examples = format_project_member_candidates(project, limit=3)
    if candidate_examples:
        lines.append(f"Owner assignment suggestions (ProMarshal ids): {candidate_examples}")

    sent = await slack_client.send_message(owner_slack_id, text="\n".join(lines))
    if not sent:
        return {"project_id": project_id, "escalated": 0, "notified": False, "reason": "slack_send_failed"}

    escalated_at = datetime.utcnow()
    action_item_ids = [_normalize_text(item.get("action_item_id")) for item in unresolved_items if _normalize_text(item.get("action_item_id"))]
    if action_item_ids:
        await collection.update_many(
            {
                "entity_type": "action_item",
                "action_item_id": {"$in": action_item_ids},
            },
            {"$set": {"followup_escalated_at": escalated_at}},
        )

    return {
        "project_id": project_id,
        "actor_reminded": len(reminded_action_item_ids),
        "actor_reminder_failed": reminder_failed,
        "actor_reminder_skipped": reminder_skipped,
        "escalated": len(action_item_ids),
        "notified": True,
    }
