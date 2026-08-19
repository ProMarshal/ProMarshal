"""Deterministic temporal parsing helpers for action-item text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo


_WEEKDAY_MAP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_REL_DATE_RE = re.compile(
    r"\b(today|tomorrow|this\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)
_EXPLICIT_DUE_RE = re.compile(r"\b(by|before|due)\b", re.IGNORECASE)
_THIS_WEEK_RE = re.compile(r"\bthis\s+week\b", re.IGNORECASE)
_TODAY_RE = re.compile(r"\btoday\b", re.IGNORECASE)


@dataclass(frozen=True)
class TemporalParseResult:
    target_datetime: Optional[datetime]
    explicit_due_datetime: Optional[datetime]
    explicit_due_type: Optional[str]


def _to_timezone(timezone_name: Optional[str]) -> ZoneInfo:
    candidate = str(timezone_name or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(candidate)
    except Exception:
        return ZoneInfo("UTC")


def _parse_time_in_text(text: str) -> Optional[time]:
    match = _TIME_RE.search(str(text or ""))
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = str(match.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour=hour, minute=minute)


def _parse_date_phrase(text: str, now_local: datetime) -> Optional[datetime]:
    normalized = str(text or "").lower()
    iso_match = _ISO_DATE_RE.search(normalized)
    if iso_match:
        try:
            return datetime.fromisoformat(iso_match.group(1)).replace(
                tzinfo=now_local.tzinfo,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        except Exception:
            return None

    rel_match = _REL_DATE_RE.search(normalized)
    if not rel_match:
        return None
    token = rel_match.group(1).lower().strip()
    if token == "today":
        return now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    if token == "tomorrow":
        return (now_local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    if " " in token:
        prefix, weekday_name = token.split(" ", 1)
        target_weekday = _WEEKDAY_MAP.get(weekday_name)
        if target_weekday is None:
            return None
        day_delta = target_weekday - now_local.weekday()
        if prefix == "next":
            day_delta += 7 if day_delta <= 0 else 7
        elif prefix == "this" and day_delta < 0:
            day_delta += 7
    else:
        target_weekday = _WEEKDAY_MAP.get(token)
        if target_weekday is None:
            return None
        day_delta = target_weekday - now_local.weekday()
        if day_delta < 0:
            day_delta += 7
    return (now_local + timedelta(days=day_delta)).replace(hour=0, minute=0, second=0, microsecond=0)


def _with_time(base_date: datetime, parsed_time: Optional[time]) -> datetime:
    if parsed_time is None:
        return base_date.replace(hour=17, minute=0, second=0, microsecond=0)
    return base_date.replace(
        hour=parsed_time.hour,
        minute=parsed_time.minute,
        second=0,
        microsecond=0,
    )


def parse_temporal_hints(
    *,
    text: str,
    timezone_name: Optional[str],
    now_utc: Optional[datetime] = None,
) -> TemporalParseResult:
    normalized_text = str(text or "").strip()
    if not normalized_text:
        return TemporalParseResult(target_datetime=None, explicit_due_datetime=None, explicit_due_type=None)

    tz = _to_timezone(timezone_name)
    anchor_utc = now_utc or datetime.now(timezone.utc)
    if anchor_utc.tzinfo is None:
        anchor_utc = anchor_utc.replace(tzinfo=timezone.utc)
    now_local = anchor_utc.astimezone(tz)

    explicit_due_type: Optional[str] = None
    explicit_due_datetime: Optional[datetime] = None
    if _EXPLICIT_DUE_RE.search(normalized_text):
        if _TODAY_RE.search(normalized_text):
            explicit_due_type = "today"
        elif _THIS_WEEK_RE.search(normalized_text):
            explicit_due_type = "this_week"
        parsed_date = _parse_date_phrase(normalized_text, now_local)
        if parsed_date is not None:
            explicit_due_type = "by_date"
            explicit_due_datetime = _with_time(parsed_date, _parse_time_in_text(normalized_text))
            explicit_due_datetime = explicit_due_datetime.astimezone(timezone.utc)

    target_datetime: Optional[datetime] = None
    parsed_target_date = _parse_date_phrase(normalized_text, now_local)
    if parsed_target_date is not None:
        target_datetime = _with_time(parsed_target_date, _parse_time_in_text(normalized_text)).astimezone(timezone.utc)

    return TemporalParseResult(
        target_datetime=target_datetime,
        explicit_due_datetime=explicit_due_datetime,
        explicit_due_type=explicit_due_type,
    )
