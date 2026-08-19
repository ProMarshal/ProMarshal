"""Deterministic due-date inference policy for action items."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class DueInferenceResult:
    due_type: str
    due_date: Optional[datetime]

_PREP_ACTION_TOKENS = {
    "invite",
    "schedule",
    "book",
    "prepare",
    "setup",
    "set up",
    "share",
    "distribute",
    "send",
    "arrange",
    "coordinate",
    "organize",
    "organise",
    "draft",
}

_DEADLINE_ACTION_TOKENS = {
    "finish",
    "complete",
    "review",
    "submit",
    "deliver",
    "close",
    "resolve",
    "finalize",
    "finalise",
    "publish",
    "merge",
    "approve",
    "signoff",
    "sign off",
}


def _to_timezone(timezone_name: Optional[str]) -> ZoneInfo:
    candidate = str(timezone_name or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(candidate)
    except Exception:
        return ZoneInfo("UTC")


def _to_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _looks_like_prep_action(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(token in lowered for token in _PREP_ACTION_TOKENS)


def _looks_like_deadline_action(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(token in lowered for token in _DEADLINE_ACTION_TOKENS)


def _previous_business_day(date_value) -> datetime.date:
    candidate = date_value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def infer_due(
    *,
    action_text: str,
    timezone_name: Optional[str],
    explicit_due_type: Optional[str],
    explicit_due_date: Optional[datetime],
    target_datetime: Optional[datetime],
    now_utc: Optional[datetime] = None,
) -> DueInferenceResult:
    normalized_explicit_due_type = str(explicit_due_type or "").strip().lower()
    normalized_explicit_due_date = _to_utc(explicit_due_date)
    normalized_target = _to_utc(target_datetime)

    # Explicit due input always wins.
    if normalized_explicit_due_type in {"today", "this_week", "by_date"}:
        return DueInferenceResult(
            due_type=normalized_explicit_due_type,
            due_date=normalized_explicit_due_date,
        )
    if normalized_explicit_due_date is not None:
        return DueInferenceResult(due_type="by_date", due_date=normalized_explicit_due_date)

    if normalized_target is None:
        return DueInferenceResult(due_type="no_due_date", due_date=None)

    tz = _to_timezone(timezone_name)
    anchor_utc = now_utc or datetime.now(timezone.utc)
    if anchor_utc.tzinfo is None:
        anchor_utc = anchor_utc.replace(tzinfo=timezone.utc)
    now_local = anchor_utc.astimezone(tz)
    target_local = normalized_target.astimezone(tz)

    if _looks_like_deadline_action(action_text):
        return DueInferenceResult(
            due_type="by_date",
            due_date=normalized_target.astimezone(timezone.utc),
        )

    if _looks_like_prep_action(action_text):
        candidate_date = _previous_business_day(target_local.date())
        if candidate_date < now_local.date():
            candidate_date = now_local.date()
        inferred_local = datetime.combine(candidate_date, time(hour=17, minute=0), tzinfo=tz)
        if inferred_local < now_local:
            inferred_local = now_local + timedelta(minutes=30)
        if inferred_local >= target_local:
            inferred_local = max(now_local + timedelta(minutes=30), target_local - timedelta(minutes=30))
        return DueInferenceResult(
            due_type="by_date",
            due_date=inferred_local.astimezone(timezone.utc),
        )

    # Default-safe behavior: treat target as deadline when intent is unclear.
    return DueInferenceResult(
        due_type="by_date",
        due_date=normalized_target.astimezone(timezone.utc),
    )
