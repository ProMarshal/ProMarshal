"""Shared scope-gating models for Cortex and Slack ingress."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


SCOPE_GATE_PROJECT_RELATED = "project_related"
SCOPE_GATE_OUT_OF_SCOPE = "out_of_scope"
SCOPE_GATE_UNCERTAIN = "uncertain"
SCOPE_GATE_GREETING = "greeting"


@dataclass(frozen=True)
class ScopeGateDecision:
    """Deterministic Stage-1 scope gate decision."""

    classification: str
    reason_code: str
    signals: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScopeLlmDecision:
    """Stage-2 LLM scope decision mapped to existing orchestrator labels."""

    scope_classification: str
    reason_code: str
    detail_classification: str
    confidence: float

