"""Post-processing guards for Cortex user-visible responses."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Set


class CortexResponseGuard:
    """Centralized response guard for commitment-vs-write-outcome consistency."""

    COMMITMENT_GUARD_CLAUSE = (
        "Note: one or more write actions did not complete successfully. "
        "Please verify current state before relying on this update."
    )

    _COMMITMENT_PATTERNS = (
        re.compile(
            r"\b(?:i|we)\s+(?:created|added|updated|scheduled|set|assigned|closed|sent|posted)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:i|we)'?ve\s+(?:created|added|updated|scheduled|set|assigned|closed|sent|posted)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:i|we)\s+will\s+(?:remind|follow up|update|create|schedule|send|post|assign)\b",
            re.IGNORECASE,
        ),
    )

    @classmethod
    def apply(
        cls,
        *,
        response_text: str,
        raw_response: Dict[str, Any],
        tool_descriptors: Iterable[Dict[str, Any]],
    ) -> str:
        text = str(response_text or "").strip()
        if not text:
            return text
        if cls.COMMITMENT_GUARD_CLAUSE in text:
            return text
        if not cls._contains_commitment_language(text):
            return text
        non_idempotent_tools = cls._extract_non_idempotent_tool_names(tool_descriptors)
        if not non_idempotent_tools:
            return text
        if not cls._has_failed_non_idempotent_write(
            raw_response=raw_response,
            non_idempotent_tool_names=non_idempotent_tools,
        ):
            return text
        return f"{text}\n\n{cls.COMMITMENT_GUARD_CLAUSE}"

    @classmethod
    def _contains_commitment_language(cls, text: str) -> bool:
        return any(pattern.search(text) for pattern in cls._COMMITMENT_PATTERNS)

    @staticmethod
    def _extract_non_idempotent_tool_names(
        tool_descriptors: Iterable[Dict[str, Any]],
    ) -> Set[str]:
        names: Set[str] = set()
        for descriptor in tool_descriptors or []:
            if not isinstance(descriptor, dict):
                continue
            tool_name = str(descriptor.get("name") or "").strip()
            idempotency = str(descriptor.get("idempotency") or "").strip().lower()
            if tool_name and idempotency == "non_idempotent":
                names.add(tool_name)
        return names

    @staticmethod
    def _has_failed_non_idempotent_write(
        *,
        raw_response: Dict[str, Any],
        non_idempotent_tool_names: Set[str],
    ) -> bool:
        raw = raw_response or {}
        errors = raw.get("tool_errors")
        if isinstance(errors, list):
            for item in errors:
                if not isinstance(item, dict):
                    continue
                tool_name = str(item.get("tool_name") or "").strip()
                if tool_name and tool_name in non_idempotent_tool_names:
                    return True

        try:
            tool_error_count = int(raw.get("tool_error_count") or 0)
            tool_success_count = int(raw.get("tool_success_count") or 0)
        except Exception:
            return False
        if tool_error_count <= 0:
            return False

        tool_names_called = raw.get("tool_names_called")
        if not isinstance(tool_names_called, list):
            return False
        called_non_idempotent = [
            str(name or "").strip()
            for name in tool_names_called
            if str(name or "").strip() in non_idempotent_tool_names
        ]
        if not called_non_idempotent:
            return False
        return tool_success_count < len(called_non_idempotent)

