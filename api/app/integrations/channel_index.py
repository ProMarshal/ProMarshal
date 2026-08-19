"""Provider-agnostic DM active-project context store."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase

from app.core.database import get_channel_index_collection


def _collection(db: Optional[AsyncIOMotorDatabase]) -> AsyncIOMotorCollection:
    if db is not None:
        return db["channel_index"]
    return get_channel_index_collection()


def _norm(value: Any) -> str:
    return str(value or "").strip()


async def read_active_project(
    *,
    db: Optional[AsyncIOMotorDatabase],
    provider: str,
    workspace_id: str,
    external_user_id: str,
) -> Optional[Dict[str, Any]]:
    """Read active project context for a provider/workspace/external user key."""
    normalized_provider = _norm(provider).lower()
    normalized_workspace = _norm(workspace_id)
    normalized_user = _norm(external_user_id)
    if not (normalized_provider and normalized_workspace and normalized_user):
        return None

    doc = await _collection(db).find_one(
        {
            "provider": normalized_provider,
            "workspace_id": normalized_workspace,
            "external_user_id": normalized_user,
        },
        {
            "_id": 0,
            "provider": 1,
            "workspace_id": 1,
            "external_user_id": 1,
            "active_project_id": 1,
            "active_project_name": 1,
            "updated_at": 1,
            "updated_by": 1,
        },
    )
    if not isinstance(doc, dict):
        return None
    active_project_id = _norm(doc.get("active_project_id"))
    if not active_project_id:
        return None
    doc["active_project_id"] = active_project_id
    doc["active_project_name"] = _norm(doc.get("active_project_name")) or active_project_id
    return doc


async def write_active_project(
    *,
    db: Optional[AsyncIOMotorDatabase],
    provider: str,
    workspace_id: str,
    external_user_id: str,
    project_id: str,
    project_name: Optional[str] = None,
    updated_by: str = "manual_selection",
) -> bool:
    """Upsert active project context for a provider/workspace/external user key."""
    normalized_provider = _norm(provider).lower()
    normalized_workspace = _norm(workspace_id)
    normalized_user = _norm(external_user_id)
    normalized_project_id = _norm(project_id)
    if not (normalized_provider and normalized_workspace and normalized_user and normalized_project_id):
        return False

    now = datetime.utcnow()
    normalized_updated_by = _norm(updated_by) or "manual_selection"
    normalized_project_name = _norm(project_name) or normalized_project_id

    result = await _collection(db).update_one(
        {
            "provider": normalized_provider,
            "workspace_id": normalized_workspace,
            "external_user_id": normalized_user,
        },
        {
            "$set": {
                "provider": normalized_provider,
                "workspace_id": normalized_workspace,
                "external_user_id": normalized_user,
                "active_project_id": normalized_project_id,
                "active_project_name": normalized_project_name,
                "updated_at": now,
                "updated_by": normalized_updated_by,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return bool(result.acknowledged)


async def clear_active_project(
    *,
    db: Optional[AsyncIOMotorDatabase],
    provider: str,
    workspace_id: str,
    external_user_id: str,
) -> int:
    """Clear active project context for one provider/workspace/external user key."""
    normalized_provider = _norm(provider).lower()
    normalized_workspace = _norm(workspace_id)
    normalized_user = _norm(external_user_id)
    if not (normalized_provider and normalized_workspace and normalized_user):
        return 0

    result = await _collection(db).delete_many(
        {
            "provider": normalized_provider,
            "workspace_id": normalized_workspace,
            "external_user_id": normalized_user,
        }
    )
    return int(result.deleted_count or 0)


async def clear_active_project_rows(
    *,
    db: Optional[AsyncIOMotorDatabase],
    provider: Optional[str] = None,
    workspace_id: Optional[str] = None,
    external_user_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> int:
    """Clear active project context rows by optional key filters."""
    query: Dict[str, Any] = {}
    normalized_provider = _norm(provider).lower()
    normalized_workspace = _norm(workspace_id)
    normalized_user = _norm(external_user_id)
    normalized_project_id = _norm(project_id)

    if normalized_provider:
        query["provider"] = normalized_provider
    if normalized_workspace:
        query["workspace_id"] = normalized_workspace
    if normalized_user:
        query["external_user_id"] = normalized_user
    if normalized_project_id:
        query["active_project_id"] = normalized_project_id
    if not query:
        return 0

    result = await _collection(db).delete_many(query)
    return int(result.deleted_count or 0)

