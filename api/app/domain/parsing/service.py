"""Runtime parsing service built on shared ParseOutcome contract."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from app.domain.parsing.models import (
    PARSE_STATUS_AMBIGUOUS,
    PARSE_STATUS_INVALID,
    PARSE_STATUS_UNSUPPORTED,
    ParseOutcome,
    clamp_confidence,
)
from app.domain.parsing.registry import ParseRegistry


ParseAdapterFunc = Union[
    Callable[..., ParseOutcome],
    Callable[..., Awaitable[ParseOutcome]],
]

ACTION_ALLOW_WRITE = "allow_write"
ACTION_CLARIFY = "clarify"
ACTION_REJECT = "reject"


@dataclass(frozen=True)
class ParseGateDecision:
    """Normalized decision emitted by shared mutation gate."""

    action: str
    reason_code: str
    outcome: ParseOutcome

    def allows_write(self) -> bool:
        return self.action == ACTION_ALLOW_WRITE

    def requires_clarification(self) -> bool:
        return self.action == ACTION_CLARIFY

    def should_reject(self) -> bool:
        return self.action == ACTION_REJECT


_runtime_registry = ParseRegistry()
_runtime_bootstrap_attempted: set[str] = set()


def register_runtime_adapter(key: str, adapter: ParseAdapterFunc) -> None:
    """Register one runtime parsing adapter."""
    _runtime_registry.register(key, adapter)  # type: ignore[arg-type]


def get_runtime_adapter(key: str) -> Optional[ParseAdapterFunc]:
    """Return runtime adapter if present."""
    adapter = _runtime_registry.get(key)
    return adapter  # type: ignore[return-value]


def _bootstrap_runtime_adapter_for_key(key: str) -> None:
    """Best-effort, one-time runtime adapter bootstrap by key namespace."""
    normalized = str(key or "").strip().lower()
    if not normalized or "." not in normalized:
        return
    namespace = normalized.split(".", 1)[0]
    if namespace in _runtime_bootstrap_attempted:
        return
    _runtime_bootstrap_attempted.add(namespace)
    try:
        if namespace == "cortex":
            module = import_module("app.cortex.parsing_adapter")
            register = getattr(module, "register_cortex_runtime_adapters", None)
            if callable(register):
                register()
    except Exception:
        # Bootstrap is best-effort only; unresolved adapters fail closed below.
        return


async def parse_with_runtime_adapter(
    key: str,
    **kwargs: Any,
) -> ParseOutcome:
    """Execute registered adapter and normalize failure behavior."""
    adapter = get_runtime_adapter(key)
    if adapter is None:
        _bootstrap_runtime_adapter_for_key(key)
        adapter = get_runtime_adapter(key)
    if adapter is None:
        return ParseOutcome(
            status=PARSE_STATUS_UNSUPPORTED,
            reason_code="unsupported_capability",
            confidence=clamp_confidence(0.0),
            requires_clarification=False,
            normalized_payload={
                "adapter_key": str(key or "").strip(),
                "error": "adapter_not_registered",
            },
        )
    try:
        result = adapter(**kwargs)
        if isinstance(result, ParseOutcome):
            return result
        if hasattr(result, "__await__"):
            awaited = await result  # type: ignore[misc]
            if isinstance(awaited, ParseOutcome):
                return awaited
    except Exception as exc:
        return ParseOutcome(
            status=PARSE_STATUS_INVALID,
            reason_code="invalid_input",
            confidence=clamp_confidence(0.0),
            requires_clarification=True,
            normalized_payload={
                "adapter_key": str(key or "").strip(),
                "error": f"adapter_exception:{exc}",
            },
        )
    return ParseOutcome(
        status=PARSE_STATUS_INVALID,
        reason_code="invalid_input",
        confidence=clamp_confidence(0.0),
        requires_clarification=True,
        normalized_payload={
            "adapter_key": str(key or "").strip(),
            "error": "adapter_return_type_invalid",
        },
    )


def enforce_mutation_gate(outcome: ParseOutcome) -> ParseGateDecision:
    """Shared deterministic gate before non-idempotent mutations."""
    status = str(getattr(outcome, "status", "") or "").strip().lower()
    reason_code = str(getattr(outcome, "reason_code", "") or "").strip().lower() or "clarification_required"
    if status == "resolved":
        return ParseGateDecision(
            action=ACTION_ALLOW_WRITE,
            reason_code=reason_code or "resolved",
            outcome=outcome,
        )
    if status in {PARSE_STATUS_AMBIGUOUS, PARSE_STATUS_INVALID}:
        return ParseGateDecision(
            action=ACTION_CLARIFY,
            reason_code=reason_code or "clarification_required",
            outcome=outcome,
        )
    if status == PARSE_STATUS_UNSUPPORTED:
        return ParseGateDecision(
            action=ACTION_REJECT,
            reason_code=reason_code or "unsupported_capability",
            outcome=outcome,
        )
    # Fail-closed for unknown parse status.
    return ParseGateDecision(
        action=ACTION_CLARIFY,
        reason_code=reason_code or "clarification_required",
        outcome=outcome,
    )


def build_clarification_payload(
    *,
    reason_code: str,
    original_user_text: str,
    parser_understanding: Dict[str, Any],
    clarification_kind: str,
    retry_count: int = 0,
    max_retries: int = 2,
    candidate_statuses: Optional[List[str]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a shared clarification envelope payload."""
    payload: Dict[str, Any] = {
        "reason_code": str(reason_code or "clarification_required").strip().lower(),
        "original_user_text": str(original_user_text or "").strip(),
        "parser_understanding": dict(parser_understanding or {}),
        "retry_count": int(retry_count),
        "max_retries": int(max_retries),
        "clarification_kind": str(clarification_kind or "generic").strip().lower(),
    }
    clean_candidates = [str(item).strip() for item in (candidate_statuses or []) if str(item).strip()]
    if clean_candidates:
        payload["candidate_statuses"] = clean_candidates
    if isinstance(extra, dict):
        for key, value in extra.items():
            if value is None:
                continue
            payload[str(key)] = value
    return payload
