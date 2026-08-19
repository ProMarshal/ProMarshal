"""Data models for shared deterministic interpretation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from re import Pattern
from typing import Any, Dict, List


_ACTION_TYPE_ALIASES = {
    "create_task": "create_work_item",
    "assign_task": "assign_work_item",
}


@dataclass(frozen=True)
class ActionPatternSpec:
    """Declarative deterministic pattern contract for one action type."""

    action_type: str
    capability_id: str
    regex: Pattern[str]
    field_extractors: Dict[str, str] = field(default_factory=dict)
    required_fields: List[str] = field(default_factory=list)
    tool_name: str = ""

    def normalized_action_type(self) -> str:
        normalized = str(self.action_type or "").strip().lower()
        return _ACTION_TYPE_ALIASES.get(normalized, normalized)

    def normalized_capability_id(self) -> str:
        return str(self.capability_id or "").strip().lower()

    def normalized_tool_name(self) -> str:
        return str(self.tool_name or "").strip()


@dataclass(frozen=True)
class DeterministicMatchResult:
    """Structured match output from Stage R2."""

    action_type: str
    capability_id: str
    tool_name: str
    start: int
    end: int
    raw_groups: Dict[str, str]
    extracted_slots: Dict[str, str]
    required_fields: List[str]
    missing_required_fields: List[str]

    def span_len(self) -> int:
        return max(0, int(self.end) - int(self.start))


@dataclass(frozen=True)
class DeterministicInterpretation:
    """Interpretation envelope for Stage R1-R3."""

    normalized_text: str
    matches: List[DeterministicMatchResult]
    confidence: float
    slot_coverage: float
    signal_strength: float
    missing_slots: Dict[str, List[str]] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

