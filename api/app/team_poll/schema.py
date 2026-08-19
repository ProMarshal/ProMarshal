"""Schema helpers for Team Poll."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import re
from zoneinfo import ZoneInfo

from app.core.config import settings


OWNER_SKIP_TEXT_COMMAND = "team_poll skip my response"


def _utcnow() -> datetime:
    return datetime.utcnow()


def compute_deadline(*, now: Optional[datetime] = None) -> datetime:
    base = now or _utcnow()
    minutes = max(15, int(getattr(settings, "team_poll_default_deadline_minutes", 90) or 90))
    return base + timedelta(minutes=minutes)


def compute_eod_deadline(*, now: Optional[datetime] = None, timezone_name: Optional[str] = None) -> datetime:
    base_utc = now or _utcnow()
    cutoff_hour = max(0, min(23, int(getattr(settings, "team_poll_eod_hour_local", 17) or 17)))
    tz_value = str(timezone_name or "UTC").strip() or "UTC"
    try:
        tz = ZoneInfo(tz_value)
    except Exception:
        tz = ZoneInfo("UTC")

    base_local = base_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    same_day_local = base_local.replace(hour=cutoff_hour, minute=0, second=0, microsecond=0)
    due_local = same_day_local if same_day_local > base_local else same_day_local + timedelta(days=1)
    return due_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def compute_member_due_at(*, delivery_time: datetime, deadline_at: datetime) -> datetime:
    min_window_minutes = max(5, int(getattr(settings, "team_poll_min_response_window_minutes", 30) or 30))
    extension_cap_minutes = max(0, int(getattr(settings, "team_poll_delivery_extension_cap_minutes", 60) or 60))
    base_due = delivery_time + timedelta(minutes=min_window_minutes)
    capped_due = deadline_at + timedelta(minutes=extension_cap_minutes)
    return min(capped_due, max(deadline_at, base_due))


def parse_minutes_csv(raw: Any, *, default: List[int]) -> List[int]:
    text = str(raw or "").strip()
    if not text:
        return list(default)
    parts = [item.strip() for item in text.split(",")]
    values: List[int] = []
    for part in parts:
        if not part:
            continue
        try:
            value = int(part)
        except Exception:
            continue
        if value > 0:
            values.append(value)
    return values or list(default)


def normalize_question_text(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\s*(ask|check|collect|run)\s+(with\s+)?(the\s+)?team\s*(if|about)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if text.endswith("?"):
        return text
    return f"{text}?"


def needs_question_rewrite(*, question_text: str, owner_text: str) -> bool:
    normalized_question = str(question_text or "").strip()
    normalized_owner = str(owner_text or "").strip()
    if not normalized_question:
        return True
    token_count = len([tok for tok in re.split(r"\s+", normalized_question) if tok])
    if token_count < 4:
        return True
    # vague pronoun-only prompts are poor for team fanout
    if re.search(r"\b(this|that|it|same|above)\b", normalized_question, flags=re.IGNORECASE):
        return True
    # if owner message is much richer than parsed question, likely rewrite-worthy
    if normalized_owner and len(normalized_question) < max(20, int(len(normalized_owner) * 0.35)):
        return True
    return False


def parse_owner_question_and_mode(raw_text: str) -> Tuple[str, Dict[str, Any]]:
    text = str(raw_text or "").strip()
    mode: Dict[str, Any] = {
        "response_format": "free_text",
        "options": [],
        "requires_comment": False,
        "deadline_mode": "default",
    }
    if re.search(r"\bby\s+eod\b", text, flags=re.IGNORECASE):
        mode["deadline_mode"] = "eod"
    format_match = re.search(
        r"\bformat\s+(free_text|yes_no|single_choice|multi_choice|scale)\b",
        text,
        flags=re.IGNORECASE,
    )
    if format_match:
        mode["response_format"] = str(format_match.group(1) or "free_text").strip().lower()
    options_match = re.search(r"\boptions\s+\"([^\"]+)\"", text, flags=re.IGNORECASE)
    if options_match:
        raw_options = str(options_match.group(1) or "").strip()
        options = [item.strip() for item in raw_options.split(",") if item.strip()]
        mode["options"] = options
    stripped = re.sub(r"\bby\s+eod\b", "", text, flags=re.IGNORECASE)
    stripped = re.sub(r"\bformat\s+(free_text|yes_no|single_choice|multi_choice|scale)\b", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\boptions\s+\"([^\"]+)\"", "", stripped, flags=re.IGNORECASE)
    question = normalize_question_text(stripped)
    return question, mode


def build_owner_skip_hint() -> str:
    return "If you don't want to respond, reply `skip`."


def build_member_prompt_text(*, member_name: str, project_name: str, question_text: str, owner_prompt: bool) -> str:
    def _first_name(name: str) -> str:
        text = str(name or "").strip()
        if not text:
            return "there"
        return text.split()[0]

    greeting = f"Hi {_first_name(member_name)} - a quick check."
    clean_question = str(question_text or "").strip()
    if clean_question and not clean_question.endswith("?"):
        clean_question = clean_question + "?"
    core = f"{clean_question}\n\nPlease reply in this DM."
    if owner_prompt:
        return f"{greeting}\n\n{core}\n{build_owner_skip_hint()}"
    return f"{greeting}\n\n{core}"


def is_owner_skip_text(text: str) -> bool:
    normalized = " ".join(str(text or "").strip().lower().split())
    return normalized in {
        "skip",
        "skip my response",
        OWNER_SKIP_TEXT_COMMAND,
        "/promarshal " + OWNER_SKIP_TEXT_COMMAND,
    }


def normalize_response_for_mode(*, response_format: str, text: str, options: Optional[List[str]] = None) -> Tuple[bool, Dict[str, Any]]:
    normalized_format = str(response_format or "free_text").strip().lower()
    raw_text = str(text or "").strip()
    if not raw_text:
        return False, {"error": "empty_response"}

    if normalized_format == "free_text":
        return True, {"value": raw_text}

    if normalized_format == "yes_no":
        compact = raw_text.lower()
        if compact in {"yes", "y", "yeah", "yep"}:
            return True, {"value": "yes", "raw": raw_text}
        if compact in {"no", "n", "nope"}:
            return True, {"value": "no", "raw": raw_text}
        return False, {"error": "expected_yes_no"}

    if normalized_format in {"single_choice", "multi_choice"}:
        normalized_options = [str(item).strip() for item in (options or []) if str(item).strip()]
        if not normalized_options:
            return False, {"error": "missing_options"}
        raw_items = [item.strip() for item in raw_text.split(",") if item.strip()]
        if normalized_format == "single_choice" and len(raw_items) != 1:
            return False, {"error": "expected_single_choice", "options": normalized_options}
        allowed_lower = {item.lower(): item for item in normalized_options}
        resolved: List[str] = []
        for raw_item in raw_items:
            mapped = allowed_lower.get(raw_item.lower())
            if not mapped:
                return False, {"error": "invalid_choice", "options": normalized_options}
            resolved.append(mapped)
        return True, {"value": resolved[0] if normalized_format == "single_choice" else resolved, "raw": raw_text}

    if normalized_format == "scale":
        try:
            value = int(raw_text)
        except Exception:
            return False, {"error": "expected_scale_integer"}
        if value < 1 or value > 5:
            return False, {"error": "expected_scale_1_to_5"}
        return True, {"value": value, "raw": raw_text}

    return False, {"error": "unsupported_response_format"}


def session_outcome(*, delivery_status: str, response_status: str, reason_codes: Optional[list[str]] = None, terminal: bool = False) -> Dict[str, Any]:
    return {
        "delivery_status": str(delivery_status or "pending").strip().lower(),
        "response_status": str(response_status or "pending").strip().lower(),
        "reason_codes": sorted({str(x).strip().lower() for x in (reason_codes or []) if str(x).strip()}),
        "terminal": bool(terminal),
    }
