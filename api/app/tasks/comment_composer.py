"""Deterministic comment text composer for command-style user phrasing."""

from __future__ import annotations

import re


def _normalize_whitespace(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def compose_comment_text(comment: str) -> str:
    """Convert command-style text into direct Jira comment phrasing."""
    text = _normalize_whitespace(comment)
    if not text:
        return text

    match = re.match(r"^ask\s+(.+?)\s+to\s+(.+)$", text, flags=re.IGNORECASE)
    if not match:
        return text

    target = str(match.group(1) or "").strip(" ,")
    action = str(match.group(2) or "").strip()
    if not action:
        return text

    action = re.sub(r"^\s*please\s+", "", action, flags=re.IGNORECASE).strip().rstrip(".")
    if not action:
        return text

    normalized_target = target.lower().strip()
    if normalized_target in {"requestor", "requester", "assignee", "owner"}:
        return f"Please {action}."

    if target and re.fullmatch(r"[@A-Za-z0-9._-]{2,64}", target):
        mention = target if target.startswith("@") else f"@{target}"
        return f"{mention}, please {action}."

    return f"Please {action}."
