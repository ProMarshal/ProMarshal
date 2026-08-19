"""Cadence DM queue canary routing helpers."""

from __future__ import annotations

import hashlib

from app.core.config import settings


def should_route_to_queue(session_id: str) -> bool:
    normalized = str(session_id or "").strip()
    if not normalized:
        return True

    pct = int(getattr(settings, "cadence_dm_queue_canary_pct", 100) or 0)
    if pct <= 0:
        return False
    if pct >= 100:
        return True
    bucket = int(hashlib.md5(normalized.encode("utf-8")).hexdigest(), 16) % 100
    return bucket < pct
