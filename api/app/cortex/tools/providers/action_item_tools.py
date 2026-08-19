"""Cortex Action Item toolset for project-scoped action item operations."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional, Type
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.action_items.owner_intent import parse_owner_intent
from app.action_items.service import ActionItemService
from app.core.config import settings
from app.core.database import get_brain_collection
from app.core.identity_resolver import (
    build_identity_followup_message,
    resolve_project_member_identity,
)
from app.cortex.tools.base import CortexTool, IdempotencyClass, MemberRole, ToolSpec
from app.cortex.tools.error_envelope import build_tool_error, build_validation_error
from app.cortex.tools.runtime_helpers import load_project, to_iso
from app.cortex.tools.schemas import OPERATION_ID_PATTERN, normalize_operation_id, normalize_text
from app.llm import LLMGatewayRequest, LLMMessage, LLMMessageRole, get_shared_llm_gateway

_TEMPORAL_TOKEN_RE = re.compile(
    r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next\s+\w+|this\s+\w+|\d{1,2}(?::\d{2})?\s*(?:am|pm))\b",
    re.IGNORECASE,
)
_CREATE_PREFIX_RE = re.compile(
    r"^\s*(?:(?:can|could)\s+you\s+|please\s+|pls\s+|kindly\s+)?"
    r"(?:create|add|make|open|log|raise)\s+"
    r"(?:(?:an?|one|new)\s+)?"
    r"(?:\w+\s+){0,2}?"
    r"action\s*item(?:s)?"
    r"(?:\s+for\s+me)?"
    r"(?:\s+(?:to|for))?\s+",
    re.IGNORECASE,
)
_ASSIGN_SUFFIX_RE = re.compile(
    r"\s+(?:and\s+)?assign(?:ed)?\s+to\s+.+$",
    re.IGNORECASE,
)
_OWNER_SUFFIX_RE = re.compile(
    r"\s+(?:and\s+)?(?:set\s+)?owner\s+(?:as|to|is)\s+.+$",
    re.IGNORECASE,
)
_ACTION_ITEM_CLOSED_SCOPE_RE = re.compile(
    r"\b(?:closed|done|completed|cancelled|canceled)\b.*\baction\s*items?\b|\baction\s*items?\b.*\b(?:closed|done|completed|cancelled|canceled)\b",
    re.IGNORECASE,
)
_ACTION_ITEM_ALL_STATUS_SCOPE_RE = re.compile(
    r"\b(?:all\s+statuses|all\s+status|including\s+closed|include\s+closed|including\s+cancelled|include\s+cancelled|including\s+done|include\s+done)\b",
    re.IGNORECASE,
)


class ActionItemCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, max_length=180)
    owner_user_id: Optional[str] = Field(default=None, max_length=120)
    related_to: Optional[str] = Field(default=None, max_length=220)
    related_type: str = Field(default="other", max_length=40)
    related_id: Optional[str] = Field(default=None, max_length=180)
    due_type: str = Field(default="no_due_date", max_length=40)
    due_date: Optional[datetime] = None
    source: str = Field(default="manual", max_length=40)
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)

    @field_validator("operation_id", mode="before")
    @classmethod
    def normalize_op_id(cls, value: str) -> str:
        return normalize_operation_id(str(value))

    @field_validator("title", "owner_user_id", "related_to", "related_id", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value))
        return text or None

    @field_validator("related_type", "due_type", "source", mode="before")
    @classmethod
    def normalize_enumish_text(cls, value: str) -> str:
        text = normalize_text(str(value)).lower()
        if not text:
            raise ValueError("value is required")
        return text


class ActionItemListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Optional[str] = Field(default=None, max_length=40)
    owner_user_id: Optional[str] = Field(default=None, max_length=120)
    due_type: Optional[str] = Field(default=None, max_length=40)
    source: Optional[str] = Field(default=None, max_length=40)
    needs_followup: Optional[bool] = None
    closed_window_hours: Optional[int] = Field(default=None, ge=1, le=168)
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

    @field_validator("status", "owner_user_id", "due_type", "source", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value))
        return text.lower() if text else None


class ActionItemUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_item_ref: str = Field(min_length=1, max_length=120)
    title: Optional[str] = Field(default=None, max_length=180)
    owner_user_id: Optional[str] = Field(default=None, max_length=120)
    related_to: Optional[str] = Field(default=None, max_length=220)
    related_type: Optional[str] = Field(default=None, max_length=40)
    related_id: Optional[str] = Field(default=None, max_length=180)
    status: Optional[str] = Field(default=None, max_length=40)
    due_type: Optional[str] = Field(default=None, max_length=40)
    due_date: Optional[datetime] = None
    needs_followup: Optional[bool] = None
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)

    @field_validator("operation_id", mode="before")
    @classmethod
    def normalize_op_id(cls, value: str) -> str:
        return normalize_operation_id(str(value))

    @field_validator(
        "action_item_ref",
        "title",
        "owner_user_id",
        "related_to",
        "related_id",
        mode="before",
    )
    @classmethod
    def normalize_free_text_fields(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value))
        return text or None

    @field_validator("related_type", "status", "due_type", mode="before")
    @classmethod
    def normalize_enumish_text_fields(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value)).lower()
        return text or None


class ActionItemCloseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_item_ref: str = Field(min_length=1, max_length=120)
    status: str = Field(default="done", max_length=20)
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)

    @field_validator("operation_id", mode="before")
    @classmethod
    def normalize_op_id(cls, value: str) -> str:
        return normalize_operation_id(str(value))

    @field_validator("action_item_ref", mode="before")
    @classmethod
    def normalize_ref(cls, value: str) -> str:
        text = normalize_text(str(value))
        if not text:
            raise ValueError("action_item_ref is required")
        return text

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        text = normalize_text(str(value)).lower()
        if text not in {"done", "cancelled"}:
            raise ValueError("status must be done or cancelled")
        return text


class _BaseActionItemTool(CortexTool):
    input_model: Type[BaseModel]

    async def execute(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return await self.execute_with_context(args, {})

    async def execute_with_context(
        self,
        args: Dict[str, Any],
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            parsed = self.input_model.model_validate(args or {})
        except ValidationError as exc:
            print(
                f"Cortex ActionItem validation error ({self.spec.name}): "
                f"errors={exc.errors()} args={args or {}}"
            )
            return build_validation_error(tool_name=self.spec.name, error=exc)

        if not bool(settings.action_items_enabled):
            return build_tool_error(
                error_code="feature_disabled",
                error_class="policy",
                retryable=False,
                http_status=403,
                user_message="Action items are currently disabled for this workspace.",
                details={"tool_name": self.spec.name},
            )

        try:
            return await self._execute_validated(
                parsed=parsed,
                execution_context=execution_context or {},
            )
        except Exception as exc:
            print(f"Cortex ActionItem tool execution failed ({self.spec.name}): {exc}")
            return build_tool_error(
                error_code="action_item_tool_execution_failed",
                error_class="integration",
                retryable=False,
                http_status=500,
                user_message=f"I couldn't complete `{self.spec.name}` right now.",
                details={"tool_name": self.spec.name, "reason": str(exc)},
            )

    async def _execute_validated(
        self,
        *,
        parsed: BaseModel,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return build_tool_error(
            error_code="tool_not_implemented",
            error_class="integration",
            retryable=False,
            http_status=501,
            user_message="This action is not available yet.",
            details={"tool_name": self.spec.name},
        )

    def _parse_context(self, execution_context: Dict[str, Any]) -> tuple[str, str]:
        project_id = str(execution_context.get("project_id") or "").strip()
        user_id = str(execution_context.get("user_id") or "").strip()
        return project_id, user_id

    def _serialize_action_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(item or {})
        for key in (
            "created_at",
            "updated_at",
            "due_date",
            "closed_at",
            "needs_followup_set_at",
        ):
            if key in payload:
                payload[key] = to_iso(payload.get(key))
        # Target datetime is internal-only inference context, not user-facing response data.
        payload.pop("target_datetime", None)
        return payload

    def _resolve_raw_user_text(self, execution_context: Dict[str, Any]) -> Optional[str]:
        for key in ("latest_user_message", "current_user_message", "user_message", "input_text", "raw_user_text"):
            value = str(execution_context.get(key) or "").strip()
            if value:
                return value
        return None

    def _resolve_list_status(self, *, provided_status: Optional[str], raw_text: Optional[str]) -> Optional[str]:
        normalized_status = normalize_text(str(provided_status or "")).lower()
        if normalized_status in {"all", "any", "*"}:
            return None
        if normalized_status == "closed":
            return "closed"
        if normalized_status:
            return normalized_status

        normalized_text = str(raw_text or "").strip().lower()
        if normalized_text:
            if _ACTION_ITEM_ALL_STATUS_SCOPE_RE.search(normalized_text):
                return None
            if _ACTION_ITEM_CLOSED_SCOPE_RE.search(normalized_text):
                return "closed"
        return "open"

    def _derive_title_from_raw_text(self, raw_text: Optional[str]) -> Optional[str]:
        text = str(raw_text or "").strip()
        if not text:
            return None
        derived = _CREATE_PREFIX_RE.sub("", text).strip()
        derived = _ASSIGN_SUFFIX_RE.sub("", derived).strip()
        derived = _OWNER_SUFFIX_RE.sub("", derived).strip()
        derived = re.sub(r"\s+", " ", derived).strip(" .")
        if not derived:
            return None
        return derived[:180]

    def _normalize_create_title(
        self,
        *,
        parsed_title: Optional[str],
        raw_text: Optional[str],
    ) -> Optional[str]:
        normalized_title = str(parsed_title or "").strip() or None
        derived_from_raw = self._derive_title_from_raw_text(raw_text)
        if not normalized_title:
            return derived_from_raw
        if derived_from_raw:
            lowered_title = normalized_title.lower()
            if _CREATE_PREFIX_RE.search(lowered_title) or _ASSIGN_SUFFIX_RE.search(lowered_title) or _OWNER_SUFFIX_RE.search(lowered_title):
                return derived_from_raw
        if self._title_needs_temporal_coverage_backfill(title=normalized_title, raw_text=raw_text):
            return derived_from_raw or normalized_title
        return normalized_title

    def _parse_owner_intent(
        self,
        *,
        raw_text: Optional[str],
        candidate_owner_hint: Optional[str],
    ) -> tuple[bool, Optional[str], str]:
        parsed = parse_owner_intent(str(raw_text or ""), candidate_owner_hint)
        normalized_hint = str(parsed.owner_hint or "").strip() or None
        return bool(parsed.intent_detected), normalized_hint, str(parsed.confidence or "none")

    def _title_needs_temporal_coverage_backfill(self, *, title: Optional[str], raw_text: Optional[str]) -> bool:
        normalized_title = str(title or "").strip().lower()
        normalized_raw = str(raw_text or "").strip().lower()
        if not normalized_raw:
            return False
        raw_tokens = [str(match.group(1) or "").strip().lower() for match in _TEMPORAL_TOKEN_RE.finditer(normalized_raw)]
        if not raw_tokens:
            return False
        if not normalized_title:
            return True
        return not any(token and token in normalized_title for token in raw_tokens)

    async def _try_llm_owner_hint_from_raw_text(
        self,
        *,
        raw_text: Optional[str],
        project: Dict[str, Any],
    ) -> tuple[Optional[str], str]:
        text = str(raw_text or "").strip()
        if not text:
            return None, "none"
        gateway = get_shared_llm_gateway()
        if gateway is None:
            return None, "none"

        member_labels: list[str] = []
        for member in project.get("members") or []:
            if not isinstance(member, dict):
                continue
            if str(member.get("status") or "").strip().lower() != "active":
                continue
            name = str(member.get("name") or "").strip()
            email = str(member.get("email") or "").strip()
            user_id = str(member.get("user_id") or "").strip()
            if name:
                member_labels.append(name)
            if email:
                member_labels.append(email)
            if user_id:
                member_labels.append(user_id)
        member_hints = ", ".join(member_labels[:30])
        system_prompt = (
            "Extract assignee intent for action-item creation.\n"
            "Return strict JSON only: "
            "{\"explicit_owner_intent\": boolean, \"owner_hint\": string, \"confidence\": \"high|medium|low\"}.\n"
            "Rules:\n"
            "- Set explicit_owner_intent=true only when the user is assigning ownership.\n"
            "- If user asks 'for <person>' in an action-item create request, treat as owner intent.\n"
            "- owner_hint should be the shortest identity phrase (name/email/user id).\n"
            "- If no owner intent, return explicit_owner_intent=false, owner_hint=\"\", confidence=\"low\".\n"
            "- Never return the message sender unless explicitly requested (me/myself/self).\n"
            f"- Candidate members: {member_hints or 'none'}"
        )
        request = LLMGatewayRequest(
            messages=[
                LLMMessage(role=LLMMessageRole.SYSTEM, content=system_prompt),
                LLMMessage(role=LLMMessageRole.USER, content=text),
            ],
            temperature=0.0,
            max_tokens=80,
            timeout_seconds=6,
            retries=0,
            metadata={"feature": "action_items_owner_hint_extract"},
        )
        try:
            result = await gateway.generate(request)
        except Exception:
            return None, "none"
        if not bool(getattr(result, "ok", False)):
            return None, "none"
        raw = str(getattr(result, "text", "") or "").strip()
        if not raw:
            return None, "none"
        json_match = re.search(r"\{[\s\S]*\}", raw)
        json_text = json_match.group(0) if json_match else raw
        try:
            import json

            payload = json.loads(json_text)
        except Exception:
            return None, "none"
        if not isinstance(payload, dict):
            return None, "none"
        if not bool(payload.get("explicit_owner_intent")):
            return None, "none"
        hint = str(payload.get("owner_hint") or "").strip()
        confidence = str(payload.get("confidence") or "").strip().lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        return (hint or None), confidence

    def _format_due_date_for_summary(self, *, due_date_value: Any, timezone_name: str) -> Optional[str]:
        raw = str(due_date_value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return raw
        if parsed.tzinfo is None:
            return parsed.strftime("%Y-%m-%d %H:%M UTC")
        try:
            local = parsed.astimezone(ZoneInfo(str(timezone_name or "UTC").strip() or "UTC"))
            return local.strftime("%Y-%m-%d %H:%M %Z")
        except Exception:
            return parsed.astimezone().strftime("%Y-%m-%d %H:%M %Z")

    def _resolve_owner_display_label(self, *, project: Dict[str, Any], owner_user_id: str) -> str:
        normalized_owner = str(owner_user_id or "").strip()
        if not normalized_owner:
            return "Unassigned"
        for member in project.get("members") or []:
            if not isinstance(member, dict):
                continue
            if str(member.get("user_id") or "").strip() != normalized_owner:
                continue
            name = str(member.get("name") or "").strip()
            if name:
                return name
            email = str(member.get("email") or "").strip()
            if email:
                return email
            break
        if str(project.get("user_id") or "").strip() == normalized_owner:
            owner_name = str(project.get("owner_name") or "").strip()
            if owner_name:
                return owner_name
        return normalized_owner

    def _build_create_summary(self, *, action_item: Dict[str, Any], project: Dict[str, Any], timezone_name: str) -> str:
        title = str(action_item.get("title") or "").strip() or "(untitled)"
        key = str(action_item.get("display_key") or action_item.get("action_item_id") or "").strip() or "N/A"
        status = str(action_item.get("status") or "open").strip().lower() or "open"
        owner = str(action_item.get("owner_user_id") or "").strip()
        owner_label = self._resolve_owner_display_label(project=project, owner_user_id=owner)
        due_type = str(action_item.get("due_type") or "").strip().lower()
        due_value = self._format_due_date_for_summary(
            due_date_value=action_item.get("due_date"),
            timezone_name=timezone_name,
        )
        lines = [
            "Action Item Created:",
            f"- Title: {title}",
            f"- Action Item ID: {key}",
            f"- Status: {status.capitalize()}",
        ]
        lines.append(f"- Assigned to: {owner_label}")
        if due_type == "by_date" and due_value:
            lines.append(f"- Due Date: {due_value}")
        elif due_type in {"today", "this_week"}:
            lines.append(f"- Due Type: {due_type}")
        if bool(action_item.get("needs_followup")):
            lines.append("")
            lines.append("Owner is unresolved. Who should this be assigned to?")
        return "\n".join(lines)

    async def _resolve_actor_user_id(
        self,
        *,
        project: Dict[str, Any],
        fallback_user_id: str,
        execution_context: Dict[str, Any],
    ) -> Optional[str]:
        normalized_user_id = str(fallback_user_id or "").strip()
        if normalized_user_id:
            if str(project.get("user_id") or "").strip() == normalized_user_id:
                return normalized_user_id
            for member in project.get("members") or []:
                if not isinstance(member, dict):
                    continue
                if str(member.get("status") or "").strip().lower() != "active":
                    continue
                if str(member.get("user_id") or "").strip() == normalized_user_id:
                    return normalized_user_id

        actor_email = str(
            (execution_context.get("actor_identity") or {}).get("actor_email")
            or execution_context.get("actor_email")
            or ""
        ).strip().lower()
        if actor_email:
            for member in project.get("members") or []:
                if not isinstance(member, dict):
                    continue
                if str(member.get("status") or "").strip().lower() != "active":
                    continue
                member_email = str(member.get("email") or "").strip().lower()
                member_user_id = str(member.get("user_id") or "").strip()
                if member_email and member_email == actor_email and member_user_id:
                    return member_user_id
        return None

    async def _resolve_action_item_id(
        self,
        *,
        project_id: str,
        action_item_ref: str,
    ) -> Optional[str]:
        ref = str(action_item_ref or "").strip()
        if not ref:
            return None
        if ref.lower().startswith("action-"):
            suffix = ref.split("-", 1)[1] if "-" in ref else ""
            collection = get_brain_collection(project_id)
            doc = await collection.find_one(
                {
                    "entity_type": "action_item",
                    "display_key": f"ACTION-{suffix}".upper(),
                },
                {"action_item_id": 1},
            )
            return str((doc or {}).get("action_item_id") or "").strip() or None
        return ref

    def _resolve_owner_user_id_or_error(
        self,
        *,
        project: Dict[str, Any],
        actor_user_id: str,
        owner_hint: Optional[str],
    ) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        normalized_hint = str(owner_hint or "").strip()
        if not normalized_hint:
            return None, None
        resolution = resolve_project_member_identity(
            project,
            normalized_hint,
            actor_user_id=actor_user_id,
            actor_aliases_enabled=True,
        )
        resolved_owner_user_id = str(resolution.promarshal_user_id or "").strip()
        if resolution.status == "resolved" and resolved_owner_user_id:
            return resolved_owner_user_id, None
        return None, build_tool_error(
            error_code="owner_resolution_failed",
            error_class="validation",
            retryable=False,
            http_status=400,
            user_message=build_identity_followup_message(
                resolution,
                subject="owner",
                project=project,
            ),
            details={
                "tool_name": self.spec.name,
                "owner_hint": normalized_hint,
                "resolution_status": resolution.status,
                "resolution_reason": resolution.reason,
            },
        )

    def _service_error(self, *, exc: Exception) -> Dict[str, Any]:
        if isinstance(exc, PermissionError):
            return build_tool_error(
                error_code="permission_denied",
                error_class="policy",
                retryable=False,
                http_status=403,
                user_message=str(exc),
                details={"tool_name": self.spec.name},
            )
        if isinstance(exc, ValueError):
            return build_tool_error(
                error_code="validation_error",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message=str(exc),
                details={"tool_name": self.spec.name},
            )
        return build_tool_error(
            error_code="action_item_service_error",
            error_class="integration",
            retryable=False,
            http_status=500,
            user_message="I couldn't process that action item request right now.",
            details={"tool_name": self.spec.name, "reason": str(exc)},
        )


class ActionItemCreateTool(_BaseActionItemTool):
    input_model = ActionItemCreateInput
    spec = ToolSpec(
        name="action_item_create",
        description="Create a project action item for follow-up tracking.",
        provider="promarshal",
        capabilities=["action_item_create"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=[],
        requires_confirmation=True,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: ActionItemCreateInput,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        project_id, user_id = self._parse_context(execution_context)
        project = await load_project(project_id)
        if not project:
            return build_tool_error(
                error_code="project_not_found",
                error_class="not_found",
                retryable=False,
                http_status=404,
                user_message="I couldn't find the active project for this action.",
                details={"project_id": project_id, "tool_name": self.spec.name},
            )
        actor_user_id = await self._resolve_actor_user_id(
            project=project,
            fallback_user_id=user_id,
            execution_context=execution_context,
        )
        if not actor_user_id:
            return build_tool_error(
                error_code="actor_not_project_member",
                error_class="validation",
                retryable=False,
                http_status=403,
                user_message="I couldn't map your identity to an active project member.",
                details={"project_id": project_id, "tool_name": self.spec.name},
            )
        raw_text = self._resolve_raw_user_text(execution_context)
        normalized_title = self._normalize_create_title(parsed_title=parsed.title, raw_text=raw_text)
        timezone_name = str(project.get("timezone") or "UTC").strip() or "UTC"
        explicit_owner_intent, owner_hint, owner_intent_confidence = self._parse_owner_intent(
            raw_text=raw_text,
            candidate_owner_hint=parsed.owner_user_id,
        )
        if explicit_owner_intent and (not owner_hint or owner_intent_confidence != "strong"):
            llm_owner_hint, llm_owner_confidence = await self._try_llm_owner_hint_from_raw_text(
                raw_text=raw_text,
                project=project,
            )
            if llm_owner_hint:
                owner_hint = llm_owner_hint
                owner_intent_confidence = llm_owner_confidence
        resolved_owner_user_id: Optional[str] = None
        owner_resolution_error: Optional[Dict[str, Any]] = None
        if explicit_owner_intent and owner_hint:
            resolved_owner_user_id, owner_resolution_error = self._resolve_owner_user_id_or_error(
                project=project,
                actor_user_id=actor_user_id,
                owner_hint=owner_hint,
            )
            if owner_resolution_error is not None and owner_intent_confidence in {"weak", "medium", "low"}:
                resolution_status = str(
                    ((owner_resolution_error.get("details") or {}).get("resolution_status")) or ""
                ).strip()
                if resolution_status == "not_found":
                    owner_resolution_error = None
        if owner_resolution_error is not None:
            return owner_resolution_error

        try:
            created = await ActionItemService.create(
                project_id,
                {
                    "user_id": actor_user_id,
                    "title": normalized_title,
                    "owner_user_id": resolved_owner_user_id,
                    "related_to": parsed.related_to,
                    "related_type": parsed.related_type,
                    "related_id": parsed.related_id,
                    "source": parsed.source,
                    "due_type": parsed.due_type,
                    "due_date": parsed.due_date,
                    "raw_text": raw_text,
                    "source_event_key": f"cortex:{self.spec.name}:{parsed.operation_id}",
                },
            )
        except Exception as exc:  # noqa: BLE001
            return self._service_error(exc=exc)
        serialized = self._serialize_action_item(created)
        summary = self._build_create_summary(
            action_item=serialized,
            project=project,
            timezone_name=timezone_name,
        )
        return {
            "ok": True,
            "tool_name": self.spec.name,
            "source": "brain",
            "action_item": serialized,
            "committed_action": {
                "summary": summary,
                "tool_name": self.spec.name,
                "action_item_id": serialized.get("action_item_id"),
            },
        }


class ActionItemListTool(_BaseActionItemTool):
    input_model = ActionItemListInput
    spec = ToolSpec(
        name="action_item_list",
        description="List project action items with filters.",
        provider="promarshal",
        capabilities=["action_item_list"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=[],
        requires_confirmation=False,
        idempotency=IdempotencyClass.READ_ONLY.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: ActionItemListInput,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        project_id, user_id = self._parse_context(execution_context)
        project = await load_project(project_id)
        if not project:
            return build_tool_error(
                error_code="project_not_found",
                error_class="not_found",
                retryable=False,
                http_status=404,
                user_message="I couldn't find the active project for this action.",
                details={"project_id": project_id, "tool_name": self.spec.name},
            )
        actor_user_id = await self._resolve_actor_user_id(
            project=project,
            fallback_user_id=user_id,
            execution_context=execution_context,
        )
        if not actor_user_id:
            return build_tool_error(
                error_code="actor_not_project_member",
                error_class="validation",
                retryable=False,
                http_status=403,
                user_message="I couldn't map your identity to an active project member.",
                details={"project_id": project_id, "tool_name": self.spec.name},
            )
        resolved_owner_user_id, owner_resolution_error = self._resolve_owner_user_id_or_error(
            project=project,
            actor_user_id=actor_user_id,
            owner_hint=parsed.owner_user_id,
        )
        if owner_resolution_error is not None:
            return owner_resolution_error

        raw_text = self._resolve_raw_user_text(execution_context)
        resolved_status = self._resolve_list_status(
            provided_status=parsed.status,
            raw_text=raw_text,
        )

        try:
            result = await ActionItemService.list(
                project_id,
                {
                    "user_id": actor_user_id,
                    "status": resolved_status,
                    "owner_user_id": resolved_owner_user_id,
                    "due_type": parsed.due_type,
                    "source": parsed.source,
                    "needs_followup": parsed.needs_followup,
                    "closed_window_hours": parsed.closed_window_hours,
                    "limit": parsed.limit,
                    "offset": parsed.offset,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return self._service_error(exc=exc)
        items: list[Dict[str, Any]] = []
        for item in result.get("items", []):
            serialized_item = self._serialize_action_item(item)
            owner_user_id = str(serialized_item.get("owner_user_id") or "").strip()
            serialized_item["owner_display_name"] = self._resolve_owner_display_label(
                project=project,
                owner_user_id=owner_user_id,
            )
            items.append(serialized_item)
        return {
            "ok": True,
            "tool_name": self.spec.name,
            "source": "brain",
            "items": items,
            "count": len(items),
            "total": int(result.get("total") or 0),
            "limit": int(result.get("limit") or parsed.limit),
            "offset": int(result.get("offset") or parsed.offset),
        }


class ActionItemUpdateTool(_BaseActionItemTool):
    input_model = ActionItemUpdateInput
    spec = ToolSpec(
        name="action_item_update",
        description="Update a project action item by id or ACTION-<n> display key.",
        provider="promarshal",
        capabilities=["action_item_update"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=[],
        requires_confirmation=False,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: ActionItemUpdateInput,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        project_id, user_id = self._parse_context(execution_context)
        project = await load_project(project_id)
        if not project:
            return build_tool_error(
                error_code="project_not_found",
                error_class="not_found",
                retryable=False,
                http_status=404,
                user_message="I couldn't find the active project for this action.",
                details={"project_id": project_id, "tool_name": self.spec.name},
            )
        actor_user_id = await self._resolve_actor_user_id(
            project=project,
            fallback_user_id=user_id,
            execution_context=execution_context,
        )
        if not actor_user_id:
            return build_tool_error(
                error_code="actor_not_project_member",
                error_class="validation",
                retryable=False,
                http_status=403,
                user_message="I couldn't map your identity to an active project member.",
                details={"project_id": project_id, "tool_name": self.spec.name},
            )
        resolved_owner_user_id, owner_resolution_error = self._resolve_owner_user_id_or_error(
            project=project,
            actor_user_id=actor_user_id,
            owner_hint=parsed.owner_user_id,
        )
        if owner_resolution_error is not None:
            return owner_resolution_error

        action_item_id = await self._resolve_action_item_id(
            project_id=project_id,
            action_item_ref=parsed.action_item_ref,
        )
        if not action_item_id:
            return build_tool_error(
                error_code="action_item_not_found",
                error_class="not_found",
                retryable=False,
                http_status=404,
                user_message="I couldn't find that action item.",
                details={"action_item_ref": parsed.action_item_ref, "tool_name": self.spec.name},
            )

        try:
            updated = await ActionItemService.update(
                project_id,
                action_item_id,
                {
                    "user_id": actor_user_id,
                    "title": parsed.title,
                    "owner_user_id": resolved_owner_user_id,
                    "related_to": parsed.related_to,
                    "related_type": parsed.related_type,
                    "related_id": parsed.related_id,
                    "status": parsed.status,
                    "due_type": parsed.due_type,
                    "due_date": parsed.due_date,
                    "needs_followup": parsed.needs_followup,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return self._service_error(exc=exc)
        serialized = self._serialize_action_item(updated)
        summary = f"Updated action item {serialized.get('display_key') or serialized.get('action_item_id')}"
        return {
            "ok": True,
            "tool_name": self.spec.name,
            "source": "brain",
            "action_item": serialized,
            "committed_action": {
                "summary": summary,
                "tool_name": self.spec.name,
                "action_item_id": serialized.get("action_item_id"),
            },
        }


class ActionItemCloseTool(_BaseActionItemTool):
    input_model = ActionItemCloseInput
    spec = ToolSpec(
        name="action_item_close",
        description="Close or cancel a project action item by id or display key.",
        provider="promarshal",
        capabilities=["action_item_close"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=[],
        requires_confirmation=False,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: ActionItemCloseInput,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        project_id, user_id = self._parse_context(execution_context)
        project = await load_project(project_id)
        if not project:
            return build_tool_error(
                error_code="project_not_found",
                error_class="not_found",
                retryable=False,
                http_status=404,
                user_message="I couldn't find the active project for this action.",
                details={"project_id": project_id, "tool_name": self.spec.name},
            )
        actor_user_id = await self._resolve_actor_user_id(
            project=project,
            fallback_user_id=user_id,
            execution_context=execution_context,
        )
        if not actor_user_id:
            return build_tool_error(
                error_code="actor_not_project_member",
                error_class="validation",
                retryable=False,
                http_status=403,
                user_message="I couldn't map your identity to an active project member.",
                details={"project_id": project_id, "tool_name": self.spec.name},
            )
        action_item_id = await self._resolve_action_item_id(
            project_id=project_id,
            action_item_ref=parsed.action_item_ref,
        )
        if not action_item_id:
            return build_tool_error(
                error_code="action_item_not_found",
                error_class="not_found",
                retryable=False,
                http_status=404,
                user_message="I couldn't find that action item.",
                details={"action_item_ref": parsed.action_item_ref, "tool_name": self.spec.name},
            )

        try:
            closed = await ActionItemService.close(
                project_id,
                action_item_id,
                {"user_id": actor_user_id, "status": parsed.status},
            )
        except Exception as exc:  # noqa: BLE001
            return self._service_error(exc=exc)
        serialized = self._serialize_action_item(closed)
        summary = (
            f"Marked action item {serialized.get('display_key') or serialized.get('action_item_id')} "
            f"as {parsed.status}"
        )
        return {
            "ok": True,
            "tool_name": self.spec.name,
            "source": "brain",
            "action_item": serialized,
            "committed_action": {
                "summary": summary,
                "tool_name": self.spec.name,
                "action_item_id": serialized.get("action_item_id"),
            },
        }


def get_launch_action_item_tools() -> list[CortexTool]:
    return [
        ActionItemCreateTool(),
        ActionItemListTool(),
        ActionItemUpdateTool(),
        ActionItemCloseTool(),
    ]
