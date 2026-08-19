"""Runtime helpers shared by Cortex tool adapters."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Dict, Optional

from app.core.database import get_database

ACTOR_SELF_ALIASES = {"me", "myself", "mine"}
_TRIM_WRAP_CHARS_RE = re.compile(r"^[\s`\"'“”‘’]+|[\s`\"'“”‘’]+$")
_TRAILING_DELIMITER_RE = re.compile(r"[;:,.!?]+$")


def normalize_identity_hint(value: Any) -> str:
    """Normalize assignee/member hint text for robust actor/self resolution."""
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = _TRIM_WRAP_CHARS_RE.sub("", text).strip()
    normalized = _TRAILING_DELIMITER_RE.sub("", normalized).strip()
    return normalized


async def load_project(project_id: str) -> Optional[Dict[str, Any]]:
    """Load a project document by project_id."""
    normalized = str(project_id or "").strip()
    if not normalized:
        return None

    db = get_database()
    if db is None:
        return None
    return await db.projects.find_one({"project_id": normalized})


def resolve_actor_member(
    project: Dict[str, Any],
    *,
    actor_email: Optional[str] = None,
    slack_user_id: Optional[str] = None,
    jira_account_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve project member by provider IDs first, then email fallback."""
    normalized_email = str(actor_email or "").strip().lower()
    normalized_user_id = str(slack_user_id or "").strip()
    normalized_jira_account_id = str(jira_account_id or "").strip()

    id_matched_member: Optional[Dict[str, Any]] = None
    if normalized_user_id:
        for member in project.get("members") or []:
            if not isinstance(member, dict):
                continue
            if str(member.get("status") or "").strip().lower() != "active":
                continue
            integration_ids = member.get("integration_ids") or {}
            if str(integration_ids.get("slack_user_id") or "").strip() == normalized_user_id:
                id_matched_member = member
                break

    if id_matched_member is None and normalized_jira_account_id:
        for member in project.get("members") or []:
            if not isinstance(member, dict):
                continue
            if str(member.get("status") or "").strip().lower() != "active":
                continue
            integration_ids = member.get("integration_ids") or {}
            if str(integration_ids.get("jira_account_id") or "").strip() == normalized_jira_account_id:
                id_matched_member = member
                break

    email_matched_member: Optional[Dict[str, Any]] = None
    if normalized_email:
        for member in project.get("members") or []:
            if not isinstance(member, dict):
                continue
            if str(member.get("status") or "").strip().lower() != "active":
                continue
            member_email = str(member.get("email") or "").strip().lower()
            if member_email == normalized_email:
                email_matched_member = member
                break

    if id_matched_member and email_matched_member and id_matched_member is not email_matched_member:
        print(
            "actor_identity_conflict_resolved_to_id: "
            f"id_email={str((id_matched_member or {}).get('email') or '').strip().lower()} "
            f"email_match={str((email_matched_member or {}).get('email') or '').strip().lower()}"
        )

    if id_matched_member is not None:
        return id_matched_member
    if email_matched_member is not None:
        return email_matched_member

    return None


def resolve_actor_email(
    project: Dict[str, Any],
    *,
    actor_email: Optional[str] = None,
    slack_user_id: Optional[str] = None,
    jira_account_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve actor email by provider IDs first, then email fallback."""
    member = resolve_actor_member(
        project,
        actor_email=actor_email,
        slack_user_id=slack_user_id,
        jira_account_id=jira_account_id,
    )
    if not member:
        return None
    email = str(member.get("email") or "").strip().lower()
    return email or None


def is_actor_self_alias(value: Any) -> bool:
    """Return True when value is a self-referential actor alias."""
    normalized = normalize_identity_hint(value).lower()
    return normalized in ACTOR_SELF_ALIASES


def resolve_self_alias_to_actor_email(
    value: Any,
    *,
    project: Dict[str, Any],
    actor_email: Optional[str] = None,
    slack_user_id: Optional[str] = None,
    jira_account_id: Optional[str] = None,
) -> tuple[Optional[str], bool]:
    """
    Resolve self alias (me/myself/mine) to actor email.

    Returns:
    - (resolved_value, True) when value was self alias (resolved or unresolved)
    - (normalized_value, False) when value was not self alias
    """
    normalized = normalize_identity_hint(value)
    if not is_actor_self_alias(normalized):
        return normalized or None, False

    normalized_slack_user_id = str(slack_user_id or "").strip() or None
    normalized_jira_account_id = str(jira_account_id or "").strip() or None
    normalized_actor_email = (actor_email or "").strip().lower() or None

    # Strict safety for self-alias:
    # when provider IDs exist, resolve only by IDs (no email fallback).
    if normalized_slack_user_id or normalized_jira_account_id:
        member = resolve_actor_member(
            project,
            actor_email=None,
            slack_user_id=normalized_slack_user_id,
            jira_account_id=normalized_jira_account_id,
        )
        if isinstance(member, dict):
            resolved_actor_email = str(member.get("email") or "").strip().lower() or None
            if resolved_actor_email:
                return resolved_actor_email, True
        print(
            "self_alias_identity_unresolved_from_ids: "
            f"slack_user_id={normalized_slack_user_id or 'none'} "
            f"jira_account_id={normalized_jira_account_id or 'none'}"
        )
        return None, True

    resolved_actor_email = resolve_actor_email(
        project,
        actor_email=normalized_actor_email,
        slack_user_id=None,
        jira_account_id=None,
    )
    if resolved_actor_email:
        return resolved_actor_email, True
    return None, True


def to_iso(value: Any) -> Optional[str]:
    """Convert datetime-like value to ISO string."""
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value or "").strip()
    return text or None


def serialize_task_like(item: Any) -> Dict[str, Any]:
    """Serialize task-like DTO/object/dict for tool outputs."""
    if hasattr(item, "model_dump"):
        data = item.model_dump(mode="json")
    elif isinstance(item, dict):
        data = dict(item)
    else:
        data = {
            "value": str(item),
        }

    for key in ("created_at", "updated_at", "due_date", "synced_at"):
        if key in data:
            data[key] = to_iso(data.get(key))
    return data
