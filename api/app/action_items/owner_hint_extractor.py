"""Shared owner-hint extraction for action-item workflows."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from app.llm import LLMGatewayRequest, LLMMessage, LLMMessageRole, get_shared_llm_gateway

_OWNER_STOPWORDS = {
    "me",
    "myself",
    "self",
    "it",
    "this",
    "that",
    "task",
    "action",
    "item",
    "someone",
    "anyone",
    "team",
}
_OWNER_INTENT_RE = re.compile(
    r"\b(?:assign(?:ed)?\s+to|assign(?:ed)?|owner\s*:|owner\s+is|for\s+me|to\s+me|myself|ask\s+\S+\s+to|for\s+@?\w+)\b",
    re.IGNORECASE,
)
_ASK_TO_RE = re.compile(r"\bask\s+([A-Za-z][A-Za-z0-9@._\-\s]{0,80}?)\s+to\b", re.IGNORECASE)
_ASSIGN_TO_RE = re.compile(
    r"\bassign(?:ed)?\s+(?:this|it|task|action\s*item)?\s*(?:to)?\s*([A-Za-z0-9@._\-\s]{1,80})$",
    re.IGNORECASE,
)
_OWNER_IS_RE = re.compile(r"\bowner\s*(?:is|:)\s*([A-Za-z0-9@._\-\s]{1,80})$", re.IGNORECASE)
_FOR_OWNER_RE = re.compile(
    r"\bfor\s+([A-Za-z][A-Za-z0-9@._\-]*(?:\s+[A-Za-z][A-Za-z0-9@._\-]*){0,3})\b",
    re.IGNORECASE,
)
_LEADING_OWNER_TO_VERB_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9@._\-]*(?:\s+[A-Za-z][A-Za-z0-9@._\-]*){0,3})\s+to\s+"
    r"(?:test|retest|verify|check|review|update|send|follow(?:\s*up)?|fix|confirm|validate|deploy|run|prepare|share|close)\b",
    re.IGNORECASE,
)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_owner_hint(value: str) -> str:
    text = _normalize_text(value)
    text = re.sub(r"\s+", " ", text).strip(" .,:;!?")
    if text.startswith("@"):
        text = text[1:].strip()
    lowered = text.lower()
    if not text or lowered in _OWNER_STOPWORDS:
        return ""
    return text


def has_explicit_owner_intent(raw_text: Optional[str], owner_hint: Optional[str]) -> bool:
    raw = _normalize_text(raw_text)
    hint = _normalize_text(owner_hint)
    if hint:
        return True
    return bool(raw and _OWNER_INTENT_RE.search(raw))


def extract_owner_hint_deterministic(raw_text: Optional[str], owner_hint: Optional[str] = None) -> Optional[str]:
    direct_hint = _clean_owner_hint(_normalize_text(owner_hint))
    if direct_hint:
        return direct_hint

    text = _normalize_text(raw_text)
    if not text:
        return None

    for pattern in (_ASK_TO_RE, _LEADING_OWNER_TO_VERB_RE, _ASSIGN_TO_RE, _OWNER_IS_RE, _FOR_OWNER_RE):
        match = pattern.search(text)
        if not match:
            continue
        extracted = _clean_owner_hint(match.group(1))
        if extracted:
            return extracted
    return None


async def extract_owner_hint(
    *,
    raw_text: Optional[str],
    owner_hint: Optional[str] = None,
    use_llm: bool = True,
    min_confidence: float = 0.70,
) -> Optional[str]:
    deterministic = extract_owner_hint_deterministic(raw_text, owner_hint)
    if deterministic:
        return deterministic
    if not use_llm:
        return None

    text = _normalize_text(raw_text)
    if not text:
        return None
    gateway = get_shared_llm_gateway()
    if gateway is None:
        return None

    request = LLMGatewayRequest(
        messages=[
            LLMMessage(
                role=LLMMessageRole.SYSTEM,
                content=(
                    "Extract assignee/owner hint from user request. "
                    "Return STRICT JSON with keys owner_hint and confidence. "
                    "If no assignee intent is present, return owner_hint as empty string."
                ),
            ),
            LLMMessage(role=LLMMessageRole.USER, content=text),
        ],
        temperature=0.0,
        max_tokens=80,
        timeout_seconds=6,
        retries=0,
        metadata={"feature": "action_items_owner_hint_extractor"},
    )
    response = await gateway.generate(request)
    if not bool(getattr(response, "ok", False)):
        return None

    raw_payload = _normalize_text(getattr(response, "text", ""))
    if not raw_payload:
        return None
    parsed: Optional[dict] = None
    try:
        parsed = json.loads(raw_payload)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", raw_payload)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except Exception:
                parsed = None
    if not isinstance(parsed, dict):
        return None

    try:
        confidence = float(parsed.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    if confidence < max(0.0, min(1.0, float(min_confidence))):
        return None

    hint = _clean_owner_hint(_normalize_text(parsed.get("owner_hint")))
    return hint or None
