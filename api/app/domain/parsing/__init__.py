"""Shared parse outcome contracts and registries."""

from app.domain.parsing.models import ParseOutcome
from app.domain.parsing.registry import ParseRegistry, message_for_reason_code
from app.domain.parsing.service import (
    ParseGateDecision,
    build_clarification_payload,
    enforce_mutation_gate,
    parse_with_runtime_adapter,
    register_runtime_adapter,
)

__all__ = [
    "ParseOutcome",
    "ParseRegistry",
    "ParseGateDecision",
    "build_clarification_payload",
    "enforce_mutation_gate",
    "message_for_reason_code",
    "parse_with_runtime_adapter",
    "register_runtime_adapter",
]
