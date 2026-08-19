"""User-facing clarification formatting helpers."""

from __future__ import annotations

from typing import List


_FIELD_LABEL_OVERRIDES = {
    "external_id": "task ID",
    "entity_id": "task ID",
    "issue_id": "task ID",
    "task_id": "task ID",
    "issue_key": "task ID",
    "assignee_mode": "assignee",
    "assignee_value": "assignee",
    "include_closed": "include closed items",
    "include_unassigned": "include unassigned items",
    "due_date_mode": "due date filter",
    "read_source": "data source",
}


def _humanize_field_name(field_name: str) -> str:
    token = str(field_name or "").strip().lower()
    if not token:
        return ""
    override = _FIELD_LABEL_OVERRIDES.get(token)
    if override:
        return override
    return " ".join(part for part in token.split("_") if part)


def render_missing_field_labels(
    missing_fields: List[str],
    *,
    max_items: int = 4,
) -> List[str]:
    """Render stable user-facing labels from missing-field tokens."""
    labels: List[str] = []
    seen: set[str] = set()
    for item in missing_fields:
        token = str(item or "").strip()
        if not token:
            continue
        if "." in token:
            _tool_name, field_name = token.split(".", 1)
        else:
            field_name = token
        label = _humanize_field_name(field_name) or str(field_name).strip()
        if not label:
            continue
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
        if len(labels) >= max(1, int(max_items or 4)):
            break
    return labels


def build_missing_fields_response(
    missing_fields: List[str],
    *,
    max_items: int = 4,
) -> str:
    """Build a friendly missing-fields clarification response."""
    labels = render_missing_field_labels(missing_fields, max_items=max_items)
    if not labels:
        return "I can proceed once I have the missing details."
    return f"I can proceed once I have: {', '.join(labels)}."

