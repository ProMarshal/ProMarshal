"""Slack welcome DM delivery with per-member idempotency markers."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.security import encryption_service
from app.integrations.channel_index import read_active_project
from app.integrations.slack import slack_bot_service
from app.integrations.slack.client import SlackClient


_WELCOME_PENDING_STALE_SECONDS = 15 * 60
_CHANNEL_CONTEXT_PROVIDER_SLACK = "slack"


def _normalize_email(email: Optional[str]) -> str:
    return str(email or "").strip().lower()


def _project_name(project: Dict[str, Any]) -> str:
    return str(
        project.get("project_name")
        or project.get("name")
        or project.get("project_id")
        or "Your Project"
    ).strip()


def _welcome_message(
    *,
    member_name: Optional[str],
    project_name: str,
    include_switch_tip: bool,
    active_project_name: Optional[str] = None,
) -> str:
    clean_name = str(member_name or "").strip()
    greeting = f"Hi {clean_name} - welcome to ProMarshal." if clean_name else "Welcome to ProMarshal."
    lines: List[str] = [
        greeting,
        "",
        f"You are now invited to project *{project_name}*.",
        "",
        "I can help with your project activities. DM me anytime, or mention @ProMarshal in the channel.",
        "",
        "If you want more details, try:",
        "`/promarshal help`",
    ]
    if include_switch_tip:
        active_project_label = str(active_project_name or "").strip() or "Not set yet"
        lines.extend(
            [
                "",
                "You are part of multiple ProMarshal projects in this Slack workspace.",
                f"Current active project: *{active_project_label}*",
                "",
                'To switch project, type: "switch project"',
                "",
                "You can switch projects anytime.",
            ]
        )
    return "\n".join(lines)


async def _read_member_active_project_name(
    *,
    db: AsyncIOMotorDatabase,
    team_id: str,
    slack_user_id: str,
) -> Optional[str]:
    normalized_team = str(team_id or "").strip()
    normalized_user = str(slack_user_id or "").strip()
    if not normalized_team or not normalized_user:
        return None

    try:
        context_row = await read_active_project(
            db=db,
            provider=_CHANNEL_CONTEXT_PROVIDER_SLACK,
            workspace_id=normalized_team,
            external_user_id=normalized_user,
        )
        active_project_id = str((context_row or {}).get("active_project_id") or "").strip()
        if not active_project_id:
            return None

        active_project = await db.projects.find_one(
            {
                "project_id": active_project_id,
                "integrations.slack.team_id": normalized_team,
                "integrations.slack.status": "connected",
            }
        )
        if not active_project:
            return None
        return _project_name(active_project)
    except Exception as exc:
        print(
            "slack_welcome_active_project_lookup_failed: "
            f"team_id={normalized_team} slack_user_id={normalized_user} error={exc}"
        )
        return None


async def _load_project(
    *,
    db: AsyncIOMotorDatabase,
    project_id: str,
) -> Optional[Dict[str, Any]]:
    normalized = str(project_id or "").strip()
    if not normalized:
        return None
    if ObjectId.is_valid(normalized):
        by_object_id = await db.projects.find_one({"_id": ObjectId(normalized)})
        if by_object_id:
            return by_object_id
    return await db.projects.find_one({"project_id": normalized})


def _active_member_by_email(*, project: Dict[str, Any], member_email: str) -> Optional[Dict[str, Any]]:
    normalized = _normalize_email(member_email)
    for member in project.get("members", []):
        if not isinstance(member, dict):
            continue
        if str(member.get("status") or "").strip().lower() != "active":
            continue
        if _normalize_email(member.get("email")) == normalized:
            return member
    return None


async def _reserve_member_welcome(
    *,
    db: AsyncIOMotorDatabase,
    project_db_id: ObjectId,
    member_email: str,
    pending_token: str,
) -> bool:
    now = datetime.utcnow()
    stale_before = now - timedelta(seconds=_WELCOME_PENDING_STALE_SECONDS)
    normalized_email = _normalize_email(member_email)
    if not normalized_email:
        return False

    result = await db.projects.update_one(
        {
            "_id": project_db_id,
            "members": {
                "$elemMatch": {
                    "email": normalized_email,
                    "status": "active",
                    "integration_ids.slack_welcome_sent_at": {"$exists": False},
                    "$or": [
                        {"integration_ids.slack_welcome_pending_token": {"$exists": False}},
                        {"integration_ids.slack_welcome_pending_at": {"$lt": stale_before}},
                    ],
                }
            },
        },
        {
            "$set": {
                "members.$.integration_ids.slack_welcome_pending_token": pending_token,
                "members.$.integration_ids.slack_welcome_pending_at": now,
                "updated_at": now,
            }
        },
    )
    return result.modified_count > 0


async def _release_member_welcome_pending(
    *,
    db: AsyncIOMotorDatabase,
    project_db_id: ObjectId,
    member_email: str,
    pending_token: str,
) -> None:
    normalized_email = _normalize_email(member_email)
    await db.projects.update_one(
        {
            "_id": project_db_id,
            "members": {
                "$elemMatch": {
                    "email": normalized_email,
                    "integration_ids.slack_welcome_pending_token": pending_token,
                }
            },
        },
        {
            "$unset": {
                "members.$.integration_ids.slack_welcome_pending_token": "",
                "members.$.integration_ids.slack_welcome_pending_at": "",
            },
            "$set": {"updated_at": datetime.utcnow()},
        },
    )


async def _mark_member_welcome_sent(
    *,
    db: AsyncIOMotorDatabase,
    project: Dict[str, Any],
    member_email: str,
    slack_user_id: str,
    pending_token: str,
) -> None:
    normalized_email = _normalize_email(member_email)
    now = datetime.utcnow()
    await db.projects.update_one(
        {
            "_id": project["_id"],
            "members": {
                "$elemMatch": {
                    "email": normalized_email,
                    "integration_ids.slack_welcome_pending_token": pending_token,
                }
            },
        },
        {
            "$set": {
                "members.$.integration_ids.slack_welcome_sent_at": now,
                "members.$.integration_ids.slack_welcome_sent_project_id": str(
                    project.get("project_id") or ""
                ),
                "members.$.integration_ids.slack_welcome_sent_team_id": str(
                    ((project.get("integrations") or {}).get("slack") or {}).get("team_id") or ""
                ),
                "members.$.integration_ids.slack_welcome_sent_channel": str(slack_user_id or ""),
                "updated_at": now,
            },
            "$unset": {
                "members.$.integration_ids.slack_welcome_pending_token": "",
                "members.$.integration_ids.slack_welcome_pending_at": "",
            },
        },
    )


async def _count_member_workspace_projects(
    *,
    db: AsyncIOMotorDatabase,
    team_id: str,
    member_email: str,
) -> int:
    normalized_team = str(team_id or "").strip()
    normalized_email = _normalize_email(member_email)
    if not normalized_team or not normalized_email:
        return 0
    return int(
        await db.projects.count_documents(
            {
                "integrations.slack.team_id": normalized_team,
                "integrations.slack.status": "connected",
                "members": {
                    "$elemMatch": {
                        "status": "active",
                        "email": normalized_email,
                    }
                },
            }
        )
    )


async def _resolve_member_slack_user_id(
    *,
    db: AsyncIOMotorDatabase,
    project: Dict[str, Any],
    member: Dict[str, Any],
    lookup_client: SlackClient,
) -> Optional[str]:
    """Resolve the Slack user ID for a member.

    Always performs a live email lookup first (email-first pattern) so that a
    stale or corrupted cached ID is never blindly trusted. The returned ID is
    cross-validated: we call users.info to confirm the Slack user's email
    matches the member's ProMarshal email before caching or using it.

    The cached value is only used as a fallback when the live Slack API lookup
    fails (e.g. network error, rate-limit exhaustion after retries).
    """
    member_email = _normalize_email(member.get("email"))
    if not member_email:
        return None

    # --- Primary path: live lookup by email ---
    resolved_user_id = await lookup_client.find_user_by_email(member_email)
    if resolved_user_id:
        # Cross-validate: confirm the returned Slack user's email matches the
        # member's email.  This guards against Slack API anomalies or workspace
        # mis-configurations that could return a wrong user.
        confirmed_email = await lookup_client.get_user_email(resolved_user_id)
        if confirmed_email and _normalize_email(confirmed_email) != member_email:
            print(
                "slack_welcome_email_mismatch_rejected: "
                f"project_id={project.get('project_id')} member_email={member_email} "
                f"slack_user_id={resolved_user_id} slack_email={confirmed_email}"
            )
            return None

        # Persist confirmed ID to cache (update only — never overwrite a
        # *different* already-stored ID without a conflict log).
        integration_ids = member.get("integration_ids") or {}
        cached_slack_user_id = str(integration_ids.get("slack_user_id") or "").strip()
        if cached_slack_user_id and cached_slack_user_id != resolved_user_id:
            print(
                "slack_welcome_cache_stale_overwrite: "
                f"project_id={project.get('project_id')} member_email={member_email} "
                f"old_cached_id={cached_slack_user_id} new_id={resolved_user_id}"
            )

        try:
            await db.projects.update_one(
                {"_id": project["_id"], "members.email": member_email},
                {
                    "$set": {
                        "members.$.integration_ids.slack_user_id": resolved_user_id,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
        except Exception as exc:
            print(
                "slack_welcome_persist_slack_user_id_failed: "
                f"project_id={project.get('project_id')} email={member_email} error={exc}"
            )
        return resolved_user_id

    # --- Fallback: use cached ID only when live lookup fails ---
    integration_ids = member.get("integration_ids") or {}
    cached_slack_user_id = str(integration_ids.get("slack_user_id") or "").strip()
    if cached_slack_user_id:
        print(
            "slack_welcome_using_cached_id_fallback: "
            f"project_id={project.get('project_id')} member_email={member_email} "
            f"cached_slack_user_id={cached_slack_user_id}"
        )
        return cached_slack_user_id

    return None


async def _send_member_welcome_with_context(
    *,
    db: AsyncIOMotorDatabase,
    project: Dict[str, Any],
    member: Dict[str, Any],
    slack_client,
    lookup_client: SlackClient,
) -> Dict[str, Any]:
    member_email = _normalize_email(member.get("email"))
    if not member_email:
        return {"status": "invalid_member_email"}

    integration_ids = member.get("integration_ids") or {}
    if integration_ids.get("slack_welcome_sent_at"):
        return {"status": "already_sent", "member_email": member_email}

    pending_token = uuid4().hex
    reserved = await _reserve_member_welcome(
        db=db,
        project_db_id=project["_id"],
        member_email=member_email,
        pending_token=pending_token,
    )
    if not reserved:
        return {"status": "already_sent_or_inflight", "member_email": member_email}

    try:
        slack_user_id = await _resolve_member_slack_user_id(
            db=db,
            project=project,
            member=member,
            lookup_client=lookup_client,
        )
        if not slack_user_id:
            await _release_member_welcome_pending(
                db=db,
                project_db_id=project["_id"],
                member_email=member_email,
                pending_token=pending_token,
            )
            return {"status": "slack_user_not_resolved", "member_email": member_email}

        team_id = str(((project.get("integrations") or {}).get("slack") or {}).get("team_id") or "")
        member_project_count = await _count_member_workspace_projects(
            db=db,
            team_id=team_id,
            member_email=member_email,
        )
        include_switch_tip = member_project_count > 1
        active_project_name = None
        if include_switch_tip:
            active_project_name = await _read_member_active_project_name(
                db=db,
                team_id=team_id,
                slack_user_id=slack_user_id,
            )
        message = _welcome_message(
            member_name=str(member.get("name") or "").strip() or None,
            project_name=_project_name(project),
            include_switch_tip=include_switch_tip,
            active_project_name=active_project_name,
        )
        response = await slack_client.chat_postMessage(channel=slack_user_id, text=message)
        if not bool(response.get("ok")):
            await _release_member_welcome_pending(
                db=db,
                project_db_id=project["_id"],
                member_email=member_email,
                pending_token=pending_token,
            )
            return {
                "status": "send_failed",
                "member_email": member_email,
                "slack_user_id": slack_user_id,
            }

        await _mark_member_welcome_sent(
            db=db,
            project=project,
            member_email=member_email,
            slack_user_id=slack_user_id,
            pending_token=pending_token,
        )
        return {
            "status": "sent",
            "member_email": member_email,
            "slack_user_id": slack_user_id,
            "include_switch_tip": include_switch_tip,
            "member_project_count": member_project_count,
            "active_project_name": active_project_name,
        }
    except Exception as exc:
        await _release_member_welcome_pending(
            db=db,
            project_db_id=project["_id"],
            member_email=member_email,
            pending_token=pending_token,
        )
        return {
            "status": "send_error",
            "member_email": member_email,
            "error": str(exc),
        }


async def send_project_welcome_to_member_detailed(
    *,
    db: AsyncIOMotorDatabase,
    project_id: str,
    member_email: str,
) -> Dict[str, Any]:
    project = await _load_project(db=db, project_id=project_id)
    if not project:
        return {"status": "project_not_found", "project_id": project_id}

    slack_cfg = ((project.get("integrations") or {}).get("slack") or {})
    if str(slack_cfg.get("status") or "").strip().lower() != "connected":
        return {
            "status": "integration_not_connected",
            "project_id": str(project.get("project_id") or project_id),
            "member_email": _normalize_email(member_email),
        }

    member = _active_member_by_email(project=project, member_email=member_email)
    if not member:
        return {
            "status": "member_not_found",
            "project_id": str(project.get("project_id") or project_id),
            "member_email": _normalize_email(member_email),
        }

    encrypted_token = slack_cfg.get("access_token")
    if not encrypted_token:
        return {
            "status": "slack_token_missing",
            "project_id": str(project.get("project_id") or project_id),
            "member_email": _normalize_email(member_email),
        }

    decrypted_token = encryption_service.decrypt(encrypted_token)
    slack_client = slack_bot_service.get_slack_client(encrypted_token)
    lookup_client = SlackClient(decrypted_token)
    result = await _send_member_welcome_with_context(
        db=db,
        project=project,
        member=member,
        slack_client=slack_client,
        lookup_client=lookup_client,
    )
    result["project_id"] = str(project.get("project_id") or project_id)
    return result


async def send_project_welcome_to_all_active_members_detailed(
    *,
    db: AsyncIOMotorDatabase,
    project_id: str,
) -> Dict[str, Any]:
    project = await _load_project(db=db, project_id=project_id)
    if not project:
        return {"status": "project_not_found", "project_id": project_id}

    project_key = str(project.get("project_id") or project_id)
    slack_cfg = ((project.get("integrations") or {}).get("slack") or {})
    if str(slack_cfg.get("status") or "").strip().lower() != "connected":
        return {"status": "integration_not_connected", "project_id": project_key}

    encrypted_token = slack_cfg.get("access_token")
    if not encrypted_token:
        return {"status": "slack_token_missing", "project_id": project_key}

    decrypted_token = encryption_service.decrypt(encrypted_token)
    slack_client = slack_bot_service.get_slack_client(encrypted_token)
    lookup_client = SlackClient(decrypted_token)
    active_members = [
        member
        for member in project.get("members", [])
        if isinstance(member, dict) and str(member.get("status") or "").strip().lower() == "active"
    ]

    outcomes: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {
        "sent": 0,
        "already_sent": 0,
        "already_sent_or_inflight": 0,
        "slack_user_not_resolved": 0,
        "send_failed": 0,
        "send_error": 0,
        "invalid_member_email": 0,
        "other": 0,
    }
    for member in active_members:
        outcome = await _send_member_welcome_with_context(
            db=db,
            project=project,
            member=member,
            slack_client=slack_client,
            lookup_client=lookup_client,
        )
        outcomes.append(outcome)
        status_key = str(outcome.get("status") or "other")
        if status_key in counts:
            counts[status_key] += 1
        else:
            counts["other"] += 1

    return {
        "status": "ok",
        "project_id": project_key,
        "member_count": len(active_members),
        "counts": counts,
        "outcomes": outcomes,
    }
