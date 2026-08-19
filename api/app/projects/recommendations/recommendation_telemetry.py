"""Recommendation telemetry helpers.

Telemetry persistence is currently disabled by product policy.
The helper remains as a stable no-op contract for callers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_VALID_ACTIONS = {"shown", "accepted", "dismissed", "completed"}


def _normalized_action(action: str) -> str:
    normalized = str(action or "").strip().lower()
    if normalized not in _VALID_ACTIONS:
        raise ValueError("Invalid recommendation telemetry action")
    return normalized


async def record_recommendation_event(
    *,
    project_id: str,
    custom_project_id: str,
    action: str,
    task_key: Optional[str] = None,
    recommendation_id: Optional[str] = None,
    source: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Best-effort recommendation telemetry hook.

    Persistence is intentionally disabled; this function validates action shape
    and returns True to preserve caller contract without DB writes.
    """
    try:
        _normalized_action(action)
        _ = (
            str(project_id or "").strip(),
            str(custom_project_id or "").strip(),
            str(task_key or "").strip() or None,
            str(recommendation_id or "").strip() or None,
            str(source or "").strip() or None,
            str(actor_user_id or "").strip() or None,
            dict(metadata or {}),
        )
        return True
    except Exception as exc:
        logger.warning(
            "recommendation_telemetry_noop_failed action=%s project_id=%s error=%s",
            str(action or "").strip().lower(),
            str(project_id or "").strip(),
            exc,
        )
        return False
