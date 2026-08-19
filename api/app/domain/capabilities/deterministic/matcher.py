"""Stage R2 deterministic matcher over registered pattern specs."""

from __future__ import annotations

from typing import Dict, Iterable, List

from app.domain.capabilities.deterministic.models import ActionPatternSpec, DeterministicMatchResult


def match_patterns(
    *,
    text: str,
    pattern_specs: Iterable[ActionPatternSpec],
) -> List[DeterministicMatchResult]:
    """Match deterministic patterns and extract normalized slots."""
    matches: List[DeterministicMatchResult] = []
    for spec in pattern_specs:
        for found in spec.regex.finditer(text):
            raw_groups: Dict[str, str] = {
                str(key): str(value or "")
                for key, value in (found.groupdict() or {}).items()
                if str(key).strip()
            }
            extracted_slots: Dict[str, str] = {}
            if spec.field_extractors:
                for source_name, target_name in spec.field_extractors.items():
                    source_key = str(source_name or "").strip()
                    target_key = str(target_name or "").strip()
                    if not source_key or not target_key:
                        continue
                    candidate = str(raw_groups.get(source_key, "") or "")
                    if candidate.strip():
                        extracted_slots[target_key] = candidate
                        continue
                    if target_key not in extracted_slots:
                        extracted_slots[target_key] = candidate
            else:
                extracted_slots = dict(raw_groups)

            required_fields = [str(item).strip() for item in (spec.required_fields or []) if str(item).strip()]
            missing_required = [
                field_name
                for field_name in required_fields
                if not str(extracted_slots.get(field_name, "") or "").strip()
            ]

            matches.append(
                DeterministicMatchResult(
                    action_type=spec.normalized_action_type(),
                    capability_id=spec.normalized_capability_id(),
                    tool_name=spec.normalized_tool_name(),
                    start=int(found.start()),
                    end=int(found.end()),
                    raw_groups=raw_groups,
                    extracted_slots=extracted_slots,
                    required_fields=required_fields,
                    missing_required_fields=missing_required,
                )
            )

    matches.sort(key=lambda item: (item.start, item.end, item.action_type))
    deduped: List[DeterministicMatchResult] = []
    for match in matches:
        if deduped and match.start < deduped[-1].end and match.action_type == deduped[-1].action_type:
            continue
        deduped.append(match)
    return deduped

