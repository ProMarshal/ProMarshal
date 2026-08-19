"""Shared parse outcome models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


PARSE_STATUS_RESOLVED = "resolved"
PARSE_STATUS_AMBIGUOUS = "ambiguous"
PARSE_STATUS_INVALID = "invalid"
PARSE_STATUS_UNSUPPORTED = "unsupported"

PARSE_STATUSES = {
    PARSE_STATUS_RESOLVED,
    PARSE_STATUS_AMBIGUOUS,
    PARSE_STATUS_INVALID,
    PARSE_STATUS_UNSUPPORTED,
}


@dataclass(frozen=True)
class ParseOutcome:
    """Normalized parser contract shared by Cadence and Cortex."""

    status: str
    reason_code: str
    confidence: float
    requires_clarification: bool
    normalized_payload: Dict[str, Any] = field(default_factory=dict)

    def should_block_mutation(self) -> bool:
        """Return True when write paths must not execute."""
        return self.status in {PARSE_STATUS_AMBIGUOUS, PARSE_STATUS_INVALID}


def clamp_confidence(value: Any, *, fallback: float = 0.0) -> float:
    """Clamp arbitrary confidence value into [0.0, 1.0]."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = fallback
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def normalize_parse_status(raw_status: Any) -> str:
    """Normalize parse status into one of the supported contract values."""
    normalized = str(raw_status or "").strip().lower()
    if normalized in PARSE_STATUSES:
        return normalized
    return PARSE_STATUS_INVALID

