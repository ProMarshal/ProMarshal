"""Schedule time calculations."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.core.timezone import UTC_TZ_NAME, get_zoneinfo_or_utc


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = str(value or "").strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid HH:MM value: {value}")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"Invalid HH:MM value: {value}")
    return hour, minute


def due_bucket_key(due_at: datetime) -> str:
    """Return a stable minute-level idempotency bucket."""
    return due_at.replace(second=0, microsecond=0).isoformat()


def compute_next_run_at(
    *,
    schedule_spec: Dict[str, Any],
    now_utc: datetime,
    project_timezone: Optional[str] = None,
    current_due_at: Optional[datetime] = None,
) -> datetime:
    """
    Compute next UTC run timestamp for a schedule.

    Supported schedule_spec:
    - {"mode": "interval", "minutes": 60}
    - {"mode": "daily", "time": "09:00"}
    """
    mode = str((schedule_spec or {}).get("mode") or "").strip().lower()

    if mode == "interval":
        minutes = max(1, int((schedule_spec or {}).get("minutes") or 1))
        base = current_due_at or now_utc
        candidate = base + timedelta(minutes=minutes)
        while candidate <= now_utc:
            candidate += timedelta(minutes=minutes)
        return candidate

    if mode == "daily":
        hhmm = str((schedule_spec or {}).get("time") or "09:00").strip()
        hour, minute = _parse_hhmm(hhmm)
        skip_weekends = bool((schedule_spec or {}).get("skip_weekends", False))
        tz_name = str(project_timezone or "UTC").strip() or "UTC"
        tz = get_zoneinfo_or_utc(tz_name, context="scheduler_compute_next_run_at")
        utc_tz = get_zoneinfo_or_utc(UTC_TZ_NAME, context="scheduler_compute_next_run_at_utc")
        now_local = now_utc.replace(tzinfo=utc_tz).astimezone(tz)
        candidate_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate_local <= now_local:
            candidate_local = candidate_local + timedelta(days=1)
        if skip_weekends:
            while candidate_local.weekday() >= 5:
                candidate_local = candidate_local + timedelta(days=1)
        candidate_utc = candidate_local.astimezone(utc_tz).replace(tzinfo=None)
        return candidate_utc

    raise ValueError(f"Unsupported schedule mode: {mode}")
