"""Shared deterministic interpretation service (R1 -> R3)."""

from __future__ import annotations

from typing import Dict, Iterable, List

from app.domain.capabilities.deterministic.matcher import match_patterns
from app.domain.capabilities.deterministic.models import (
    ActionPatternSpec,
    DeterministicInterpretation,
    DeterministicMatchResult,
)
from app.domain.capabilities.deterministic.normalizer import normalize_text
from app.domain.capabilities.deterministic.scorer import score_matches


class DeterministicInterpretationService:
    """Normalize, match, and score deterministic intent interpretation."""

    def interpret(
        self,
        *,
        text: str,
        pattern_specs: Iterable[ActionPatternSpec],
    ) -> DeterministicInterpretation:
        normalized_text = normalize_text(text)
        specs = list(pattern_specs or [])
        matches: List[DeterministicMatchResult] = match_patterns(
            text=normalized_text,
            pattern_specs=specs,
        )
        score = score_matches(text=normalized_text, matches=matches)
        missing_slots: Dict[str, List[str]] = {}
        for item in matches:
            if not item.missing_required_fields:
                continue
            key = f"{item.action_type}:{item.capability_id}"
            missing_slots[key] = list(item.missing_required_fields)

        return DeterministicInterpretation(
            normalized_text=normalized_text,
            matches=matches,
            confidence=float(score.get("confidence", 0.0)),
            slot_coverage=float(score.get("slot_coverage", 0.0)),
            signal_strength=float(score.get("signal_strength", 0.0)),
            missing_slots=missing_slots,
            metadata={
                "pattern_count": len(specs),
                "match_count": len(matches),
            },
        )


deterministic_interpretation_service = DeterministicInterpretationService()

