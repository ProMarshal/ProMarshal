"""Slack member identity mapping sync helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.security import encryption_service
from app.integrations.slack.client import SlackClient


def _normalize_email(email: Optional[str]) -> str:
    return str(email or "").strip().lower()


async def _load_project_for_sync(
    *,
    db: AsyncIOMotorDatabase,
    project_id: str,
) -> Optional[Dict[str, Any]]:
    normalized = str(project_id or "").strip()
    if not normalized:
        return None

    project = None
    if ObjectId.is_valid(normalized):
        project = await db.projects.find_one({"_id": ObjectId(normalized)})
        if project:
            return project
    return await db.projects.find_one({"project_id": normalized})


def _find_active_member_by_email(
    *,
    project: Dict[str, Any],
    member_email: str,
) -> Optional[Dict[str, Any]]:
    for member in project.get("members", []):
        if not isinstance(member, dict):
            continue
        if str(member.get("status") or "").strip().lower() != "active":
            continue
        if _normalize_email(member.get("email")) == member_email:
            return member
    return None


def _slack_integration(project: Dict[str, Any]) -> Dict[str, Any]:
    return ((project.get("integrations") or {}).get("slack") or {})


async def _sync_member_with_project(
    *,
    db: AsyncIOMotorDatabase,
    project: Dict[str, Any],
    member_email: str,
    slack_client: SlackClient,
    overwrite: bool = False,
) -> Dict[str, Any]:
    normalized_email = _normalize_email(member_email)
    if not normalized_email:
        return {"status": "invalid_member_email", "member_email": member_email}

    member = _find_active_member_by_email(project=project, member_email=normalized_email)
    if not member:
        return {"status": "member_not_found", "member_email": normalized_email}

    integration_ids = member.get("integration_ids") or {}
    existing_slack_user_id = str(integration_ids.get("slack_user_id") or "").strip() or None
    resolved_slack_user_id = await slack_client.find_user_by_email(normalized_email)
    if not resolved_slack_user_id:
        return {
            "status": "not_found_in_slack_workspace",
            "member_email": normalized_email,
            "existing_slack_user_id": existing_slack_user_id,
        }

    # Cross-validate: confirm the Slack user returned for this email actually
    # has the same email on their Slack profile.  This prevents writing a wrong
    # Slack user ID when the Slack API returns an unexpected/mismatched user.
    confirmed_email = await slack_client.get_user_email(resolved_slack_user_id)
    if confirmed_email is not None and _normalize_email(confirmed_email) != normalized_email:
        print(
            "slack_sync_email_mismatch_rejected: "
            f"project_id={project.get('project_id')} member_email={normalized_email} "
            f"slack_user_id={resolved_slack_user_id} slack_email={confirmed_email}"
        )
        return {
            "status": "email_mismatch_rejected",
            "member_email": normalized_email,
            "slack_user_id": resolved_slack_user_id,
            "slack_profile_email": confirmed_email,
        }

    if existing_slack_user_id == resolved_slack_user_id:
        return {
            "status": "already_mapped",
            "member_email": normalized_email,
            "slack_user_id": resolved_slack_user_id,
        }

    if existing_slack_user_id and existing_slack_user_id != resolved_slack_user_id and not overwrite:
        return {
            "status": "conflict",
            "member_email": normalized_email,
            "existing_slack_user_id": existing_slack_user_id,
            "resolved_slack_user_id": resolved_slack_user_id,
        }

    result = await db.projects.update_one(
        {"_id": project["_id"], "members.email": normalized_email},
        {
            "$set": {
                "members.$.integration_ids.slack_user_id": resolved_slack_user_id,
                "updated_at": datetime.utcnow(),
            }
        },
    )
    if result.modified_count <= 0:
        return {
            "status": "write_not_applied",
            "member_email": normalized_email,
            "slack_user_id": resolved_slack_user_id,
        }

    return {
        "status": "mapped",
        "member_email": normalized_email,
        "slack_user_id": resolved_slack_user_id,
        "overwrote_existing": bool(existing_slack_user_id and existing_slack_user_id != resolved_slack_user_id),
    }


async def sync_single_member_slack_id_detailed(
    *,
    db: AsyncIOMotorDatabase,
    project_id: str,
    member_email: str,
    overwrite: bool = False,
) -> Dict[str, Any]:
    project = await _load_project_for_sync(db=db, project_id=project_id)
    if not project:
        return {"status": "project_not_found", "project_id": project_id, "member_email": member_email}

    slack_cfg = _slack_integration(project)
    if str(slack_cfg.get("status") or "").strip().lower() != "connected":
        return {
            "status": "integration_not_connected",
            "project_id": str(project.get("project_id") or project_id),
            "member_email": member_email,
        }

    encrypted_token = slack_cfg.get("access_token")
    if not encrypted_token:
        return {
            "status": "slack_token_missing",
            "project_id": str(project.get("project_id") or project_id),
            "member_email": member_email,
        }

    try:
        slack_token = encryption_service.decrypt(encrypted_token)
    except Exception as exc:
        return {
            "status": "slack_token_decrypt_failed",
            "project_id": str(project.get("project_id") or project_id),
            "member_email": member_email,
            "error": str(exc),
        }

    client = SlackClient(slack_token)
    result = await _sync_member_with_project(
        db=db,
        project=project,
        member_email=member_email,
        slack_client=client,
        overwrite=overwrite,
    )
    result["project_id"] = str(project.get("project_id") or project_id)
    return result


async def sync_project_slack_member_ids_detailed(
    *,
    db: AsyncIOMotorDatabase,
    project_id: str,
    overwrite: bool = False,
) -> Dict[str, Any]:
    started_at = datetime.utcnow()
    project = await _load_project_for_sync(db=db, project_id=project_id)
    if not project:
        return {"status": "project_not_found", "project_id": project_id}

    project_key = str(project.get("project_id") or project_id)
    slack_cfg = _slack_integration(project)
    if str(slack_cfg.get("status") or "").strip().lower() != "connected":
        return {"status": "integration_not_connected", "project_id": project_key}

    encrypted_token = slack_cfg.get("access_token")
    if not encrypted_token:
        return {"status": "slack_token_missing", "project_id": project_key}

    try:
        slack_token = encryption_service.decrypt(encrypted_token)
    except Exception as exc:
        return {
            "status": "slack_token_decrypt_failed",
            "project_id": project_key,
            "error": str(exc),
        }

    client = SlackClient(slack_token)
    active_emails: List[str] = []
    skipped_already_mapped: int = 0
    for member in project.get("members", []):
        if not isinstance(member, dict):
            continue
        if str(member.get("status") or "").strip().lower() != "active":
            continue
        email = _normalize_email(member.get("email"))
        if not email:
            continue
        active_emails.append(email)

    outcomes: List[Dict[str, Any]] = []
    for email in active_emails:
        outcome = await _sync_member_with_project(
            db=db,
            project=project,
            member_email=email,
            slack_client=client,
            overwrite=overwrite,
        )
        outcomes.append(outcome)

    counts = {
        "mapped": 0,
        "already_mapped": 0,
        "skipped_already_mapped": skipped_already_mapped,
        "not_found_in_slack_workspace": 0,
        "conflict": 0,
        "member_not_found": 0,
        "write_not_applied": 0,
        "other": 0,
    }
    for item in outcomes:
        status = str(item.get("status") or "other")
        if status in counts:
            counts[status] += 1
        else:
            counts["other"] += 1

    now = datetime.utcnow()
    project_update_applied = False
    try:
        project_update = await db.projects.update_one(
            {"_id": project["_id"]},
            {
                "$set": {
                    "integrations.slack.last_sync_at": now,
                    "updated_at": now,
                }
            },
        )
        project_update_applied = bool(project_update.acknowledged)
    except Exception as exc:
        print(
            "Slack member sync: failed to persist sync timestamp "
            f"project_id={project_key} error={exc}"
        )

    duration_ms = max(0.0, (datetime.utcnow() - started_at).total_seconds() * 1000.0)
    return {
        "status": "ok",
        "project_id": project_key,
        "members_attempted": len(active_emails),
        "members_skipped_already_mapped": skipped_already_mapped,
        "counts": counts,
        "project_update_applied": project_update_applied,
        "duration_ms": duration_ms,
        "outcomes": outcomes,
    }


async def link_member_slack_id_detailed(
    *,
    db: AsyncIOMotorDatabase,
    project_id: str,
    member_email: str,
    slack_user_id: str,
    overwrite: bool = False,
    validate_with_slack: bool = True,
) -> Dict[str, Any]:
    project = await _load_project_for_sync(db=db, project_id=project_id)
    if not project:
        return {"status": "project_not_found", "project_id": project_id}

    project_key = str(project.get("project_id") or project_id)
    normalized_email = _normalize_email(member_email)
    normalized_slack_user_id = str(slack_user_id or "").strip()
    if not normalized_email:
        return {"status": "invalid_member_email", "project_id": project_key}
    if not normalized_slack_user_id:
        return {"status": "invalid_slack_user_id", "project_id": project_key}

    member = _find_active_member_by_email(project=project, member_email=normalized_email)
    if not member:
        return {
            "status": "member_not_found",
            "project_id": project_key,
            "member_email": normalized_email,
        }

    existing = str(((member.get("integration_ids") or {}).get("slack_user_id") or "")).strip() or None
    if existing == normalized_slack_user_id:
        return {
            "status": "already_mapped",
            "project_id": project_key,
            "member_email": normalized_email,
            "slack_user_id": normalized_slack_user_id,
        }
    if existing and existing != normalized_slack_user_id and not overwrite:
        return {
            "status": "conflict",
            "project_id": project_key,
            "member_email": normalized_email,
            "existing_slack_user_id": existing,
            "requested_slack_user_id": normalized_slack_user_id,
        }

    validation_email = None
    validation_note = None
    if validate_with_slack:
        slack_cfg = _slack_integration(project)
        encrypted_token = slack_cfg.get("access_token")
        if str(slack_cfg.get("status") or "").strip().lower() == "connected" and encrypted_token:
            try:
                token = encryption_service.decrypt(encrypted_token)
                client = SlackClient(token)
                validation_email = await client.get_user_email(normalized_slack_user_id)
                if not validation_email:
                    validation_note = "slack_user_email_not_available_or_not_found"
            except Exception as exc:
                validation_note = f"slack_validation_failed:{exc}"
        normalized_validation_email = _normalize_email(validation_email)
        if (
            normalized_validation_email
            and normalized_validation_email != normalized_email
            and not overwrite
        ):
            return {
                "status": "validation_email_mismatch",
                "project_id": project_key,
                "member_email": normalized_email,
                "slack_user_id": normalized_slack_user_id,
                "validation_email": normalized_validation_email,
                "validation_note": validation_note,
            }

    result = await db.projects.update_one(
        {"_id": project["_id"], "members.email": normalized_email},
        {
            "$set": {
                "members.$.integration_ids.slack_user_id": normalized_slack_user_id,
                "updated_at": datetime.utcnow(),
            }
        },
    )
    if result.modified_count <= 0:
        return {
            "status": "write_not_applied",
            "project_id": project_key,
            "member_email": normalized_email,
            "slack_user_id": normalized_slack_user_id,
            "validation_email": validation_email,
            "validation_note": validation_note,
        }

    return {
        "status": "linked",
        "project_id": project_key,
        "member_email": normalized_email,
        "slack_user_id": normalized_slack_user_id,
        "validation_email": validation_email,
        "validation_note": validation_note,
        "overwrote_existing": bool(existing and existing != normalized_slack_user_id),
    }
