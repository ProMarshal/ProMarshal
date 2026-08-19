"""Shared parser adapter registry and reason-message catalog."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional, Union

from app.domain.parsing.models import ParseOutcome


ParseAdapter = Callable[..., Union[ParseOutcome, Awaitable[ParseOutcome]]]


class ParseRegistry:
    """Simple parser adapter registry keyed by parser identifier."""

    def __init__(self) -> None:
        self._adapters: Dict[str, ParseAdapter] = {}

    def register(self, key: str, adapter: ParseAdapter) -> None:
        normalized = str(key or "").strip().lower()
        if not normalized:
            raise ValueError("parser key is required")
        self._adapters[normalized] = adapter

    def get(self, key: str) -> Optional[ParseAdapter]:
        normalized = str(key or "").strip().lower()
        if not normalized:
            return None
        return self._adapters.get(normalized)


_REASON_MESSAGE_CATALOG: Dict[str, str] = {
    "resolved": "",
    "ambiguous_mixed_signals": (
        "I found mixed status signals in your update. Please reply with one status only: "
        "`to do`, `in progress`, `in review`, `done`, or `no change`."
    ),
    "ambiguous_target": "I couldn't determine the exact target. Please clarify what to update.",
    "invalid_input": "I couldn't parse that update reliably. Please restate it in one sentence.",
    "clarification_required": "I need a quick clarification before I continue.",
    "unsupported_capability": "I cannot do that operation with the currently available tools.",
    "missing_capability": "I cannot do that operation with the currently available tools.",
    "required_tool_call_missing": "I cannot do that operation with the currently available tools.",
    "ambiguous_provider": "I found multiple connected providers for that action. Please specify which provider to use.",
    "low_confidence": "I need a bit more detail before I can safely proceed with that action.",
    "deterministic_unmatched": "Please rephrase the action so I can match it reliably.",
}


def message_for_reason_code(
    reason_code: str,
    *,
    fallback: str = "I need a clarification before I proceed.",
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """Return deterministic user-facing message for reason code."""
    normalized = str(reason_code or "").strip().lower()
    base = _REASON_MESSAGE_CATALOG.get(normalized, fallback)
    if normalized == "ambiguous_target" and isinstance(context, dict):
        target_kind = str(context.get("target_kind") or "").strip().lower()
        if target_kind == "backlog":
            return "I couldn't determine whether you want to see backlog tasks or skip for now."
    if normalized == "clarification_required" and isinstance(context, dict):
        unavailable = str(context.get("requested_status") or "").strip()
        available = context.get("available_statuses") or []
        clean_available = [str(item).strip() for item in available if str(item).strip()]
        if unavailable and clean_available:
            options = ", ".join(f"`{item}`" for item in clean_available[:6])
            return (
                f"`{unavailable}` is not available for this issue. "
                f"Available statuses are {options}. Which status should I apply?"
            )
    if normalized == "ambiguous_provider" and isinstance(context, dict):
        providers = [
            str(item).strip().lower()
            for item in (context.get("providers") or [])
            if str(item).strip()
        ]
        capability = str(context.get("capability") or "").strip()
        if providers and capability:
            listed = ", ".join(f"`{item}`" for item in providers[:4])
            return (
                f"Multiple providers can handle `{capability}` ({listed}). "
                "Tell me which provider to use."
            )
        if providers:
            listed = ", ".join(f"`{item}`" for item in providers[:4])
            return f"Multiple providers are connected ({listed}). Tell me which provider to use."
    if normalized in {"unsupported_capability", "missing_capability", "required_tool_call_missing"} and isinstance(context, dict):
        operations = [
            str(item).strip()
            for item in (context.get("operations") or [])
            if str(item).strip()
        ]
        if len(operations) == 1:
            return f"{base} Unsupported operation: `{operations[0]}`."
        if len(operations) > 1:
            listed = ", ".join(f"`{item}`" for item in operations[:3])
            return f"{base} Unsupported operations: {listed}."
    return base
