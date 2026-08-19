from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.core.config import settings


def _clamp_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(min_value, min(max_value, parsed))


def _clamp_float(value: Any, *, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    return max(min_value, min(max_value, parsed))


def default_recommendation_thresholds() -> Dict[str, Any]:
    return {
        "overload_threshold": _clamp_float(
            getattr(settings, "recommendation_overload_threshold", 70.0),
            default=70.0,
            min_value=0.0,
            max_value=100.0,
        ),
        "due_soon_hours": _clamp_int(
            getattr(settings, "recommendation_due_soon_hours", 48),
            default=48,
            min_value=1,
            max_value=336,
        ),
        "sprint_end_soon_hours": _clamp_int(
            getattr(settings, "recommendation_sprint_end_soon_hours", 72),
            default=72,
            min_value=1,
            max_value=336,
        ),
        "sprint_open_critical_threshold": _clamp_int(
            getattr(settings, "recommendation_sprint_open_critical_threshold", 2),
            default=2,
            min_value=1,
            max_value=20,
        ),
    }


def _apply_threshold_overrides(base: Dict[str, Any], overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(base or {})
    raw = dict(overrides or {})
    if not raw:
        return merged
    if "overload_threshold" in raw:
        merged["overload_threshold"] = _clamp_float(
            raw.get("overload_threshold"),
            default=merged["overload_threshold"],
            min_value=0.0,
            max_value=100.0,
        )
    if "due_soon_hours" in raw:
        merged["due_soon_hours"] = _clamp_int(
            raw.get("due_soon_hours"),
            default=merged["due_soon_hours"],
            min_value=1,
            max_value=336,
        )
    if "sprint_end_soon_hours" in raw:
        merged["sprint_end_soon_hours"] = _clamp_int(
            raw.get("sprint_end_soon_hours"),
            default=merged["sprint_end_soon_hours"],
            min_value=1,
            max_value=336,
        )
    if "sprint_open_critical_threshold" in raw:
        merged["sprint_open_critical_threshold"] = _clamp_int(
            raw.get("sprint_open_critical_threshold"),
            default=merged["sprint_open_critical_threshold"],
            min_value=1,
            max_value=20,
        )
    return merged


def normalize_recommendation_thresholds(
    overrides: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    return _apply_threshold_overrides(default_recommendation_thresholds(), overrides)


def _normalize_project_type_key(value: Any) -> str:
    text = " ".join(str(value or "").strip().lower().replace("_", " ").split())
    if not text:
        return ""
    if "implementation" in text:
        return "implementation"
    if "software" in text or "product" in text:
        return "software_product"
    return text


def _builtin_project_type_threshold_profiles() -> Dict[str, Dict[str, Any]]:
    return {
        # Implementation projects typically run longer validation/rollout windows.
        "implementation": {
            "due_soon_hours": 72,
            "sprint_end_soon_hours": 96,
        }
    }


def _settings_project_type_threshold_profiles() -> Dict[str, Dict[str, Any]]:
    raw = str(getattr(settings, "recommendation_project_type_thresholds_json", "") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for key, value in parsed.items():
        normalized_key = _normalize_project_type_key(key)
        if not normalized_key or not isinstance(value, dict):
            continue
        normalized[normalized_key] = dict(value)
    return normalized


def _normalize_workspace_key(value: Any) -> str:
    return str(value or "").strip().upper()


def _settings_workspace_threshold_profiles() -> Dict[str, Dict[str, Any]]:
    raw = str(getattr(settings, "recommendation_workspace_thresholds_json", "") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for key, value in parsed.items():
        normalized_key = _normalize_workspace_key(key)
        if not normalized_key or not isinstance(value, dict):
            continue
        normalized[normalized_key] = dict(value)
    return normalized


def _resolve_workspace_id(project_doc: Dict[str, Any]) -> str:
    direct = _normalize_workspace_key(project_doc.get("workspace_id"))
    if direct:
        return direct
    integrations = project_doc.get("integrations")
    if isinstance(integrations, dict):
        slack = integrations.get("slack")
        if isinstance(slack, dict):
            return _normalize_workspace_key(slack.get("team_id"))
    return ""


def _resolve_workspace_threshold_overrides(project_doc: Dict[str, Any]) -> Dict[str, Any]:
    workspace_key = _resolve_workspace_id(project_doc)
    if not workspace_key:
        return {}
    merged: Dict[str, Any] = {}

    settings_profile = _settings_workspace_threshold_profiles().get(workspace_key)
    if isinstance(settings_profile, dict):
        merged.update(settings_profile)

    project_profiles = project_doc.get("recommendation_thresholds_by_workspace")
    if isinstance(project_profiles, dict):
        project_profile = project_profiles.get(workspace_key)
        if isinstance(project_profile, dict):
            merged.update(project_profile)
    return merged


def _resolve_project_type_threshold_overrides(project_doc: Dict[str, Any]) -> Dict[str, Any]:
    key = _normalize_project_type_key(project_doc.get("project_type") or project_doc.get("type"))
    if not key:
        return {}

    merged: Dict[str, Any] = {}
    builtin = _builtin_project_type_threshold_profiles().get(key)
    if isinstance(builtin, dict):
        merged.update(builtin)

    settings_profile = _settings_project_type_threshold_profiles().get(key)
    if isinstance(settings_profile, dict):
        merged.update(settings_profile)

    project_profiles = project_doc.get("recommendation_thresholds_by_project_type")
    if isinstance(project_profiles, dict):
        project_type_profile = project_profiles.get(key)
        if isinstance(project_type_profile, dict):
            merged.update(project_type_profile)
    return merged


def resolve_project_recommendation_thresholds(project_doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(project_doc, dict):
        return default_recommendation_thresholds()
    resolved = default_recommendation_thresholds()
    resolved = _apply_threshold_overrides(
        resolved,
        _resolve_workspace_threshold_overrides(project_doc),
    )
    resolved = _apply_threshold_overrides(
        resolved,
        _resolve_project_type_threshold_overrides(project_doc),
    )
    overrides = project_doc.get("recommendation_thresholds")
    if isinstance(overrides, dict):
        resolved = _apply_threshold_overrides(resolved, overrides)
    return resolved


def resolve_project_recommendation_thresholds_with_sources(
    project_doc: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    safe_doc = project_doc if isinstance(project_doc, dict) else {}
    defaults = default_recommendation_thresholds()
    workspace_profile = _resolve_workspace_threshold_overrides(safe_doc)
    project_type_profile = _resolve_project_type_threshold_overrides(safe_doc)
    project_override = (
        dict(safe_doc.get("recommendation_thresholds"))
        if isinstance(safe_doc.get("recommendation_thresholds"), dict)
        else {}
    )
    resolved = _apply_threshold_overrides(defaults, workspace_profile)
    resolved = _apply_threshold_overrides(resolved, project_type_profile)
    resolved = _apply_threshold_overrides(resolved, project_override)
    return {
        "resolved": resolved,
        "sources": {
            "defaults": defaults,
            "workspace_profile": workspace_profile,
            "project_type_profile": project_type_profile,
            "project_override": project_override,
        },
    }
