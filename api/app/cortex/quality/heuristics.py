"""Deterministic argument hygiene helpers."""

from __future__ import annotations

from typing import Any, Dict


PLACEHOLDER_TOKENS = {
    "",
    "na",
    "n/a",
    "none",
    "null",
    "nil",
    "unknown",
    "tbd",
    "-",
}


def normalize_text(value: Any) -> str:
    """Return compact lowercase text for matching."""
    return " ".join(str(value or "").strip().lower().split())


def is_placeholder_value(value: Any) -> bool:
    """Return True when value is an empty/template placeholder."""
    return normalize_text(value) in PLACEHOLDER_TOKENS


def sanitize_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """Drop empty/null placeholders and trim string values."""
    sanitized: Dict[str, Any] = {}
    for key, value in (args or {}).items():
        if value is None:
            continue
        if isinstance(value, str):
            compact = value.strip()
            if not compact:
                continue
            if is_placeholder_value(compact):
                continue
            sanitized[key] = compact
            continue
        sanitized[key] = value
    return sanitized


def appears_explicit_in_user_text(value: Any, context: Dict[str, Any]) -> bool:
    """
    Return True when candidate value appears directly in user text.

    This provides a low-cost provenance hint to avoid unnecessary semantic blocks.
    """
    candidate = normalize_text(value)
    if not candidate:
        return False

    user_text = normalize_text(
        str(context.get("latest_user_message") or context.get("user_message") or "")
    )
    if not user_text:
        return False
    if candidate in user_text:
        return True

    candidate_tokens = [token for token in candidate.split(" ") if token]
    user_tokens = [token for token in user_text.split(" ") if token]
    if not candidate_tokens or not user_tokens:
        return False
    if len(candidate_tokens) == 1:
        return candidate_tokens[0] in set(user_tokens)

    candidate_set = set(candidate_tokens)
    user_set = set(user_tokens)
    overlap = len(candidate_set.intersection(user_set))
    if overlap <= 0:
        return False
    overlap_ratio = overlap / float(max(1, len(candidate_set)))
    # Robust explicitness check: tolerate minor rephrasing while preventing weak matches.
    return overlap >= 2 and overlap_ratio >= 0.66
