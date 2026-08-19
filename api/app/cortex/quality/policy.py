"""Global policy resolver for Cortex quality engine."""

from __future__ import annotations

from typing import Any, Dict

from app.cortex.quality.models import OperationProfile
from app.cortex.quality.policy_registry import load_quality_policy_artifact
from app.cortex.tools.contracts import get_tool_arg_contract


DEFAULT_FIELD_POLICY_BY_CLASS: Dict[str, Dict[str, Any]] = {
    "title_like": {
        "min_chars": 4,
        "semantic_when": "inferred_only",
        "assessor_fail_mode": "allow_if_deterministic_pass",
    },
    "description_like": {
        "min_chars": 1,
        "semantic_when": "inferred_only",
        "assessor_fail_mode": "allow_if_deterministic_pass",
    },
    "comment_like": {
        "min_chars": 1,
        "semantic_when": "inferred_only",
        "assessor_fail_mode": "allow_if_deterministic_pass",
    },
    "status_like": {
        "min_chars": 1,
        "semantic_when": "never",
        "assessor_fail_mode": "allow_if_deterministic_pass",
    },
    "identifier_like": {
        "min_chars": 1,
        "semantic_when": "never",
        "assessor_fail_mode": "allow_if_deterministic_pass",
    },
    "assignee_like": {
        "min_chars": 1,
        "semantic_when": "never",
        "assessor_fail_mode": "allow_if_deterministic_pass",
    },
    "text_like": {
        "min_chars": 1,
        "semantic_when": "inferred_only",
        "assessor_fail_mode": "allow_if_deterministic_pass",
    },
}


def infer_field_class(field_name: str) -> str:
    """Infer global field class from canonical field name."""
    token = str(field_name or "").strip().lower()
    if not token:
        return "text_like"
    if token in {"title", "summary", "name", "subject"}:
        return "title_like"
    if token in {"description", "details", "body"}:
        return "description_like"
    if token in {"comment", "note"}:
        return "comment_like"
    if token in {"status", "state"}:
        return "status_like"
    if token in {"external_id", "task_id", "issue_id", "issue_key", "id", "entity_id"}:
        return "identifier_like"
    if "assignee" in token or token in {"owner", "member"}:
        return "assignee_like"
    return "text_like"


def resolve_field_policy(
    *,
    tool_name: str,
    field_name: str,
    operation_profile: OperationProfile,
) -> Dict[str, Any]:
    """
    Resolve effective policy for one field.

    Order:
    - class defaults
    - legacy tool field_quality policy (backward compatibility)
    - optional per-tool policy override
    - operation profile adjustments
    """
    contract = get_tool_arg_contract(tool_name)
    field_class = (
        str(contract.field_classes.get(field_name) or "").strip()
        or infer_field_class(field_name)
    )
    effective: Dict[str, Any] = {
        "field_class": field_class,
        **DEFAULT_FIELD_POLICY_BY_CLASS.get(field_class, DEFAULT_FIELD_POLICY_BY_CLASS["text_like"]),
    }

    legacy = contract.field_quality.get(field_name) or {}
    if isinstance(legacy, dict):
        # Backward-compatible mapping from current contract shape.
        if "min_chars" in legacy:
            effective["min_chars"] = int(legacy.get("min_chars") or effective.get("min_chars") or 1)
        if bool(legacy.get("semantic_required")):
            effective["semantic_when"] = "inferred_only"
        if str(legacy.get("semantic_hint") or "").strip():
            effective["semantic_hint"] = str(legacy.get("semantic_hint")).strip()

    override = contract.policy_overrides.get(field_name) or {}
    if isinstance(override, dict):
        for key, value in override.items():
            effective[key] = value

    artifact = load_quality_policy_artifact()
    if artifact is not None:
        class_override = artifact.field_class_defaults.get(str(field_class or "").strip().lower())
        if class_override is not None:
            for key, value in class_override.model_dump(exclude_none=True).items():
                if key == "schema_version":
                    continue
                effective[key] = value
        per_tool = artifact.tool_field_overrides.get(str(tool_name or "").strip().lower()) or {}
        field_override = per_tool.get(str(field_name or "").strip().lower())
        if field_override is not None:
            for key, value in field_override.model_dump(exclude_none=True).items():
                effective[key] = value

    if operation_profile == OperationProfile.READ_ONLY:
        effective["semantic_when"] = "never"
        effective.setdefault("read_fail_open", True)

    return effective

