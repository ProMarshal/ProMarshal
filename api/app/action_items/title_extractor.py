"""Action item title extraction with deterministic baseline and LLM gateway enhancement."""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, ValidationError, confloat

from app.core.config import settings
from app.llm import LLMGatewayRequest, LLMMessage, LLMMessageRole, get_shared_llm_gateway

_WS_RE = re.compile(r"\s+")
_ACTION_PREFIX_RE = re.compile(
    r"^\s*(?:create|add|log|note|track)\s+(?:an?\s+)?action\s*item\s*(?:to)?\s*",
    re.IGNORECASE,
)
_ASSIGN_PREFIX_RE = re.compile(
    r"\b(?:assign(?:ed)?\s+(?:to)?|owner(?:\s+is)?|for)\s+([A-Za-z0-9@._\-\s]+)$",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_TIME_HINT_RE = re.compile(
    r"\b(?:at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?|\d{1,2}(?::\d{2})?\s*(?:am|pm)|eod|today|tomorrow|this week)\b",
    re.IGNORECASE,
)


class _ExtractedTitlePayload(BaseModel):
    title: str
    confidence: confloat(ge=0.0, le=1.0)
    owner_hint: Optional[str] = None
    due_type: Optional[str] = None
    due_date_iso: Optional[str] = None
    related_to: Optional[str] = None


@dataclass(frozen=True)
class ActionItemExtractedCandidate:
    title: str
    owner_hint: Optional[str]
    due_type: str
    due_date: Optional[datetime]
    related_to: Optional[str]
    source: str
    confidence: float


def _normalize_text(value: Any) -> str:
    return _WS_RE.sub(" ", str(value or "").strip())


def _strip_action_prefix(text: str) -> str:
    return _ACTION_PREFIX_RE.sub("", text).strip()


def _extract_owner_hint(text: str) -> Optional[str]:
    lowered = str(text or "").strip().lower()
    for alias in {" me", " myself", " self", " i "}:
        if alias.strip() == lowered or lowered.endswith(alias):
            return "me"
    match = _ASSIGN_PREFIX_RE.search(text or "")
    if not match:
        return None
    hint = _normalize_text(match.group(1))
    if not hint:
        return None
    return hint


def _extract_due_hint(text: str) -> tuple[str, Optional[datetime]]:
    normalized = str(text or "").lower()
    now = datetime.now(timezone.utc)
    iso_match = _ISO_DATE_RE.search(normalized)
    if iso_match:
        try:
            return "by_date", datetime.fromisoformat(iso_match.group(1)).replace(tzinfo=timezone.utc)
        except Exception:
            return "no_due_date", None
    if "tomorrow" in normalized:
        return "by_date", (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if "today" in normalized:
        return "today", None
    if "this week" in normalized:
        return "this_week", None
    return "no_due_date", None


def _deterministic_candidate(text: str) -> ActionItemExtractedCandidate:
    normalized = _normalize_text(text)
    stripped = _strip_action_prefix(normalized)
    title = stripped or normalized or "Follow up"
    due_type, due_date = _extract_due_hint(normalized)
    return ActionItemExtractedCandidate(
        title=title[:180],
        owner_hint=_extract_owner_hint(normalized),
        due_type=due_type,
        due_date=due_date,
        related_to=None,
        source="deterministic",
        confidence=0.55,
    )


def _build_prompt_payload(*, raw_text: str, deterministic: ActionItemExtractedCandidate) -> Dict[str, Any]:
    return {
        "raw_text": raw_text,
        "deterministic_candidate": {
            "title": deterministic.title,
            "owner_hint": deterministic.owner_hint,
            "due_type": deterministic.due_type,
            "due_date_iso": deterministic.due_date.isoformat() if deterministic.due_date else None,
        },
        "rules": {
            "keep_title_short": True,
            "preserve_critical_details": ["date", "time", "topic", "target team"],
            "max_title_chars": int(getattr(settings, "action_items_title_max_chars", 180) or 180),
        },
    }


def _contains_critical_time_hint(text: str) -> bool:
    return bool(_TIME_HINT_RE.search(str(text or "")))


def _time_hint_preserved(*, original: str, rewritten: str) -> bool:
    if not _contains_critical_time_hint(original):
        return True
    return _contains_critical_time_hint(rewritten) or bool(_ISO_DATE_RE.search(rewritten))


def _postprocess_title(value: str) -> str:
    max_chars = max(60, int(getattr(settings, "action_items_title_max_chars", 180) or 180))
    normalized = _normalize_text(value).strip(" -")
    if len(normalized) > max_chars:
        normalized = normalized[:max_chars].rstrip(" ,.;:!?")
    return normalized


async def _extract_with_gateway(*, raw_text: str, deterministic: ActionItemExtractedCandidate) -> Optional[ActionItemExtractedCandidate]:
    gateway = get_shared_llm_gateway()
    if gateway is None:
        return None

    system_prompt = (
        "Extract one action item candidate from the input text.\n"
        "Return STRICT JSON with keys: title, confidence, owner_hint, due_type, due_date_iso, related_to.\n"
        "Rules:\n"
        "- Preserve important details (especially date/time if present).\n"
        "- Keep title concise and action-oriented.\n"
        "- due_type must be one of: today, this_week, no_due_date, by_date.\n"
        "- due_date_iso should be YYYY-MM-DD when due_type=by_date.\n"
        "- Do not invent facts."
    )
    payload = _build_prompt_payload(raw_text=raw_text, deterministic=deterministic)
    request = LLMGatewayRequest(
        messages=[
            LLMMessage(role=LLMMessageRole.SYSTEM, content=system_prompt),
            LLMMessage(role=LLMMessageRole.USER, content=json.dumps(payload, ensure_ascii=True)),
        ],
        temperature=0.2,
        max_tokens=220,
        timeout_seconds=10,
        retries=0,
        metadata={"feature": "action_items_title_extractor"},
    )
    response = await gateway.generate(request)
    if not response.ok or not _normalize_text(response.text):
        return None

    raw_text_out = _normalize_text(response.text)
    parsed_json: Optional[Dict[str, Any]] = None
    try:
        parsed_json = json.loads(raw_text_out)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", raw_text_out)
        if match:
            try:
                parsed_json = json.loads(match.group(0))
            except Exception:
                parsed_json = None
    if not isinstance(parsed_json, dict):
        return None

    try:
        parsed = _ExtractedTitlePayload.model_validate(parsed_json)
    except ValidationError:
        return None

    title = _postprocess_title(parsed.title)
    if not title:
        return None
    if not _time_hint_preserved(original=raw_text, rewritten=title):
        return None

    due_type = str(parsed.due_type or deterministic.due_type or "no_due_date").strip().lower()
    if due_type not in {"today", "this_week", "no_due_date", "by_date"}:
        due_type = deterministic.due_type

    due_date: Optional[datetime] = None
    if due_type == "by_date":
        try:
            token = str(parsed.due_date_iso or "").strip()
            if token:
                due_date = datetime.fromisoformat(token).replace(tzinfo=timezone.utc)
        except Exception:
            due_date = deterministic.due_date

    return ActionItemExtractedCandidate(
        title=title,
        owner_hint=_normalize_text(parsed.owner_hint) or deterministic.owner_hint,
        due_type=due_type,
        due_date=due_date,
        related_to=_normalize_text(parsed.related_to) or deterministic.related_to,
        source="llm_gateway",
        confidence=float(parsed.confidence),
    )


async def resolve_action_item_candidate(*, raw_text: str) -> ActionItemExtractedCandidate:
    deterministic = _deterministic_candidate(raw_text)
    rewrite_enabled = bool(getattr(settings, "action_items_title_rewrite_enabled", True))
    min_confidence = max(
        0.0,
        min(1.0, float(getattr(settings, "action_items_title_rewrite_min_confidence", 0.68) or 0.68)),
    )
    max_retries = max(0, int(getattr(settings, "action_items_title_rewrite_max_retries", 2) or 2))
    retry_jitter_ms = max(0, int(getattr(settings, "action_items_title_rewrite_retry_jitter_ms", 120) or 120))
    backoff_csv = str(getattr(settings, "action_items_title_rewrite_backoff_ms", "300,900") or "300,900")
    backoff = []
    for item in backoff_csv.split(","):
        token = str(item or "").strip()
        if not token:
            continue
        try:
            value = int(token)
        except Exception:
            continue
        if value > 0:
            backoff.append(value)
    if not backoff:
        backoff = [300, 900]

    if not rewrite_enabled:
        return deterministic

    import asyncio

    total_attempts = max_retries + 1
    for attempt in range(total_attempts):
        rewritten = await _extract_with_gateway(raw_text=raw_text, deterministic=deterministic)
        if rewritten is not None and rewritten.confidence >= min_confidence:
            return rewritten
        if attempt >= max_retries:
            break
        base_delay_ms = backoff[min(attempt, len(backoff) - 1)]
        jitter_ms = random.randint(0, retry_jitter_ms) if retry_jitter_ms > 0 else 0
        await asyncio.sleep((base_delay_ms + jitter_ms) / 1000.0)

    return deterministic
