"""Backward-compatible ProMarshal scope policy facade used by tests and legacy callers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from app.domain.scope import build_scope_refusal_response as _build_scope_refusal_response
from app.domain.scope import classify_project_scope_gate
from app.domain.scope.models import (
    SCOPE_GATE_GREETING,
    SCOPE_GATE_OUT_OF_SCOPE,
    SCOPE_GATE_PROJECT_RELATED,
    SCOPE_GATE_UNCERTAIN,
)


@dataclass(frozen=True)
class ScopePolicyDecision:
    classification: str
    reason_code: str
    signals: List[str]


def classify_scope_for_message(text: str) -> ScopePolicyDecision:
    normalized = str(text or "").strip()
    lowered = normalized.lower()

    # Short confirmation-style follow-ups are treated as in-scope action continuations.
    if lowered in {"yes", "y", "ok", "okay", "sure", "go ahead", "continue", "done"}:
        return ScopePolicyDecision(
            classification="in_scope_action",
            reason_code="followup_confirmation",
            signals=["followup_confirmation"],
        )

    gate = classify_project_scope_gate(normalized)
    reason_code = str(getattr(gate, "reason_code", "") or "").strip() or "scope_inferred"
    signals = list(getattr(gate, "signals", []) or [])

    if gate.classification == SCOPE_GATE_GREETING:
        return ScopePolicyDecision("greeting", reason_code, signals)
    if gate.classification == SCOPE_GATE_PROJECT_RELATED:
        non_capability_signals = [s for s in signals if s != "capability_query"]
        if signals and not non_capability_signals and "capability_query" in signals:
            return ScopePolicyDecision("in_scope_info", "capability_query", signals)
        return ScopePolicyDecision("in_scope_action", reason_code, signals)
    if gate.classification == SCOPE_GATE_UNCERTAIN:
        return ScopePolicyDecision("in_scope_action", reason_code, signals)
    if gate.classification == SCOPE_GATE_OUT_OF_SCOPE:
        return ScopePolicyDecision("out_of_scope", reason_code, signals)

    return ScopePolicyDecision("in_scope_action", "scope_inferred", signals)


def build_scope_refusal_response() -> str:
    return _build_scope_refusal_response()
