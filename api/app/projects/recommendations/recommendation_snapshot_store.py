"""Mongo-backed minimal snapshot for recommendation runtime fallback."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.database import get_brain_collection

_SNAPSHOT_ENTITY_TYPE = "project_insights_snapshot"
_SNAPSHOT_EXTERNAL_ID = "project-insights:v1"


def _clean_project_id(project_id: str) -> str:
    return str(project_id or "").strip()


def _normalize_recommendations(items: Any) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "id": str(item.get("id") or "").strip() or None,
                "taskKey": str(item.get("taskKey") or "").strip() or None,
                "title": str(item.get("title") or "").strip() or None,
                "severity": str(item.get("severity") or "").strip() or None,
                "bucket": str(item.get("bucket") or "").strip() or None,
                "category": str(item.get("category") or "").strip() or None,
                "recommendation": str(item.get("recommendation") or "").strip() or None,
                "why": [str(v).strip() for v in (item.get("why") or []) if str(v or "").strip()],
                "confidence": item.get("confidence"),
                "assignee_suggestion": str(item.get("assignee_suggestion") or "").strip() or None,
            }
        )
    return normalized


def _minimal_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    recommendations = _normalize_recommendations(payload.get("recommendations"))
    work_item_count = sum(1 for item in recommendations if str(item.get("category") or "").lower() == "work_item")
    best_practice_count = sum(
        1 for item in recommendations if str(item.get("category") or "").lower() == "best_practice"
    )
    return {
        "project_id": str(payload.get("project_id") or "").strip(),
        "generated_at": str(payload.get("generated_at") or datetime.utcnow().isoformat()),
        "trigger": str(payload.get("trigger") or "").strip() or "unknown",
        "mode": str(payload.get("mode") or "").strip() or "unknown",
        "version": str(payload.get("version") or "v1").strip() or "v1",
        "count": int(payload.get("count") or len(recommendations)),
        "recommendations": recommendations,
        "summary": {
            "total": len(recommendations),
            "work_item": work_item_count,
            "best_practice": best_practice_count,
        },
    }


async def upsert_recommendation_snapshot(project_id: str, payload: Dict[str, Any]) -> None:
    custom_project_id = _clean_project_id(project_id)
    if not custom_project_id or not isinstance(payload, dict):
        return
    brain_collection = get_brain_collection(custom_project_id)
    now = datetime.utcnow()
    snapshot_payload = _minimal_payload(payload)
    await brain_collection.update_one(
        {
            "entity_type": _SNAPSHOT_ENTITY_TYPE,
            "external_id": _SNAPSHOT_EXTERNAL_ID,
        },
        {
            "$set": {
                "project_id": custom_project_id,
                "entity_type": _SNAPSHOT_ENTITY_TYPE,
                "external_id": _SNAPSHOT_EXTERNAL_ID,
                "title": "Project Insights Snapshot",
                "updated_at": now,
                "snapshot": snapshot_payload,
            },
            "$setOnInsert": {
                "source": "recommendations",
                "created_at": now,
            },
        },
        upsert=True,
    )


async def get_recommendation_snapshot_payload(project_id: str) -> Optional[Dict[str, Any]]:
    custom_project_id = _clean_project_id(project_id)
    if not custom_project_id:
        return None
    brain_collection = get_brain_collection(custom_project_id)
    doc = await brain_collection.find_one(
        {
            "entity_type": _SNAPSHOT_ENTITY_TYPE,
            "external_id": _SNAPSHOT_EXTERNAL_ID,
        },
        {"snapshot": 1},
    )
    snapshot = (doc or {}).get("snapshot") if isinstance(doc, dict) else None
    if not isinstance(snapshot, dict):
        return None
    recommendations = _normalize_recommendations(snapshot.get("recommendations"))
    return {
        "project_id": str(snapshot.get("project_id") or custom_project_id),
        "generated_at": str(snapshot.get("generated_at") or datetime.utcnow().isoformat()),
        "trigger": str(snapshot.get("trigger") or "snapshot"),
        "mode": str(snapshot.get("mode") or "snapshot"),
        "version": str(snapshot.get("version") or "v1"),
        "count": int(snapshot.get("count") or len(recommendations)),
        "recommendations": recommendations,
    }
