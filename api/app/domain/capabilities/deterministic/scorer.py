"""Stage R3 deterministic confidence scoring helpers."""

from __future__ import annotations

from typing import Dict, List

from app.domain.capabilities.deterministic.models import DeterministicMatchResult


def score_matches(
    *,
    text: str,
    matches: List[DeterministicMatchResult],
) -> Dict[str, float]:
    """Score deterministic confidence by slot coverage and signal strength."""
    normalized_text = str(text or "")
    text_len = max(1, len(normalized_text))

    signal_chars = sum(item.span_len() for item in matches)
    signal_strength = min(1.0, signal_chars / float(text_len))

    total_required = sum(len(item.required_fields) for item in matches)
    missing_required = sum(len(item.missing_required_fields) for item in matches)
    if total_required <= 0:
        slot_coverage = 1.0 if matches else 0.0
    else:
        slot_coverage = max(0.0, 1.0 - (missing_required / float(total_required)))

    confidence = max(0.0, min(1.0, (0.6 * slot_coverage) + (0.4 * signal_strength)))
    return {
        "confidence": confidence,
        "slot_coverage": slot_coverage,
        "signal_strength": signal_strength,
    }

