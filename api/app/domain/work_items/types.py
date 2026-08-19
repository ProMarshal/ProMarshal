"""Canonical work-item type resolution utilities."""

from __future__ import annotations

from typing import Any, Dict


_WORK_ITEM_TYPE_BY_PROVIDER_TYPE = {
    "epic": "portfolio",
    "initiative": "portfolio",
    "feature": "portfolio",
    "milestone": "portfolio",
    "project": "portfolio",
    "story": "standard",
    "task": "standard",
    "issue": "standard",
    "ticket": "standard",
    "bug": "defect",
    "defect": "defect",
    "incident": "defect",
    "subtask": "subtask",
    "sub-task": "subtask",
    "sub issue": "subtask",
    "sub-issue": "subtask",
    "service request": "service",
    "service": "service",
    "support": "service",
}


def classify_work_item_type(provider_type: Any) -> str:
    """Map provider-native type name to canonical work-item class."""
    normalized = str(provider_type or "").strip().lower()
    if not normalized:
        return "custom"
    return _WORK_ITEM_TYPE_BY_PROVIDER_TYPE.get(normalized, "custom")


def resolve_work_item_type_from_item(item: Dict[str, Any]) -> str:
    """
    Resolve canonical work-item type from an item document.

    Priority:
    1. Existing canonical `work_item_type`
    2. `provider_type`
    3. Legacy `issue_type`
    """
    canonical = str((item or {}).get("work_item_type") or "").strip().lower()
    if canonical:
        return canonical
    provider_type = (item or {}).get("provider_type")
    if provider_type is None:
        provider_type = (item or {}).get("issue_type")
    return classify_work_item_type(provider_type)


def canonicalize_work_item_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a canonicalized work-item payload for Brain upsert safety.

    Guarantees:
    - `entity_type` is always `work_item`
    - `provider_type` is populated when possible (falls back to legacy `issue_type`)
    - `work_item_type` is populated from canonical mapping when missing
    """
    payload = dict(item or {})
    provider_type = str(
        payload.get("provider_type")
        or payload.get("issue_type")
        or ""
    ).strip() or None
    if provider_type:
        payload["provider_type"] = provider_type
    payload["work_item_type"] = resolve_work_item_type_from_item(payload)
    payload["entity_type"] = "work_item"
    return payload
