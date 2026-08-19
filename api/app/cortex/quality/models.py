"""Data contracts for Cortex quality decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional


class OperationProfile(str, Enum):
    """Operation profile used by quality policy resolution."""

    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"


@dataclass(frozen=True)
class FieldMeta:
    """Field-level provenance metadata."""

    provenance: Literal[
        "explicit_user_text",
        "deterministic_parse",
        "llm_inferred",
        "llm_repaired",
        "followup_user",
    ] = "llm_inferred"
    confidence: Optional[float] = None
    confirmed_by_user: bool = False
    source_span: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ArgumentEnvelope:
    """Canonical quality evaluation input."""

    tool_name: str
    args: Dict[str, Any]
    operation_profile: OperationProfile
    field_meta: Dict[str, FieldMeta] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualityIssue:
    """One quality finding for a tool field."""

    field: str
    code: str
    message: str
    retryable: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualityDecision:
    """Output of quality evaluation for one tool call."""

    status: Literal["accepted", "clarification_required", "rejected", "transient_failure"]
    normalized_args: Dict[str, Any]
    issues: List[QualityIssue] = field(default_factory=list)
    prevalidated_fields: List[str] = field(default_factory=list)
    missing_fields: List[str] = field(default_factory=list)
    blocking: bool = False

