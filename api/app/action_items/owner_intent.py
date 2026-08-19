"""Shared owner-intent parsing helpers for action-item flows.

Deterministic parsing here is intentionally high-confidence only.
Uncertain language should be handled by downstream LLM fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_OWNER_INTENT_CUE_RE = re.compile(
    r"\b(?:assign(?:ed)?\s+to|owner\s*(?:is|as|:)?|set\s+owner\s+as|create\s+(?:an?\s+)?action\s*item\s+for|for\s+(?:me|myself|self)|to\s+me)\b",
    re.IGNORECASE,
)
_ASSIGN_TO_RE = re.compile(
    r"\bassign(?:ed)?\b(?:\s+.+?)?\s+to\s+"
    r"([A-Za-z][A-Za-z0-9@._\-]*(?:\s+[A-Za-z][A-Za-z0-9@._\-]*){0,2})"
    r"(?=\s+(?:for|to|regarding|about|on|that|saying)\b|[,.!?]|$)",
    re.IGNORECASE,
)
_OWNER_AS_RE = re.compile(
    r"\b(?:set\s+)?owner\s*(?:is|as|:)\s*"
    r"([A-Za-z][A-Za-z0-9@._\-]*(?:\s+[A-Za-z][A-Za-z0-9@._\-]*){0,2})"
    r"(?=\s+(?:for|to|regarding|about|on|that|saying)\b|[,.!?]|$)",
    re.IGNORECASE,
)
_CREATE_FOR_OWNER_RE = re.compile(
    r"\bcreate\s+(?:an?\s+)?action\s*item\s+for\s+"
    r"([A-Za-z][A-Za-z0-9@._\-]*(?:\s+[A-Za-z][A-Za-z0-9@._\-]*){0,2})"
    r"(?=\s+(?:saying|to|that|about|regarding|on)\b|[,.!?]|$)",
    re.IGNORECASE,
)
_NAME_TO_ACTION_RE = re.compile(
    r"(?:^|[-:;]\s*)"
    r"([A-Za-z][A-Za-z0-9@._\-]*(?:\s+[A-Za-z][A-Za-z0-9@._\-]*){0,2})"
    r"\s+to\s+[A-Za-z][A-Za-z0-9_\-]*\b",
    re.IGNORECASE,
)
_SELF_ASSIGN_RE = re.compile(
    r"\b(?:assign(?:ed)?\s+to|owner\s*(?:is|as|:)?|for)\s+(me|myself|self|i)\b",
    re.IGNORECASE,
)
_DISALLOWED_OWNER_HINTS = {
    "someone",
    "anyone",
    "team",
    "we",
    "you",
    "customer",
    "client",
    "project",
    "task",
    "action",
    "item",
    "create",
    "add",
    "make",
    "open",
    "log",
    "track",
    "note",
    "ask",
    "check",
    "collect",
    "run",
    "start",
    "can",
    "could",
    "would",
    "should",
    "please",
    "pls",
    "kindly",
}
_SELF_HINTS = {"me", "myself", "self", "i", "my", "mine"}


@dataclass(frozen=True)
class OwnerIntentParseResult:
    owner_hint: Optional[str]
    intent_detected: bool
    confidence: str  # strong | weak | none


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _is_disallowed_owner_phrase(value: str) -> bool:
    lowered = _normalize_text(value).lower()
    if not lowered:
        return True
    if "action item" in lowered:
        return True
    if lowered.startswith("create "):
        return True
    if lowered in _DISALLOWED_OWNER_HINTS:
        return True
    return False


def parse_owner_intent(text: str, candidate_owner_hint: Optional[str] = None) -> OwnerIntentParseResult:
    normalized_text = _normalize_text(text)
    candidate_hint = _normalize_text(candidate_owner_hint)

    if not normalized_text:
        # Some callers may accidentally pass full natural-language utterance in
        # candidate_owner_hint (for example noisy tool arg payloads). Re-parse it
        # as free text before falling back to strict structured-hint acceptance.
        if candidate_hint:
            reparsed = parse_owner_intent(candidate_hint, None)
            if reparsed.intent_detected:
                return reparsed
        if not candidate_hint:
            return OwnerIntentParseResult(owner_hint=None, intent_detected=False, confidence="none")
        if candidate_hint.lower() in _SELF_HINTS:
            return OwnerIntentParseResult(owner_hint=candidate_hint, intent_detected=True, confidence="strong")
        # Structured owner hints from tool args can be accepted when no raw utterance is present.
        if candidate_hint and not _is_disallowed_owner_phrase(candidate_hint):
            return OwnerIntentParseResult(owner_hint=candidate_hint, intent_detected=True, confidence="strong")
        return OwnerIntentParseResult(owner_hint=None, intent_detected=False, confidence="none")

    self_match = _SELF_ASSIGN_RE.search(normalized_text)
    if self_match:
        extracted = _normalize_text(self_match.group(1))
        if extracted:
            return OwnerIntentParseResult(owner_hint=extracted, intent_detected=True, confidence="strong")

    assign_match = _ASSIGN_TO_RE.search(normalized_text)
    if assign_match:
        extracted = _normalize_text(assign_match.group(1))
        if extracted and not _is_disallowed_owner_phrase(extracted):
            return OwnerIntentParseResult(owner_hint=extracted, intent_detected=True, confidence="strong")

    owner_as_match = _OWNER_AS_RE.search(normalized_text)
    if owner_as_match:
        extracted = _normalize_text(owner_as_match.group(1))
        if extracted and not _is_disallowed_owner_phrase(extracted):
            return OwnerIntentParseResult(owner_hint=extracted, intent_detected=True, confidence="strong")

    create_for_match = _CREATE_FOR_OWNER_RE.search(normalized_text)
    if create_for_match:
        extracted = _normalize_text(create_for_match.group(1))
        if extracted and extracted.lower() not in _DISALLOWED_OWNER_HINTS and not _is_disallowed_owner_phrase(extracted):
            return OwnerIntentParseResult(owner_hint=extracted, intent_detected=True, confidence="weak")

    name_to_action_match = _NAME_TO_ACTION_RE.search(normalized_text)
    if name_to_action_match:
        extracted = _normalize_text(name_to_action_match.group(1))
        if extracted and extracted.lower() not in _DISALLOWED_OWNER_HINTS and not _is_disallowed_owner_phrase(extracted):
            return OwnerIntentParseResult(owner_hint=extracted, intent_detected=True, confidence="strong")

    if not _OWNER_INTENT_CUE_RE.search(normalized_text):
        return OwnerIntentParseResult(owner_hint=None, intent_detected=False, confidence="none")

    hint = candidate_hint
    if hint and not _is_disallowed_owner_phrase(hint):
        return OwnerIntentParseResult(owner_hint=hint, intent_detected=True, confidence="weak")
    return OwnerIntentParseResult(owner_hint=None, intent_detected=True, confidence="weak")
