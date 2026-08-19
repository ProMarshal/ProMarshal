"""Cadence shared deterministic interpretation bridge."""

from __future__ import annotations

from typing import Optional

from app.domain.capabilities.deterministic.models import DeterministicInterpretation
from app.domain.capabilities.deterministic.pattern_registry import (
    list_action_patterns as list_canonical_action_patterns,
)
from app.domain.capabilities.deterministic.service import deterministic_interpretation_service


def interpret_cadence_deterministic(
    *,
    text: str,
    provider: str = "jira",
    capability_ids: Optional[list[str]] = None,
) -> DeterministicInterpretation:
    """Use shared Stage R1-R3 interpretation for Cadence inputs."""
    specs = list_canonical_action_patterns(
        provider=str(provider or "").strip().lower() or None,
        capability_ids=capability_ids,
    )
    if not specs and str(provider or "").strip().lower() == "jira":
        # Compatibility fallback for environments where startup installers have not run.
        from app.domain.capabilities.jira_bundles import get_jira_deterministic_pattern_specs

        specs = get_jira_deterministic_pattern_specs()
    return deterministic_interpretation_service.interpret(
        text=text,
        pattern_specs=specs,
    )
