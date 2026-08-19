"""Shared pending-interaction helpers (cross-feature runtime state)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from app.core.database import get_pending_interactions_collection


PENDING_STATUS = "pending"
TERMINAL_STATUSES = {"resolved", "cancelled", "expired", "stale_target"}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


async def create_pending_interaction(
    *,
    interaction_type: str,
    project_id: str,
    target_user_id: str,
    context_payload: Optional[Dict[str, Any]] = None,
    priority: int = 0,
    source: str = "system",
    escalate_at: Optional[datetime] = None,
    expires_at: Optional[datetime] = None,
    provider: Optional[str] = None,
    workspace_id: Optional[str] = None,
    external_user_id: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> Dict[str, Any]:
    now = datetime.utcnow()
    doc = {
        "interaction_id": f"pi-{uuid.uuid4()}",
        "type": _normalize_text(interaction_type),
        "project_id": _normalize_text(project_id),
        "target_user_id": _normalize_text(target_user_id),
        "status": PENDING_STATUS,
        "priority": int(priority),
        "source": _normalize_text(source),
        "provider": _normalize_text(provider) or None,
        "workspace_id": _normalize_text(workspace_id) or None,
        "external_user_id": _normalize_text(external_user_id) or None,
        "channel_id": _normalize_text(channel_id) or None,
        "context_payload": dict(context_payload or {}),
        "created_at": now,
        "updated_at": now,
        "escalate_at": escalate_at,
        "expires_at": expires_at,
    }
    collection = get_pending_interactions_collection()
    await collection.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def find_pending_interaction(
    *,
    project_id: str,
    target_user_id: str,
    interaction_type: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    now = datetime.utcnow()
    query: Dict[str, Any] = {
        "project_id": _normalize_text(project_id),
        "target_user_id": _normalize_text(target_user_id),
        "status": PENDING_STATUS,
        "$or": [
            {"expires_at": {"$exists": False}},
            {"expires_at": None},
            {"expires_at": {"$gt": now}},
        ],
    }
    normalized_type = _normalize_text(interaction_type)
    if normalized_type:
        query["type"] = normalized_type
    collection = get_pending_interactions_collection()
    cleanup_query: Dict[str, Any] = {
        "project_id": _normalize_text(project_id),
        "target_user_id": _normalize_text(target_user_id),
        "status": PENDING_STATUS,
        "expires_at": {"$lte": now},
    }
    if normalized_type:
        cleanup_query["type"] = normalized_type
    await collection.delete_many(cleanup_query)
    doc = await collection.find_one(
        query,
        sort=[("priority", -1), ("created_at", -1)],
    )
    if not isinstance(doc, dict):
        return None
    doc.pop("_id", None)
    return doc


async def list_pending_interactions_by_external_identity(
    *,
    provider: str,
    workspace_id: str,
    external_user_id: str,
    interaction_type: Optional[str] = None,
    limit: int = 5,
) -> list[Dict[str, Any]]:
    now = datetime.utcnow()
    query: Dict[str, Any] = {
        "provider": _normalize_text(provider),
        "workspace_id": _normalize_text(workspace_id),
        "external_user_id": _normalize_text(external_user_id),
        "status": PENDING_STATUS,
        "$or": [
            {"expires_at": {"$exists": False}},
            {"expires_at": None},
            {"expires_at": {"$gt": now}},
        ],
    }
    normalized_type = _normalize_text(interaction_type)
    if normalized_type:
        query["type"] = normalized_type

    collection = get_pending_interactions_collection()
    cleanup_query: Dict[str, Any] = {
        "provider": _normalize_text(provider),
        "workspace_id": _normalize_text(workspace_id),
        "external_user_id": _normalize_text(external_user_id),
        "status": PENDING_STATUS,
        "expires_at": {"$lte": now},
    }
    if normalized_type:
        cleanup_query["type"] = normalized_type
    await collection.delete_many(cleanup_query)
    docs = (
        await collection.find(query)
        .sort([("priority", -1), ("created_at", -1)])
        .limit(max(1, int(limit)))
        .to_list(length=max(1, int(limit)))
    )
    normalized_docs: list[Dict[str, Any]] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        doc.pop("_id", None)
        normalized_docs.append(doc)
    return normalized_docs


async def delete_pending_interaction(*, interaction_id: str) -> bool:
    collection = get_pending_interactions_collection()
    result = await collection.delete_one({"interaction_id": _normalize_text(interaction_id)})
    return bool(result.deleted_count)


async def resolve_pending_interaction(*, interaction_id: str, terminal_status: str) -> bool:
    """Immediate cleanup policy: delete terminal records instead of retaining them."""
    status = _normalize_text(terminal_status).lower()
    if status not in TERMINAL_STATUSES:
        raise ValueError("terminal_status must be one of resolved/cancelled/expired/stale_target")
    return await delete_pending_interaction(interaction_id=interaction_id)


async def update_pending_interaction_context(
    *,
    interaction_id: str,
    context_payload: Dict[str, Any],
) -> bool:
    collection = get_pending_interactions_collection()
    result = await collection.update_one(
        {"interaction_id": _normalize_text(interaction_id), "status": PENDING_STATUS},
        {"$set": {"context_payload": dict(context_payload or {}), "updated_at": datetime.utcnow()}},
    )
    return bool(result.modified_count)


async def delete_pending_interactions_by_context(
    *,
    project_id: str,
    interaction_type: str,
    context_key: str,
    context_value: str,
) -> int:
    collection = get_pending_interactions_collection()
    result = await collection.delete_many(
        {
            "project_id": _normalize_text(project_id),
            "type": _normalize_text(interaction_type),
            f"context_payload.{_normalize_text(context_key)}": _normalize_text(context_value),
        }
    )
    return int(result.deleted_count or 0)
