"""Structured LLM proposal contracts for implicit actionable requests."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCOPE_CLASSIFICATIONS = {"in_scope_action", "in_scope_info", "out_of_scope", "ambiguous"}


class ProposedFieldMeta(BaseModel):
    """Field-level extraction provenance for proposal arguments."""

    model_config = ConfigDict(extra="forbid")

    provenance: str = Field(default="llm_inferred", max_length=64)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("provenance", mode="before")
    @classmethod
    def normalize_provenance(cls, value: Any) -> str:
        return str(value or "").strip().lower().replace(" ", "_") or "llm_inferred"


class ProposedToolCall(BaseModel):
    """One proposed runtime tool invocation."""

    model_config = ConfigDict(extra="forbid")

    tool_name: Optional[str] = Field(default=None, max_length=128)
    capability: Optional[str] = Field(default=None, max_length=128)
    provider: Optional[str] = Field(default=None, max_length=64)
    args: Dict[str, Any] = Field(default_factory=dict)
    field_meta: Dict[str, ProposedFieldMeta] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: Optional[str] = Field(default=None, max_length=400)

    @field_validator("tool_name", mode="before")
    @classmethod
    def normalize_tool_name(cls, value: Any) -> Optional[str]:
        text = str(value or "").strip()
        return text or None

    @field_validator("capability", mode="before")
    @classmethod
    def normalize_capability(cls, value: Any) -> Optional[str]:
        text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
        return text or None

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: Any) -> Optional[str]:
        text = str(value or "").strip().lower()
        return text or None

    @field_validator("args", mode="before")
    @classmethod
    def normalize_args(cls, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        return {}

    @field_validator("field_meta", mode="before")
    @classmethod
    def normalize_field_meta(cls, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        normalized: Dict[str, Any] = {}
        for key, meta in value.items():
            field_name = str(key or "").strip()
            if not field_name:
                continue
            normalized[field_name] = meta
        return normalized

    @model_validator(mode="after")
    def validate_target(self) -> "ProposedToolCall":
        if self.capability:
            return self
        raise ValueError("capability is required")


class ToolProposal(BaseModel):
    """Actionability and proposed tool-call plan extracted from user text."""

    model_config = ConfigDict(extra="forbid")

    is_actionable: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    summary: str = Field(default="", max_length=800)
    scope_classification: str = Field(default="", max_length=64)
    policy_reason_code: str = Field(default="", max_length=120)
    needs_confirmation: bool = True
    missing_fields: List[str] = Field(default_factory=list)
    proposed_tool_calls: List[ProposedToolCall] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("missing_fields", mode="before")
    @classmethod
    def normalize_missing_fields(cls, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        out: List[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                out.append(text)
        return out[:10]

    @field_validator("scope_classification", mode="before")
    @classmethod
    def normalize_scope_classification(cls, value: Any) -> str:
        text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        return text

    @field_validator("policy_reason_code", mode="before")
    @classmethod
    def normalize_policy_reason_code(cls, value: Any) -> str:
        return str(value or "").strip().lower().replace(" ", "_")

    @model_validator(mode="after")
    def finalize_scope_fields(self) -> "ToolProposal":
        if self.scope_classification not in SCOPE_CLASSIFICATIONS:
            if self.is_actionable or self.proposed_tool_calls:
                self.scope_classification = "in_scope_action"
            else:
                self.scope_classification = "in_scope_info"
        if not self.policy_reason_code:
            if self.scope_classification == "out_of_scope":
                self.policy_reason_code = "proposal_marked_out_of_scope"
            elif self.scope_classification == "ambiguous":
                self.policy_reason_code = "proposal_scope_ambiguous"
            else:
                self.policy_reason_code = "proposal_scope_inferred"
        return self
