"""Deterministic Stage-1 project scope gate."""

from __future__ import annotations

from typing import List

from app.domain.scope import registry
from app.domain.scope.models import (
    SCOPE_GATE_GREETING,
    SCOPE_GATE_OUT_OF_SCOPE,
    SCOPE_GATE_PROJECT_RELATED,
    SCOPE_GATE_UNCERTAIN,
    ScopeGateDecision,
)

def classify_project_scope_gate(text: str) -> ScopeGateDecision:
    """Deterministically classify whether a message is project-related."""
    normalized = str(text or "").strip().lower()
    if not normalized:
        return ScopeGateDecision(
            classification=SCOPE_GATE_OUT_OF_SCOPE,
            reason_code=registry.REASON_EMPTY_MESSAGE,
            signals=[],
        )

    tokens = registry.WORD_RE.findall(normalized)
    token_set = set(tokens)
    if not tokens:
        return ScopeGateDecision(
            classification=SCOPE_GATE_OUT_OF_SCOPE,
            reason_code=registry.REASON_NO_TEXT_TOKENS,
            signals=[],
        )

    if registry.PM_DEFINITION_RE.search(normalized):
        return ScopeGateDecision(
            classification=SCOPE_GATE_OUT_OF_SCOPE,
            reason_code=registry.REASON_PM_DEFINITION_QUERY,
            signals=[registry.SIGNAL_CONCEPTUAL_QUERY],
        )

    if registry.NON_PM_WORKLOAD_RE.search(normalized):
        return ScopeGateDecision(
            classification=SCOPE_GATE_OUT_OF_SCOPE,
            reason_code=registry.REASON_GENERAL_WORKLOAD_QUERY,
            signals=[registry.SIGNAL_GENERAL_QA_TOPIC],
        )

    if registry.is_pm_analytics_query(normalized=normalized, token_set=token_set):
        return ScopeGateDecision(
            classification=SCOPE_GATE_PROJECT_RELATED,
            reason_code=registry.REASON_PM_ANALYTICS_QUERY,
            signals=[registry.SIGNAL_ANALYTICS_QUERY, registry.SIGNAL_PROJECT_ENTITY_HINT],
        )

    if registry.is_project_broadcast_operation(normalized=normalized, token_set=token_set):
        return ScopeGateDecision(
            classification=SCOPE_GATE_PROJECT_RELATED,
            reason_code=registry.REASON_PROJECT_BROADCAST_OPERATION,
            signals=[registry.SIGNAL_BROADCAST_OPERATION, registry.SIGNAL_PROJECT_ACTION_HINT],
        )

    if len(tokens) <= 6 and all(token in registry.GREETING_WORDS for token in tokens):
        return ScopeGateDecision(
            classification=SCOPE_GATE_GREETING,
            reason_code=registry.REASON_GREETING_ONLY,
            signals=[registry.SIGNAL_GREETING_ONLY],
        )

    signals: List[str] = []
    if registry.WORK_ITEM_KEY_RE.search(normalized):
        signals.append(registry.SIGNAL_WORK_ITEM_KEY)
    if token_set.intersection(registry.PROJECT_ACTION_HINTS):
        signals.append(registry.SIGNAL_PROJECT_ACTION_HINT)
    if token_set.intersection(registry.PROJECT_ENTITY_HINTS):
        signals.append(registry.SIGNAL_PROJECT_ENTITY_HINT)
    if (
        ("promarshal" in token_set or "bot" in token_set or "you" in token_set)
        and token_set.intersection(registry.CAPABILITY_QUERY_HINTS)
    ):
        signals.append(registry.SIGNAL_CAPABILITY_QUERY)

    if signals:
        return ScopeGateDecision(
            classification=SCOPE_GATE_PROJECT_RELATED,
            reason_code=registry.REASON_PROJECT_SIGNAL_DETECTED,
            signals=sorted(set(signals)),
        )

    if token_set.intersection(registry.GENERAL_QA_HINTS):
        return ScopeGateDecision(
            classification=SCOPE_GATE_OUT_OF_SCOPE,
            reason_code=registry.REASON_GENERAL_QA_TOPIC,
            signals=[registry.SIGNAL_GENERAL_QA_TOPIC],
        )

    if normalized.startswith(registry.CONCEPTUAL_QUERY_PREFIXES):
        return ScopeGateDecision(
            classification=SCOPE_GATE_OUT_OF_SCOPE,
            reason_code=registry.REASON_CONCEPTUAL_QUERY_WITHOUT_PROJECT_SIGNAL,
            signals=[registry.SIGNAL_CONCEPTUAL_QUERY],
        )

    return ScopeGateDecision(
        classification=SCOPE_GATE_UNCERTAIN,
        reason_code=registry.REASON_INSUFFICIENT_PROJECT_SIGNALS,
        signals=[],
    )


def build_scope_refusal_response() -> str:
    return (
        "I can only help with project operations in ProMarshal. "
        "I can help with tasks, status updates, assignments, planning, and blockers. "
        "Tell me what you want to do in your project."
    )
