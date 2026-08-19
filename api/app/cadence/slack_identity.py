"""Slack identity resolution helpers for reminder flows."""

from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase


async def _store_slack_user_id_cache(
    db: AsyncIOMotorDatabase,
    project: Dict[str, Any],
    member_email: str,
    slack_user_id: str,
) -> None:
    """Best-effort cache update for members[].integration_ids.slack_user_id."""
    try:
        await db.projects.update_one(
            {"_id": project["_id"], "members.email": member_email},
            {"$set": {"members.$.integration_ids.slack_user_id": slack_user_id}},
        )
    except Exception as exc:
        print(f"Failed to persist Slack user-id cache for {member_email}: {exc}")


async def resolve_slack_user_id_for_member(
    db: AsyncIOMotorDatabase,
    project: Dict[str, Any],
    member: Dict[str, Any],
    slack_client: Any,
) -> Optional[str]:
    """
    Resolve Slack user id with email as primary source and cache as optional fallback.

    Priority:
    1) Email lookup via Slack API (source-of-truth)
    2) Cached members[].integration_ids.slack_user_id (optimization/fallback only)
    """
    member_email = (member.get("email") or "").strip()
    integration_ids = member.get("integration_ids") or {}
    cached_slack_user_id = integration_ids.get("slack_user_id")

    if member_email:
        resolved_user_id = await slack_client.find_user_by_email(member_email)
        if resolved_user_id:
            if resolved_user_id != cached_slack_user_id:
                await _store_slack_user_id_cache(
                    db=db,
                    project=project,
                    member_email=member_email,
                    slack_user_id=resolved_user_id,
                )
            return resolved_user_id

    if cached_slack_user_id:
        if member_email:
            print(
                f"Using cached Slack user ID for {member_email}; "
                "email lookup unavailable or unresolved."
            )
        return cached_slack_user_id

    return None
