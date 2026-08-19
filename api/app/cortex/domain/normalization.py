"""Shared normalization helpers for enum-like values across Cortex paths."""

from __future__ import annotations

import re
from typing import Dict, Optional


_MULTI_UNDERSCORE_RE = re.compile(r"_+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_enum_token(value: str) -> str:
    """Normalize free-form enum token into canonical lowercase underscore form."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.replace("-", "_")
    text = _WHITESPACE_RE.sub("_", text)
    text = _MULTI_UNDERSCORE_RE.sub("_", text).strip("_")
    return text


def resolve_enum_alias(value: str, aliases: Dict[str, str]) -> Optional[str]:
    """
    Resolve enum-like token through alias map with separator-insensitive matching.

    Matching is applied on both normalized and compacted keys (with underscores removed)
    so variants like `in progress`, `in-progress`, and `inprogress` resolve consistently.
    """
    token = normalize_enum_token(value)
    if not token:
        return None
    compact = token.replace("_", "")

    normalized_aliases: Dict[str, str] = {}
    for raw_key, raw_value in (aliases or {}).items():
        key = normalize_enum_token(str(raw_key or ""))
        if not key:
            continue
        value_token = str(raw_value or "").strip()
        if not value_token:
            continue
        normalized_aliases.setdefault(key, value_token)
        normalized_aliases.setdefault(key.replace("_", ""), value_token)

    return normalized_aliases.get(token) or normalized_aliases.get(compact)
