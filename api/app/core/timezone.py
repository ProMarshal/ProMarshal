"""Timezone helpers with safe fallback semantics."""

from __future__ import annotations

from typing import Optional
from zoneinfo import ZoneInfo


UTC_TZ_NAME = "UTC"


def normalize_timezone_name(value: Optional[str]) -> str:
    """Return a normalized timezone identifier or UTC when missing."""
    normalized = str(value or "").strip()
    return normalized or UTC_TZ_NAME


def get_zoneinfo_or_utc(
    timezone_name: Optional[str],
    *,
    context: str = "",
) -> ZoneInfo:
    """
    Resolve ZoneInfo safely.

    Falls back to UTC when the provided identifier is invalid or unavailable
    in the runtime tz database.
    """
    candidate = normalize_timezone_name(timezone_name)
    try:
        return ZoneInfo(candidate)
    except Exception:
        if context:
            print(
                "timezone_fallback_to_utc: "
                f"context={context} invalid_timezone={candidate}"
            )
        else:
            print(f"timezone_fallback_to_utc: invalid_timezone={candidate}")
        return ZoneInfo(UTC_TZ_NAME)


def canonical_timezone_name(timezone_name: Optional[str], *, context: str = "") -> str:
    """
    Return a canonical timezone name suitable for persistence.

    Invalid timezones are normalized to UTC.
    """
    tz = get_zoneinfo_or_utc(timezone_name, context=context)
    return str(getattr(tz, "key", UTC_TZ_NAME) or UTC_TZ_NAME)
