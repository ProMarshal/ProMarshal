"""Versioned quality policy artifact loader and validator."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.config import settings


logger = logging.getLogger(__name__)
_WARNED: set[str] = set()
_CACHE: Optional[Tuple[str, float, "QualityPolicyArtifact"]] = None
SUPPORTED_FIELD_CLASSES = {
    "title_like",
    "description_like",
    "comment_like",
    "status_like",
    "identifier_like",
    "assignee_like",
    "text_like",
}


class FieldPolicyOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_chars: Optional[int] = Field(default=None, ge=1, le=10000)
    semantic_when: Optional[str] = None
    assessor_fail_mode: Optional[str] = None
    semantic_hint: Optional[str] = None
    read_fail_open: Optional[bool] = None

    @field_validator("semantic_when", mode="before")
    @classmethod
    def _normalize_semantic_when(cls, value: Optional[Any]) -> Optional[str]:
        if value is None:
            return None
        token = str(value).strip().lower()
        if not token:
            return None
        if token not in {"never", "inferred_only"}:
            raise ValueError("semantic_when must be one of: never, inferred_only")
        return token

    @field_validator("assessor_fail_mode", mode="before")
    @classmethod
    def _normalize_assessor_fail_mode(cls, value: Optional[Any]) -> Optional[str]:
        if value is None:
            return None
        token = str(value).strip().lower()
        if not token:
            return None
        if token not in {"allow_if_deterministic_pass", "fail_closed"}:
            raise ValueError(
                "assessor_fail_mode must be one of: allow_if_deterministic_pass, fail_closed"
            )
        return token


class QualityPolicyArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    field_class_defaults: Dict[str, FieldPolicyOverride] = Field(default_factory=dict)
    tool_field_overrides: Dict[str, Dict[str, FieldPolicyOverride]] = Field(default_factory=dict)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _normalize_schema_version(cls, value: Any) -> str:
        token = str(value or "").strip()
        if not token:
            raise ValueError("schema_version is required")
        if token != "1.0":
            raise ValueError("schema_version must be 1.0")
        return token

    @field_validator("field_class_defaults", mode="before")
    @classmethod
    def _normalize_field_class_defaults(
        cls, value: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}
        for key, item in (value or {}).items():
            token = str(key or "").strip().lower()
            if token:
                normalized[token] = item
        return normalized

    @field_validator("tool_field_overrides", mode="before")
    @classmethod
    def _normalize_tool_field_overrides(
        cls, value: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}
        for tool_name, per_field in (value or {}).items():
            tool_token = str(tool_name or "").strip().lower()
            if not tool_token or not isinstance(per_field, dict):
                continue
            normalized_fields: Dict[str, Any] = {}
            for field_name, override in per_field.items():
                field_token = str(field_name or "").strip().lower()
                if field_token:
                    normalized_fields[field_token] = override
            normalized[tool_token] = normalized_fields
        return normalized

    @field_validator("field_class_defaults")
    @classmethod
    def _validate_supported_field_classes(
        cls, value: Dict[str, FieldPolicyOverride]
    ) -> Dict[str, FieldPolicyOverride]:
        unknown = sorted(
            key for key in (value or {}).keys() if key not in SUPPORTED_FIELD_CLASSES
        )
        if unknown:
            raise ValueError(
                "field_class_defaults contains unsupported classes: "
                + ", ".join(unknown)
            )
        return value


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _resolve_artifact_path() -> Path:
    raw = str(getattr(settings, "cortex_quality_policy_artifact_path", "") or "").strip()
    if not raw:
        raw = "api/app/cortex/policies/quality-policy.v1.json"
    path = Path(raw)
    if path.is_absolute():
        return path
    return _repo_root() / path


def _warn_once(key: str, message: str, *args: Any) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    logger.warning(message, *args)


def load_quality_policy_artifact(*, force_reload: bool = False) -> Optional[QualityPolicyArtifact]:
    if not bool(getattr(settings, "cortex_quality_policy_registry_enabled", False)):
        return None

    path = _resolve_artifact_path()
    warn_key = str(path)
    fail_fast = bool(getattr(settings, "cortex_quality_policy_fail_fast", False))
    global _CACHE

    try:
        stat = path.stat()
    except FileNotFoundError:
        if fail_fast:
            raise RuntimeError(f"quality_policy_artifact_missing:{path}")
        _warn_once(warn_key, "Cortex quality policy artifact not found, using built-ins: %s", path)
        return None
    except Exception as exc:
        if fail_fast:
            raise RuntimeError(f"quality_policy_artifact_stat_failed:{path}:{exc}")
        _warn_once(warn_key, "Cortex quality policy artifact stat failed, using built-ins: %s (%s)", path, exc)
        return None

    cache_key = str(path.resolve())
    if (
        not force_reload
        and _CACHE is not None
        and _CACHE[0] == cache_key
        and float(_CACHE[1]) == float(stat.st_mtime)
    ):
        return _CACHE[2]

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        artifact = QualityPolicyArtifact.model_validate(payload)
    except ValidationError as exc:
        if fail_fast:
            raise RuntimeError(f"quality_policy_artifact_invalid:{path}:{exc}")
        _warn_once(warn_key, "Cortex quality policy artifact invalid, using built-ins: %s", path)
        return None
    except Exception as exc:
        if fail_fast:
            raise RuntimeError(f"quality_policy_artifact_load_failed:{path}:{exc}")
        _warn_once(warn_key, "Cortex quality policy artifact load failed, using built-ins: %s (%s)", path, exc)
        return None

    _CACHE = (cache_key, float(stat.st_mtime), artifact)
    return artifact


def validate_quality_policy_startup() -> None:
    """Validate artifact once at startup when registry is enabled."""
    _ = load_quality_policy_artifact(force_reload=True)


def _reset_quality_policy_cache_for_tests() -> None:
    global _CACHE
    _CACHE = None
    _WARNED.clear()


def quality_policy_runtime_metadata() -> Dict[str, Any]:
    """Return quality policy registry metadata for telemetry/debugging."""
    enabled = bool(getattr(settings, "cortex_quality_policy_registry_enabled", False))
    artifact_path = str(getattr(settings, "cortex_quality_policy_artifact_path", "") or "").strip()
    metadata: Dict[str, Any] = {
        "quality_policy_registry_enabled": enabled,
        "quality_policy_artifact_path": artifact_path,
        "quality_policy_schema_version": "",
        "quality_policy_loaded": False,
    }
    if not enabled:
        return metadata
    artifact = load_quality_policy_artifact(force_reload=False)
    if artifact is None:
        return metadata
    metadata["quality_policy_schema_version"] = str(artifact.schema_version or "")
    metadata["quality_policy_loaded"] = True
    return metadata
