"""Reusable per-project sequence helpers backed by MongoDB atomic counters."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pymongo import ReturnDocument


COUNTER_ENTITY_TYPE = "counter"


def _normalize_sequence_name(sequence_name: str) -> str:
    normalized = str(sequence_name or "").strip()
    if not normalized:
        raise ValueError("sequence_name is required")
    return normalized


def _normalize_project_id(project_id: str) -> str:
    normalized = str(project_id or "").strip()
    if not normalized:
        raise ValueError("project_id is required")
    return normalized


async def next_sequence(
    brain_collection: Any,
    *,
    project_id: str,
    sequence_name: str,
) -> int:
    """
    Return the next integer value for a project-scoped sequence.

    Sequence state is stored inside each project's Brain collection using:
    - entity_type: "counter"
    - counter_key: sequence name
    - value: current integer value
    """

    if brain_collection is None:
        raise ValueError("brain_collection is required")
    normalized_project_id = _normalize_project_id(project_id)
    normalized_sequence_name = _normalize_sequence_name(sequence_name)
    now = datetime.utcnow()

    doc = await brain_collection.find_one_and_update(
        {
            "entity_type": COUNTER_ENTITY_TYPE,
            "counter_key": normalized_sequence_name,
        },
        {
            "$inc": {"value": 1},
            "$set": {
                "project_id": normalized_project_id,
                "updated_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    value = int((doc or {}).get("value") or 0)
    if value <= 0:
        raise RuntimeError("sequence allocation failed")
    return value
