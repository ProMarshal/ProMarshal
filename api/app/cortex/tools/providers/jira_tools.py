"""Cortex launch Jira toolset with policy-aware runtime execution."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from difflib import SequenceMatcher
from enum import Enum
import random
import re
from typing import Any, Dict, List, Optional, Type

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.action_items.temporal_parser import parse_temporal_hints
from app.core.config import settings
from app.core.database import get_brain_collection
from app.cortex.domain.tasks.query_intent import (
    ASSIGNEE_MODE_ALIASES,
    DueDateMode,
    LEGACY_SELF_ASSIGNEE_ALIASES,
    LEGACY_UNASSIGNED_ASSIGNEE_ALIASES,
    STATUS_ALIASES,
    AssigneeMode,
    SearchStatus,
    is_task_read_self_scope_request,
)
from app.cortex.tools.base import CortexTool, IdempotencyClass, MemberRole, ToolSpec
from app.cortex.tools.error_envelope import (
    build_tool_error,
    build_validation_error,
)
from app.cortex.tools.runtime_helpers import (
    load_project,
    resolve_actor_email,
    resolve_actor_member,
    resolve_self_alias_to_actor_email,
    serialize_task_like,
)
from app.cortex.tools.providers.jira_helpers import (
    AssigneeResolutionResult,
    resolve_assignee_account_id,
    resolve_assignee_identity,
)
from app.cortex.tools.schemas import (
    EXTERNAL_ID_PATTERN,
    OPERATION_ID_PATTERN,
    DateRange,
    Priority,
    normalize_external_id,
    normalize_operation_id,
    normalize_text,
    validate_bounded_keyword,
)
from app.integrations.jira.normalizer import JiraNormalizer
from app.integrations.jira.oauth import jira_oauth_service
from app.llm import LLMGatewayRequest, LLMMessage, LLMMessageRole, get_shared_llm_gateway
from app.projects.recommendations.recommendation_orchestrator import mark_project_task_dirty
from app.tasks.pending_parent_handler import queue_work_item_parent_clarification
from app.tasks.parent_reference_validator import (
    build_parent_reference_not_available_message,
    resolve_parent_reference,
)
from app.tasks.provider_parent_rules import resolve_parent_rule_filter
from app.tasks.comment_composer import compose_comment_text
from app.tasks.providers.jira_adapter import JiraAdapter
from app.tasks.schemas import CreateTaskDTO, ListTasksFilters, SprintDirective, UpdateTaskDTO
from app.tasks.service import TaskService


class FreshnessMode(str, Enum):
    """Read freshness mode for Jira read tools."""

    DEFAULT = "default"
    LATEST = "latest"


SPRINT_DIRECTIVE_ALIASES = {
    "auto": SprintDirective.AUTO,
    "default": SprintDirective.AUTO,
    "active_sprint": SprintDirective.ACTIVE_SPRINT,
    "active sprint": SprintDirective.ACTIVE_SPRINT,
    "current_sprint": SprintDirective.ACTIVE_SPRINT,
    "current sprint": SprintDirective.ACTIVE_SPRINT,
    "sprint": SprintDirective.ACTIVE_SPRINT,
    "backlog": SprintDirective.BACKLOG,
    "keep_backlog": SprintDirective.BACKLOG,
    "keep backlog": SprintDirective.BACKLOG,
}
SPRINT_BACKLOG_HINT_PATTERN = re.compile(
    r"\b(?:keep|leave|stay)\b.{0,24}\bbacklog\b|\bkeep\s+in\s+backlog\b",
    re.IGNORECASE,
)
SPRINT_ACTIVE_HINT_PATTERN = re.compile(
    r"\b(?:add|move|put)\b.{0,24}\b(?:active|current)?\s*sprint\b|\bin\s+(?:the\s+)?(?:active|current)\s+sprint\b",
    re.IGNORECASE,
)
from app.cortex.response import CortexResponseService, ResponsePayload

_JIRA_PROVIDER_TYPE_ALIASES = {
    "task": "Task",
    "tasks": "Task",
    "story": "Story",
    "stories": "Story",
    "user story": "Story",
    "user stories": "Story",
    "epic": "Epic",
    "epics": "Epic",
    "bug": "Bug",
    "bugs": "Bug",
    "defect": "Bug",
    "defects": "Bug",
    "subtask": "Sub-task",
    "subtasks": "Sub-task",
    "sub-task": "Sub-task",
    "sub-tasks": "Sub-task",
    "sub task": "Sub-task",
    "sub tasks": "Sub-task",
}

_DEFAULT_PROVIDER_TYPE_BY_WORK_ITEM_TYPE = {
    "portfolio": "Epic",
    "standard": "Task",
    "defect": "Bug",
    "subtask": "Sub-task",
    "service": "Task",
    "custom": "Task",
}

_PARENT_PROGRESS_MESSAGE_TEMPLATES = [
    "This {label} needs a parent. Let me find the top options for you.",
    "Parent required for this {label}. Let me get the best matches.",
    "Need a parent for this {label}. I'm checking project context now.",
]


def _normalize_provider_type_label(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = normalize_text(str(value)).lower().replace("-", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return None
    mapped = _JIRA_PROVIDER_TYPE_ALIASES.get(normalized)
    if mapped:
        return mapped
    return str(value).strip() or None


def _canonical_provider_type_token(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


class JiraCreateTaskInput(BaseModel):
    """Validated input for jira_create_work_item."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    assignee: Optional[str] = Field(
        default=None,
        max_length=320,
        description="Optional assignee email or display name. If omitted, create work item unassigned.",
    )
    priority: Priority = Priority.MEDIUM
    provider_type: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Optional Jira issue type (for example: Story, Epic, Bug, Sub-task).",
    )
    work_item_type: Optional[str] = Field(
        default=None,
        max_length=32,
        description=(
            "Optional canonical type hint (portfolio|standard|defect|subtask|service|custom). "
            "Used only when provider_type is omitted."
        ),
    )
    parent_external_id: Optional[str] = Field(
        default=None,
        pattern=EXTERNAL_ID_PATTERN,
        description="Optional parent issue key (required by Jira for Sub-task create).",
    )
    sprint_directive: Optional[SprintDirective] = Field(
        default=None,
        description="Optional sprint placement directive: auto | active_sprint | backlog.",
    )
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        if value is None:
            raise ValueError("title is required")
        text = normalize_text(str(value))
        if not text or text.strip().lower() in {"none", "null"}:
            raise ValueError("title is required")
        return text

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value))
        return text or None

    @field_validator("assignee", mode="before")
    @classmethod
    def normalize_assignee(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value))
        return text or None

    @field_validator("provider_type", mode="before")
    @classmethod
    def normalize_provider_type(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_provider_type_label(value)

    @field_validator("work_item_type", mode="before")
    @classmethod
    def normalize_work_item_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = normalize_text(str(value)).lower().replace("-", "_").strip()
        if not normalized:
            return None
        if normalized not in {
            "portfolio",
            "standard",
            "defect",
            "subtask",
            "service",
            "custom",
        }:
            raise ValueError(
                "work_item_type must be one of: portfolio, standard, defect, subtask, service, custom"
            )
        return normalized

    @field_validator("parent_external_id", mode="before")
    @classmethod
    def normalize_parent_external_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_external_id(str(value))
        return text or None

    @field_validator("operation_id", mode="before")
    @classmethod
    def normalize_op_id(cls, value: str) -> str:
        return normalize_operation_id(str(value))

    @field_validator("sprint_directive", mode="before")
    @classmethod
    def normalize_sprint_directive(cls, value: Optional[Any]) -> Optional[SprintDirective]:
        if value is None:
            return None
        if isinstance(value, SprintDirective):
            return value
        normalized = normalize_text(str(value)).lower().replace("-", " ")
        if not normalized:
            return None
        mapped = SPRINT_DIRECTIVE_ALIASES.get(normalized)
        if mapped is None:
            raise ValueError("sprint_directive must be one of: auto, active_sprint, backlog")
        return mapped

    @model_validator(mode="after")
    def hydrate_provider_type_from_work_item_type(self) -> "JiraCreateTaskInput":
        if not self.provider_type and self.work_item_type:
            self.provider_type = _DEFAULT_PROVIDER_TYPE_BY_WORK_ITEM_TYPE.get(
                str(self.work_item_type).strip().lower(),
                "Task",
            )
        title_value = normalize_text(str(self.title or ""))
        if title_value.lower().startswith("for "):
            stripped_title = title_value[4:].strip(" ,.;:-")
            if stripped_title:
                self.title = stripped_title
        return self


class JiraUpdateStatusInput(BaseModel):
    """Validated input for jira_update_status."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(pattern=EXTERNAL_ID_PATTERN)
    status: str = Field(min_length=1, max_length=120)
    sprint_directive: Optional[SprintDirective] = Field(
        default=None,
        description="Optional sprint placement directive: auto | active_sprint | backlog.",
    )
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)

    @field_validator("external_id", mode="before")
    @classmethod
    def normalize_external_id(cls, value: str) -> str:
        return normalize_external_id(str(value))

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        text = normalize_text(str(value))
        if not text:
            raise ValueError("status is required")
        normalized = text.lower().replace("-", "_").replace(" ", "_")
        mapped = STATUS_ALIASES.get(normalized)
        return str(mapped or text).strip()

    @field_validator("operation_id", mode="before")
    @classmethod
    def normalize_op_id(cls, value: str) -> str:
        return normalize_operation_id(str(value))

    @field_validator("sprint_directive", mode="before")
    @classmethod
    def normalize_sprint_directive(cls, value: Optional[Any]) -> Optional[SprintDirective]:
        if value is None:
            return None
        if isinstance(value, SprintDirective):
            return value
        normalized = normalize_text(str(value)).lower().replace("-", " ")
        if not normalized:
            return None
        mapped = SPRINT_DIRECTIVE_ALIASES.get(normalized)
        if mapped is None:
            raise ValueError("sprint_directive must be one of: auto, active_sprint, backlog")
        return mapped


class JiraAddCommentInput(BaseModel):
    """Validated input for jira_add_comment."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(pattern=EXTERNAL_ID_PATTERN)
    comment: str = Field(min_length=1, max_length=2000)
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)

    @field_validator("external_id", mode="before")
    @classmethod
    def normalize_external_id(cls, value: str) -> str:
        return normalize_external_id(str(value))

    @field_validator("comment", mode="before")
    @classmethod
    def normalize_comment(cls, value: str) -> str:
        text = normalize_text(str(value))
        if not text:
            raise ValueError("comment is required")
        return text

    @field_validator("operation_id", mode="before")
    @classmethod
    def normalize_op_id(cls, value: str) -> str:
        return normalize_operation_id(str(value))


class JiraUpdateDescriptionInput(BaseModel):
    """Validated input for jira_update_description."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(pattern=EXTERNAL_ID_PATTERN)
    description: str = Field(min_length=1, max_length=4000)
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)

    @field_validator("external_id", mode="before")
    @classmethod
    def normalize_external_id(cls, value: str) -> str:
        return normalize_external_id(str(value))

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        text = normalize_text(str(value))
        if not text:
            raise ValueError("description is required")
        return text

    @field_validator("operation_id", mode="before")
    @classmethod
    def normalize_op_id(cls, value: str) -> str:
        return normalize_operation_id(str(value))


class JiraAssignTaskInput(BaseModel):
    """Validated input for jira_assign_work_item."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(pattern=EXTERNAL_ID_PATTERN)
    assignee: str = Field(min_length=1, max_length=320)
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)

    @field_validator("external_id", mode="before")
    @classmethod
    def normalize_external_id(cls, value: str) -> str:
        return normalize_external_id(str(value))

    @field_validator("assignee", mode="before")
    @classmethod
    def normalize_assignee(cls, value: str) -> str:
        text = normalize_text(str(value))
        if not text:
            raise ValueError("assignee is required")
        return text

    @field_validator("operation_id", mode="before")
    @classmethod
    def normalize_op_id(cls, value: str) -> str:
        return normalize_operation_id(str(value))


class JiraUpdateParentInput(BaseModel):
    """Validated input for jira_update_parent_work_item."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(pattern=EXTERNAL_ID_PATTERN)
    parent_external_id: str = Field(pattern=EXTERNAL_ID_PATTERN)
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)

    @field_validator("external_id", mode="before")
    @classmethod
    def normalize_external_id(cls, value: str) -> str:
        return normalize_external_id(str(value))

    @field_validator("parent_external_id", mode="before")
    @classmethod
    def normalize_parent_external_id(cls, value: str) -> str:
        return normalize_external_id(str(value))

    @field_validator("operation_id", mode="before")
    @classmethod
    def normalize_op_id(cls, value: str) -> str:
        return normalize_operation_id(str(value))


class JiraUpdateDueDateInput(BaseModel):
    """Validated input for jira_update_due_date."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(pattern=EXTERNAL_ID_PATTERN)
    due_date: Any = Field(
        description=(
            "Due date input. Supports ISO date/datetime values or natural language "
            "phrases like 'today', 'tomorrow', and 'next monday'."
        )
    )
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)

    @field_validator("external_id", mode="before")
    @classmethod
    def normalize_external_id(cls, value: str) -> str:
        return normalize_external_id(str(value))

    @field_validator("operation_id", mode="before")
    @classmethod
    def normalize_op_id(cls, value: str) -> str:
        return normalize_operation_id(str(value))


class JiraClearDueDateInput(BaseModel):
    """Validated input for jira_clear_due_date."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(pattern=EXTERNAL_ID_PATTERN)
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)

    @field_validator("external_id", mode="before")
    @classmethod
    def normalize_external_id(cls, value: str) -> str:
        return normalize_external_id(str(value))

    @field_validator("operation_id", mode="before")
    @classmethod
    def normalize_op_id(cls, value: str) -> str:
        return normalize_operation_id(str(value))


class JiraUpdateStartDateInput(BaseModel):
    """Validated input for jira_update_start_date."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(pattern=EXTERNAL_ID_PATTERN)
    start_date: Any = Field(
        description=(
            "Start date input. Supports ISO date/datetime values or natural language "
            "phrases like 'today', 'tomorrow', and 'next monday'."
        )
    )
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)

    @field_validator("external_id", mode="before")
    @classmethod
    def normalize_external_id(cls, value: str) -> str:
        return normalize_external_id(str(value))

    @field_validator("operation_id", mode="before")
    @classmethod
    def normalize_op_id(cls, value: str) -> str:
        return normalize_operation_id(str(value))


class JiraClearStartDateInput(BaseModel):
    """Validated input for jira_clear_start_date."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(pattern=EXTERNAL_ID_PATTERN)
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)

    @field_validator("external_id", mode="before")
    @classmethod
    def normalize_external_id(cls, value: str) -> str:
        return normalize_external_id(str(value))

    @field_validator("operation_id", mode="before")
    @classmethod
    def normalize_op_id(cls, value: str) -> str:
        return normalize_operation_id(str(value))


class JiraAnalyzeTasksInput(BaseModel):
    """Validated input for jira_analyze_work_items."""

    model_config = ConfigDict(extra="forbid")

    assignee_mode: Optional[AssigneeMode] = Field(
        default=None,
        description="Optional assignee filtering mode: any | specific | unassigned | me.",
    )
    assignee_value: Optional[str] = Field(
        default=None,
        max_length=320,
        description="Required when assignee_mode is specific. Prefer member email.",
    )
    assignee: Optional[str] = Field(
        default=None,
        max_length=320,
        description="Legacy fallback. Prefer assignee_mode and assignee_value.",
    )
    include_closed: bool = Field(
        default=False,
        description="Include closed/done work items in the analysis. Default False - open work items only.",
    )
    provider_types: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional provider-native issue types to filter by "
            "(for example: Task, Story, Epic, Bug, Sub-task)."
        ),
    )
    stale_days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="Days without an update before a work item is considered stalled. Default 7.",
    )
    at_risk_in_progress_threshold: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "In-progress task count at or above which an assignee is marked at risk "
            "for capacity overload. Default 3."
        ),
    )
    include_unassigned_in_capacity: bool = Field(
        default=False,
        description=(
            "Include unassigned tasks in capacity scoring. Default False: unassigned "
            "work is reported as allocation pressure only."
        ),
    )
    due_date_mode: DueDateMode = Field(
        default=DueDateMode.ANY,
        description="Due-date filter mode: any | missing | present.",
    )

    @field_validator("assignee_mode", mode="before")
    @classmethod
    def normalize_assignee_mode(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value)).lower().replace(" ", "_").replace("-", "_")
        if not text:
            return None
        return ASSIGNEE_MODE_ALIASES.get(text, text)

    @field_validator("assignee_value", mode="before")
    @classmethod
    def normalize_assignee_value(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value)).lower()
        return text or None

    @field_validator("assignee", mode="before")
    @classmethod
    def normalize_assignee(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value)).lower()
        return text or None

    @field_validator("provider_types", mode="before")
    @classmethod
    def normalize_provider_types(cls, value: Optional[Any]) -> Optional[List[str]]:
        if value is None:
            return None
        raw_items: List[Any]
        if isinstance(value, str):
            text = normalize_text(value)
            if not text:
                return None
            raw_items = [part.strip() for part in re.split(r"[,\|/]", text) if part.strip()]
        elif isinstance(value, list):
            raw_items = list(value)
        else:
            raw_items = [value]

        normalized: List[str] = []
        for item in raw_items:
            label = _normalize_provider_type_label(None if item is None else str(item))
            if not label:
                continue
            if label not in normalized:
                normalized.append(label)
        return normalized or None

    @field_validator("due_date_mode", mode="before")
    @classmethod
    def normalize_due_date_mode(cls, value: Optional[Any]) -> Optional[str]:
        if value is None:
            return DueDateMode.ANY.value
        text = normalize_text(str(value)).lower().replace("-", "_").replace(" ", "_")
        if not text:
            return DueDateMode.ANY.value
        aliases = {
            "any": DueDateMode.ANY.value,
            "missing": DueDateMode.MISSING.value,
            "no_due_date": DueDateMode.MISSING.value,
            "without_due_date": DueDateMode.MISSING.value,
            "present": DueDateMode.PRESENT.value,
            "with_due_date": DueDateMode.PRESENT.value,
        }
        return aliases.get(text, text)

    @model_validator(mode="after")
    def finalize_assignee_filter(self) -> "JiraAnalyzeTasksInput":
        mode = self.assignee_mode
        mode_value = str(mode.value if hasattr(mode, "value") else mode or "").strip().lower()
        assignee_value = self.assignee_value
        legacy_assignee = self.assignee

        if not mode_value:
            if legacy_assignee:
                if legacy_assignee in LEGACY_UNASSIGNED_ASSIGNEE_ALIASES:
                    self.assignee_mode = AssigneeMode.UNASSIGNED
                    self.assignee_value = None
                elif legacy_assignee in LEGACY_SELF_ASSIGNEE_ALIASES:
                    self.assignee_mode = AssigneeMode.ME
                    self.assignee_value = None
                else:
                    self.assignee_mode = AssigneeMode.SPECIFIC
                    self.assignee_value = legacy_assignee
            else:
                self.assignee_mode = AssigneeMode.ANY
                self.assignee_value = None
            return self

        if mode_value == AssigneeMode.SPECIFIC.value:
            self.assignee_mode = AssigneeMode.SPECIFIC
            if not assignee_value:
                if legacy_assignee:
                    self.assignee_value = legacy_assignee
                else:
                    raise ValueError(
                        "assignee_value is required when assignee_mode is 'specific'"
                    )
        else:
            if mode_value in {
                AssigneeMode.ANY.value,
                AssigneeMode.UNASSIGNED.value,
                AssigneeMode.ME.value,
            }:
                self.assignee_mode = AssigneeMode(mode_value)
            self.assignee_value = None
        return self

class JiraSearchTasksInput(BaseModel):
    """Validated input for jira_search_work_items."""

    model_config = ConfigDict(extra="forbid")

    assignee_mode: Optional[AssigneeMode] = Field(
        default=None,
        description="Assignee filtering mode: any | specific | unassigned | me.",
    )
    assignee_value: Optional[str] = Field(
        default=None,
        max_length=320,
        description="Required only when assignee_mode is specific. Prefer member email.",
    )
    assignee: Optional[str] = Field(
        default=None,
        max_length=320,
        description="Legacy fallback. Prefer assignee_mode and assignee_value.",
    )
    include_unassigned: Optional[bool] = Field(
        default=None,
        description=(
            "When assignee_mode=any, include unassigned tasks if true. "
            "If false, return assigned tasks only."
        ),
    )
    status: Optional[SearchStatus] = None
    provider_types: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional provider-native issue types to filter by "
            "(for example: Task, Story, Epic, Bug, Sub-task)."
        ),
    )
    priority: Optional[Priority] = None
    keyword: Optional[str] = Field(default=None, max_length=200)
    date_range: Optional[DateRange] = None
    max_results: int = Field(default=20, ge=1, le=50)
    freshness: FreshnessMode = FreshnessMode.DEFAULT
    freshness_reason: Optional[str] = Field(default=None, max_length=200)
    due_date_mode: DueDateMode = Field(
        default=DueDateMode.ANY,
        description="Due-date filter mode: any | missing | present.",
    )

    @field_validator("keyword", mode="before")
    @classmethod
    def normalize_keyword(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        keyword = validate_bounded_keyword(str(value))
        return keyword or None

    @field_validator("assignee_mode", mode="before")
    @classmethod
    def normalize_assignee_mode(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value)).lower().replace(" ", "_").replace("-", "_")
        if not text:
            return None
        return ASSIGNEE_MODE_ALIASES.get(text, text)

    @field_validator("assignee_value", mode="before")
    @classmethod
    def normalize_assignee_value(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value)).lower()
        return text or None

    @field_validator("assignee", mode="before")
    @classmethod
    def normalize_assignee(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value)).lower()
        return text or None

    @field_validator("include_unassigned", mode="before")
    @classmethod
    def normalize_include_unassigned(cls, value: Optional[Any]) -> Optional[bool]:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        text = normalize_text(str(value)).lower()
        if not text:
            return None
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return None

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value)).lower().replace(" ", "_")
        return STATUS_ALIASES.get(text, text)

    @field_validator("provider_types", mode="before")
    @classmethod
    def normalize_provider_types(cls, value: Optional[Any]) -> Optional[List[str]]:
        if value is None:
            return None
        raw_items: List[Any]
        if isinstance(value, str):
            text = normalize_text(value)
            if not text:
                return None
            raw_items = [part.strip() for part in re.split(r"[,\|/]", text) if part.strip()]
        elif isinstance(value, list):
            raw_items = list(value)
        else:
            raw_items = [value]

        normalized: List[str] = []
        for item in raw_items:
            label = _normalize_provider_type_label(None if item is None else str(item))
            if not label:
                continue
            if label not in normalized:
                normalized.append(label)
        return normalized or None

    @field_validator("freshness_reason", mode="before")
    @classmethod
    def normalize_freshness_reason(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value))
        return text or None

    @field_validator("due_date_mode", mode="before")
    @classmethod
    def normalize_due_date_mode(cls, value: Optional[Any]) -> Optional[str]:
        if value is None:
            return DueDateMode.ANY.value
        text = normalize_text(str(value)).lower().replace("-", "_").replace(" ", "_")
        if not text:
            return DueDateMode.ANY.value
        aliases = {
            "any": DueDateMode.ANY.value,
            "missing": DueDateMode.MISSING.value,
            "no_due_date": DueDateMode.MISSING.value,
            "without_due_date": DueDateMode.MISSING.value,
            "present": DueDateMode.PRESENT.value,
            "with_due_date": DueDateMode.PRESENT.value,
        }
        return aliases.get(text, text)

    @model_validator(mode="after")
    def finalize_assignee_filter(self) -> "JiraSearchTasksInput":
        mode = self.assignee_mode
        mode_value = str(mode.value if hasattr(mode, "value") else mode or "").strip().lower()
        assignee_value = self.assignee_value
        legacy_assignee = self.assignee

        if not mode_value:
            if legacy_assignee:
                if legacy_assignee in LEGACY_UNASSIGNED_ASSIGNEE_ALIASES:
                    self.assignee_mode = AssigneeMode.UNASSIGNED
                    self.assignee_value = None
                elif legacy_assignee in LEGACY_SELF_ASSIGNEE_ALIASES:
                    self.assignee_mode = AssigneeMode.ME
                    self.assignee_value = None
                else:
                    self.assignee_mode = AssigneeMode.SPECIFIC
                    self.assignee_value = legacy_assignee
                print(
                    "Cortex jira_search_work_items accepted legacy `assignee`; "
                    "prefer `assignee_mode` + `assignee_value`."
                )
            else:
                self.assignee_mode = AssigneeMode.ANY
                self.assignee_value = None
            return self

        if legacy_assignee:
            print(
                "Cortex jira_search_work_items ignored legacy `assignee` because "
                "`assignee_mode` was provided."
            )

        if mode_value == AssigneeMode.SPECIFIC.value:
            self.assignee_mode = AssigneeMode.SPECIFIC
            if not assignee_value:
                if legacy_assignee:
                    self.assignee_value = legacy_assignee
                else:
                    raise ValueError(
                        "assignee_value is required when assignee_mode is 'specific'"
                    )
        else:
            if assignee_value:
                print(
                    f"Cortex jira_search_work_items ignored assignee_value for "
                    f"assignee_mode={mode_value}."
                )
            if mode_value in {
                AssigneeMode.ANY.value,
                AssigneeMode.UNASSIGNED.value,
                AssigneeMode.ME.value,
            }:
                self.assignee_mode = AssigneeMode(mode_value)
            self.assignee_value = None
        return self


class JiraGetTaskDetailsInput(BaseModel):
    """Validated input for jira_get_work_item_details."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(pattern=EXTERNAL_ID_PATTERN)
    freshness: FreshnessMode = FreshnessMode.DEFAULT
    freshness_reason: Optional[str] = Field(default=None, max_length=200)

    @field_validator("external_id", mode="before")
    @classmethod
    def normalize_external_id(cls, value: str) -> str:
        return normalize_external_id(str(value))

    @field_validator("freshness_reason", mode="before")
    @classmethod
    def normalize_freshness_reason(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value))
        return text or None


class _BaseJiraTool(CortexTool):
    """Base Jira tool with schema validation + integration error envelope."""

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
                f"Cortex Jira validation error ({self.spec.name}): "
                f"errors={exc.errors()} args={args or {}}"
            )
            return build_validation_error(tool_name=self.spec.name, error=exc)

        try:
            return await self._execute_validated(
                parsed=parsed,
                execution_context=execution_context or {},
            )
        except Exception as exc:
            print(f"Cortex Jira tool execution failed ({self.spec.name}): {exc}")
            return build_tool_error(
                error_code="jira_tool_execution_failed",
                error_class="integration",
                retryable=False,
                http_status=500,
                user_message="I couldn't complete that Jira action right now.",
                details={
                    "tool_name": self.spec.name,
                    "reason": str(exc),
                },
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
            user_message="This Jira action is not available yet.",
            details={
                "tool_name": self.spec.name,
            },
        )

    def _freshness_demanded(self, *, freshness: FreshnessMode, freshness_reason: Optional[str]) -> bool:
        return freshness == FreshnessMode.LATEST or bool((freshness_reason or "").strip())

    def _resolved_read_source_policy(self, execution_context: Dict[str, Any]) -> Dict[str, str]:
        quality_map = execution_context.get("quality_read_source")
        if not isinstance(quality_map, dict):
            return {}
        raw = quality_map.get(self.spec.name)
        if not isinstance(raw, dict):
            return {}
        return {
            "requested_mode": str(raw.get("requested_mode") or "").strip().lower(),
            "effective_mode": str(raw.get("effective_mode") or "").strip().lower(),
            "source": str(raw.get("source") or "").strip().lower(),
            "reason": str(raw.get("reason") or "").strip(),
        }

    def _build_read_source_unavailable_error(
        self,
        *,
        mode: str,
        reason: str,
    ) -> Dict[str, Any]:
        return build_tool_error(
            error_code="read_source_unavailable",
            error_class="integration",
            retryable=False,
            http_status=503,
            user_message=(
                f"I couldn't read `{self.spec.name}` using the required source mode `{mode}`."
            ),
            details={
                "tool_name": self.spec.name,
                "mode": mode,
                "reason": reason,
            },
        )

    def _parse_context(self, execution_context: Dict[str, Any]) -> tuple[str, str]:
        project_id = str(execution_context.get("project_id") or "").strip()
        user_id = str(execution_context.get("user_id") or "").strip()
        return project_id, user_id

    async def _send_parent_resolution_progress_message(
        self,
        *,
        execution_context: Dict[str, Any],
        provider_type: Optional[str],
        work_item_type: Optional[str],
    ) -> None:
        source = str(execution_context.get("source") or "").strip().lower()
        if not source.startswith("slack"):
            return
        project_id = str(execution_context.get("project_id") or "").strip()
        channel_id = str(execution_context.get("channel_id") or "").strip()
        thread_ts = str(execution_context.get("thread_ts") or "").strip() or None
        if not project_id or not channel_id:
            return

        label = str(provider_type or "").strip() or str(work_item_type or "").strip() or "work item"
        template = random.choice(_PARENT_PROGRESS_MESSAGE_TEMPLATES)
        text = template.format(label=label)
        try:
            response_service = CortexResponseService()
            await response_service.send(
                ResponsePayload(
                    text=text,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    metadata={
                        "source": source,
                        "project_id": project_id,
                    },
                )
            )
        except Exception as exc:
            print(
                "Cortex Jira parent-progress message failed: "
                f"tool={self.spec.name} reason={exc}"
            )

    def _resolve_required_jira_scope(
        self,
        *,
        project: Dict[str, Any],
        project_id: str,
    ) -> tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
        jira_integration = ((project.get("integrations") or {}).get("jira") or {})
        jira_project_key = str(jira_integration.get("default_project_key") or "").strip().upper()
        jira_site_url = str(jira_integration.get("site_url") or "").strip() or None
        if jira_project_key:
            return jira_project_key, jira_site_url, None

        return None, jira_site_url, build_tool_error(
            error_code="jira_project_scope_not_configured",
            error_class="validation",
            retryable=False,
            http_status=400,
            user_message=(
                "Jira project scope is not configured for this ProMarshal project. "
                "Please reconnect Jira or select a default Jira project."
            ),
            details={"project_id": project_id, "tool_name": self.spec.name},
        )

    def _deterministic_provider_type_ranking(
        self,
        *,
        requested_type: str,
        allowed_types: List[str],
        title: str,
        description: str,
        user_message: str,
    ) -> List[Dict[str, Any]]:
        requested_token = _canonical_provider_type_token(requested_type)
        combined_text = " ".join(
            [
                str(title or "").strip().lower(),
                str(description or "").strip().lower(),
                str(user_message or "").strip().lower(),
            ]
        ).strip()
        issue_keywords: Dict[str, List[str]] = {
            "bug": ["bug", "defect", "error", "failure", "fix", "broken", "issue"],
            "story": ["story", "requirement", "feature", "user story"],
            "task": ["task", "todo", "chore", "activity"],
            "epic": ["epic", "initiative", "program"],
            "subtask": ["subtask", "sub-task", "child task"],
        }
        scored: List[Dict[str, Any]] = []
        for item in allowed_types:
            label = str(item or "").strip()
            if not label:
                continue
            token = _canonical_provider_type_token(label)
            score = 0.0
            reason_parts: List[str] = []
            if requested_token and token == requested_token:
                score += 1.0
                reason_parts.append("exact type token match")
            if requested_token and token and token in requested_token:
                score += 0.25
            if requested_token and token:
                score += max(
                    0.0,
                    min(
                        0.35,
                        SequenceMatcher(a=requested_token, b=token).ratio() * 0.35,
                    ),
                )
            for key, hints in issue_keywords.items():
                if key != token:
                    continue
                if any(hint in combined_text for hint in hints):
                    score += 0.3
                    reason_parts.append("intent keywords match")
            scored.append(
                {
                    "value": label,
                    "score": float(score),
                    "reason": ", ".join(reason_parts) or "deterministic fallback ranking",
                    "source": "deterministic_fallback",
                }
            )
        scored.sort(
            key=lambda row: (
                float(row.get("score") or 0.0),
                str(row.get("value") or "").lower(),
            ),
            reverse=True,
        )
        ranked: List[Dict[str, Any]] = []
        for row in scored:
            ranked.append(
                {
                    "value": str(row.get("value") or "").strip(),
                    "confidence": round(float(row.get("score") or 0.0), 3),
                    "reason": str(row.get("reason") or "").strip(),
                    "source": str(row.get("source") or "deterministic_fallback").strip(),
                }
            )
        return ranked

    async def _rank_provider_type_suggestions(
        self,
        *,
        requested_type: str,
        allowed_types: List[str],
        title: str,
        description: str,
        user_message: str,
    ) -> List[Dict[str, Any]]:
        normalized_allowed = [str(item or "").strip() for item in allowed_types if str(item or "").strip()]
        if not normalized_allowed:
            return []
        fallback = self._deterministic_provider_type_ranking(
            requested_type=requested_type,
            allowed_types=normalized_allowed,
            title=title,
            description=description,
            user_message=user_message,
        )
        gateway = get_shared_llm_gateway()
        if gateway is None:
            return fallback

        model_name = str(
            getattr(settings, "cortex_llm_primary_model", "") or getattr(settings, "cortex_llm_fallback_model", "gpt-4o-mini")
        ).strip() or "gpt-4o-mini"
        timeout_seconds = max(
            2,
            min(
                20,
                int(getattr(settings, "work_item_parent_ranking_llm_timeout_seconds", 20) or 20),
            ),
        )
        system_prompt = (
            "Rank allowed provider issue types for a create request.\n"
            "Return STRICT JSON only with shape:\n"
            "{\"ranked\":[{\"value\":\"\",\"confidence\":0.0,\"reason\":\"\"}]}\n"
            "Rules:\n"
            "- Use only values from allowed_types.\n"
            "- Return at most 3 entries.\n"
            "- confidence range 0..1.\n"
            "- No markdown."
        )
        payload = {
            "requested_type": str(requested_type or "").strip(),
            "allowed_types": list(normalized_allowed),
            "title": str(title or "").strip(),
            "description": str(description or "").strip(),
            "user_message": str(user_message or "").strip(),
        }
        try:
            response = await gateway.generate(
                LLMGatewayRequest(
                    messages=[
                        LLMMessage(role=LLMMessageRole.SYSTEM, content=system_prompt),
                        LLMMessage(
                            role=LLMMessageRole.USER,
                            content=json.dumps(payload, ensure_ascii=True),
                        ),
                    ],
                    model=model_name,
                    retries=0,
                    timeout_seconds=timeout_seconds,
                    metadata={"feature": "jira_create_provider_type_rank"},
                )
            )
            if not bool(response.ok):
                return fallback
            parsed = _parse_json_object(str(response.text or ""))
            if not isinstance(parsed, dict):
                return fallback
            raw_ranked = parsed.get("ranked")
            if not isinstance(raw_ranked, list):
                return fallback
            allowed_map: Dict[str, str] = {
                _canonical_provider_type_token(item): item for item in normalized_allowed
            }
            ranked: List[Dict[str, Any]] = []
            for row in raw_ranked:
                if not isinstance(row, dict):
                    continue
                candidate_label = _normalize_provider_type_label(str(row.get("value") or ""))
                token = _canonical_provider_type_token(candidate_label)
                resolved_allowed = allowed_map.get(token)
                if not resolved_allowed:
                    continue
                ranked.append(
                    {
                        "value": resolved_allowed,
                        "confidence": max(
                            0.0,
                            min(1.0, float(row.get("confidence") or 0.0)),
                        ),
                        "reason": str(row.get("reason") or "").strip() or "best contextual match",
                        "source": "llm_ranked",
                    }
                )
            if not ranked:
                return fallback
            deduped: List[Dict[str, Any]] = []
            seen: set[str] = set()
            for row in ranked + fallback:
                label = str(row.get("value") or "").strip()
                token = _canonical_provider_type_token(label)
                if not token or token in seen:
                    continue
                seen.add(token)
                deduped.append(row)
                if len(deduped) >= 3:
                    break
            return deduped or fallback
        except Exception as exc:
            print(
                "Cortex Jira provider-type ranking fallback: "
                f"tool={self.spec.name} requested={requested_type} reason={exc}"
            )
            return fallback

    def _build_provider_type_clarification_message(
        self,
        *,
        requested_type: str,
        jira_project_key: str,
        allowed_types: List[str],
        ranked_suggestions: List[Dict[str, Any]],
    ) -> str:
        clean_requested = str(requested_type or "work item").strip()
        clean_project_key = str(jira_project_key or "").strip().upper() or "this project"
        if ranked_suggestions:
            top = ranked_suggestions[0]
            top_value = str(top.get("value") or "").strip()
            if len(ranked_suggestions) > 1:
                ranked_line = ", ".join(
                    [
                        str(item.get("value") or "").strip()
                        for item in ranked_suggestions
                        if str(item.get("value") or "").strip()
                    ]
                )
                return (
                    f"`{clean_requested}` isn't allowed in Jira project `{clean_project_key}`. "
                    f"Choose one: {ranked_line} (or `cancel`)."
                )
            return (
                f"`{clean_requested}` isn't allowed in Jira project `{clean_project_key}`. "
                f"Use `{top_value}` instead? Reply `yes`, `{top_value}`, or `cancel`."
            )
        allowed_line = ", ".join([str(item).strip() for item in allowed_types if str(item).strip()])
        return (
            f"`{clean_requested}` isn't allowed in Jira project `{clean_project_key}`. "
            f"Choose one: {allowed_line} (or `cancel`)."
        )

    async def _preflight_provider_type_for_create(
        self,
        *,
        parsed: JiraCreateTaskInput,
        project: Dict[str, Any],
        project_id: str,
        user_message: str,
    ) -> Optional[Dict[str, Any]]:
        jira_project_key, _jira_site_url, jira_scope_error = self._resolve_required_jira_scope(
            project=project,
            project_id=project_id,
        )
        if jira_scope_error:
            return jira_scope_error
        if not jira_project_key:
            return None

        adapter = JiraAdapter(project)
        preview = await adapter.preview_issue_type_for_create(
            jira_project_key=jira_project_key,
            provider_type=parsed.provider_type,
            work_item_type=parsed.work_item_type,
        )
        requested_type = str(
            preview.get("requested_issue_type")
            or parsed.provider_type
            or parsed.work_item_type
            or "Task"
        ).strip()
        matched_type = str(preview.get("matched_issue_type") or "").strip()
        allowed_types = [
            str(item or "").strip()
            for item in (preview.get("allowed_issue_types") or [])
            if str(item or "").strip()
        ]
        if matched_type:
            parsed.provider_type = matched_type
            return None
        if not allowed_types:
            # Fail open when metadata is unavailable; adapter still validates at write time.
            return None

        ranked = await self._rank_provider_type_suggestions(
            requested_type=requested_type,
            allowed_types=allowed_types,
            title=parsed.title,
            description=str(parsed.description or ""),
            user_message=user_message,
        )
        suggested_type = str((ranked[0] or {}).get("value") or "").strip() if ranked else ""
        user_message_text = self._build_provider_type_clarification_message(
            requested_type=requested_type,
            jira_project_key=str(preview.get("jira_project_key") or jira_project_key),
            allowed_types=allowed_types,
            ranked_suggestions=ranked,
        )
        return build_tool_error(
            error_code="provider_type_clarification_required",
            error_class="validation",
            retryable=False,
            http_status=400,
            user_message=user_message_text,
            details={
                "tool_name": self.spec.name,
                "field": "provider_type",
                "requested_value": requested_type,
                "allowed_values": allowed_types,
                "ranked_suggestions": ranked,
                "suggested_value": suggested_type or None,
                "jira_project_key": str(preview.get("jira_project_key") or jira_project_key),
                "cache_source": str(preview.get("cache_source") or "").strip(),
                "proposed_tool_call": {
                    "tool_name": self.spec.name,
                    "args": parsed.model_dump(mode="json"),
                },
            },
        )

    async def _resolve_parent_reference_state(
        self,
        *,
        project: Dict[str, Any],
        parent_external_id: str,
    ) -> Dict[str, Any]:
        try:
            return await resolve_parent_reference(
                project=project,
                parent_external_id=parent_external_id,
            )
        except Exception as exc:
            print(
                f"Cortex parent-reference validation failed ({self.spec.name}): "
                f"parent={parent_external_id} reason={exc}"
            )
            return {
                "exists": False,
                "parent_external_id": str(parent_external_id or "").strip().upper(),
                "source": "unverified",
                "reason": "validation_failed",
            }

    def _resolve_actor_email_from_context(
        self,
        *,
        project: Dict[str, Any],
        execution_context: Dict[str, Any],
        user_id: str,
    ) -> Optional[str]:
        actor_identity = (
            execution_context.get("actor_identity")
            if isinstance(execution_context.get("actor_identity"), dict)
            else {}
        )
        actor_email_hint = str(execution_context.get("actor_email") or "").strip().lower() or None
        actor_jira_account_id = str(actor_identity.get("jira_account_id") or "").strip() or None
        return resolve_actor_email(
            project,
            actor_email=actor_email_hint,
            slack_user_id=user_id,
            jira_account_id=actor_jira_account_id,
        )

    def _resolve_actor_jira_account_id_from_context(
        self,
        *,
        project: Dict[str, Any],
        execution_context: Dict[str, Any],
        user_id: str,
    ) -> Optional[str]:
        actor_identity = (
            execution_context.get("actor_identity")
            if isinstance(execution_context.get("actor_identity"), dict)
            else {}
        )
        actor_email_hint = str(execution_context.get("actor_email") or "").strip().lower() or None
        actor_jira_account_id = str(actor_identity.get("jira_account_id") or "").strip() or None
        member = resolve_actor_member(
            project,
            actor_email=actor_email_hint,
            slack_user_id=user_id,
            jira_account_id=actor_jira_account_id,
        )
        if not isinstance(member, dict):
            return None
        integration_ids = member.get("integration_ids") or {}
        account_id = str(integration_ids.get("jira_account_id") or "").strip()
        return account_id or None

    def _resolve_actor_name_from_context(
        self,
        *,
        project: Dict[str, Any],
        execution_context: Dict[str, Any],
        user_id: str,
    ) -> Optional[str]:
        actor_identity = (
            execution_context.get("actor_identity")
            if isinstance(execution_context.get("actor_identity"), dict)
            else {}
        )
        actor_email_hint = str(execution_context.get("actor_email") or "").strip().lower() or None
        actor_jira_account_id = str(actor_identity.get("jira_account_id") or "").strip() or None
        member = resolve_actor_member(
            project,
            actor_email=actor_email_hint,
            slack_user_id=user_id,
            jira_account_id=actor_jira_account_id,
        )
        if not isinstance(member, dict):
            return None
        name = str(member.get("name") or "").strip()
        return name or None

    def _resolve_actor_user_id_from_context(
        self,
        *,
        project: Dict[str, Any],
        execution_context: Dict[str, Any],
        user_id: str,
    ) -> Optional[str]:
        actor_identity = (
            execution_context.get("actor_identity")
            if isinstance(execution_context.get("actor_identity"), dict)
            else {}
        )
        actor_email_hint = str(execution_context.get("actor_email") or "").strip().lower() or None
        actor_jira_account_id = str(actor_identity.get("jira_account_id") or "").strip() or None
        member = resolve_actor_member(
            project,
            actor_email=actor_email_hint,
            slack_user_id=user_id,
            jira_account_id=actor_jira_account_id,
        )
        if not isinstance(member, dict):
            return None
        resolved = str(member.get("user_id") or "").strip()
        return resolved or None

    def _caller_role(self, execution_context: Dict[str, Any]) -> str:
        return str(execution_context.get("role") or "").strip().lower() or "member"

    def _mark_task_recommendation_dirty(self, *, project_id: str, task_key: Optional[str]) -> None:
        clean_project_id = str(project_id or "").strip()
        clean_task_key = str(task_key or "").strip()
        if not clean_project_id or not clean_task_key:
            return
        try:
            mark_project_task_dirty(clean_project_id, clean_task_key)
        except Exception as exc:
            print(
                "Cortex Jira dirty marker failed "
                f"(project_id={clean_project_id}, task_key={clean_task_key}): {exc}"
            )

    def _compose_comment_text(self, comment: str) -> str:
        return compose_comment_text(comment)

    def _build_self_assignee_unresolved_error(
        self,
        *,
        execution_context: Dict[str, Any],
        project_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        role = self._caller_role(execution_context)
        if role == MemberRole.OWNER.value:
            message = (
                "I couldn't resolve your member identity for assignee `me` in this project. "
                "Please reconnect member identity mapping or provide your Jira-linked email."
            )
        else:
            message = (
                "I couldn't resolve your member identity for assignee `me` in this project. "
                "Please provide your Jira-linked email."
            )
        return build_tool_error(
            error_code="actor_identity_not_resolved",
            error_class="validation",
            retryable=False,
            http_status=400,
            user_message=message,
            details={
                "tool_name": self.spec.name,
                "project_id": project_id,
                "user_id": user_id,
                "assignee_hint": "me",
            },
        )

    def _resolve_assignee_hint_for_write(
        self,
        *,
        assignee_hint: str,
        project: Dict[str, Any],
        execution_context: Dict[str, Any],
        user_id: str,
    ) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        actor_identity = (
            execution_context.get("actor_identity")
            if isinstance(execution_context.get("actor_identity"), dict)
            else {}
        )
        actor_email_hint = str(execution_context.get("actor_email") or "").strip().lower() or None
        actor_jira_account_id = str(actor_identity.get("jira_account_id") or "").strip() or None
        resolved_value, used_self_alias = resolve_self_alias_to_actor_email(
            assignee_hint,
            project=project,
            actor_email=actor_email_hint,
            slack_user_id=user_id,
            jira_account_id=actor_jira_account_id,
        )
        if not used_self_alias:
            return resolved_value, None
        if resolved_value:
            return resolved_value, None

        project_id = str(execution_context.get("project_id") or "").strip()
        return None, self._build_self_assignee_unresolved_error(
            execution_context=execution_context,
            project_id=project_id,
            user_id=user_id,
        )

    def _owner_mapping_insight(
        self,
        *,
        resolution: Optional[AssigneeResolutionResult],
        execution_context: Dict[str, Any],
    ) -> Optional[str]:
        role = self._caller_role(execution_context)
        if role != MemberRole.OWNER.value:
            return None
        if resolution is None:
            return None

        if resolution.mapping_conflict:
            existing = str(resolution.mapping_existing_account_id or "").strip() or "unknown"
            candidate = str(resolution.mapping_candidate_account_id or "").strip() or "unknown"
            return (
                "I detected a Jira mapping conflict for this member "
                f"(stored account `{existing}` vs detected `{candidate}`), "
                "so I did not overwrite the stored mapping."
            )

        if resolution.mapping_backfill_attempted and resolution.mapping_backfill_succeeded:
            return "I also synced Jira mapping for this member."

        if resolution.mapping_backfill_attempted and not resolution.mapping_backfill_succeeded:
            return (
                "I completed the action, but I could not persist Jira mapping for this member yet."
            )
        return None

    def _sprint_placement_payload(
        self,
        sprint_placement: Any,
    ) -> Optional[Dict[str, Any]]:
        if sprint_placement is None:
            return None
        if hasattr(sprint_placement, "model_dump"):
            payload = sprint_placement.model_dump(mode="json")
        elif isinstance(sprint_placement, dict):
            payload = dict(sprint_placement)
        else:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _sprint_placement_insight(self, sprint_placement: Any) -> Optional[str]:
        payload = self._sprint_placement_payload(sprint_placement)
        if not payload:
            return None
        directive = str(payload.get("directive") or "").strip().lower()
        reason = str(payload.get("reason") or "").strip().lower()
        attempted = bool(payload.get("attempted"))
        applied = bool(payload.get("applied"))
        board_type = str(payload.get("board_type") or "").strip().lower()
        sprint_name = str(payload.get("sprint_name") or "").strip()

        if applied:
            if sprint_name:
                return f"Added to active sprint '{sprint_name}'."
            return "Added to the active sprint."

        if directive == SprintDirective.BACKLOG.value and reason == "explicit_backlog":
            return "Kept in backlog as requested."
        if reason == "no_active_sprint":
            return "There is no active sprint right now, so it remains in backlog."
        if reason == "default_board_not_configured":
            return "Sprint placement skipped because no default Jira board is configured."
        if reason == "board_not_scrum" and board_type == "kanban":
            return "Project board type is Kanban, so sprint placement is not applicable."
        if attempted and not applied:
            return "Sprint placement could not be completed, but the Jira write succeeded."
        return None

    def _infer_sprint_directive_from_context(
        self,
        execution_context: Dict[str, Any],
    ) -> Optional[SprintDirective]:
        text = str(execution_context.get("user_message") or "").strip()
        if not text:
            return None
        if SPRINT_BACKLOG_HINT_PATTERN.search(text):
            return SprintDirective.BACKLOG
        if SPRINT_ACTIVE_HINT_PATTERN.search(text):
            return SprintDirective.ACTIVE_SPRINT
        return None

    def _build_assignee_resolution_error(
        self,
        *,
        assignee_hint: str,
        resolution: AssigneeResolutionResult,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        role = self._caller_role(execution_context)
        is_owner = role == MemberRole.OWNER.value
        normalized_hint = str(assignee_hint or "").strip()

        if resolution.ambiguous:
            candidates = ", ".join((resolution.candidates or [])[:3]) or "multiple matches"
            if is_owner:
                scope = (
                    "project members"
                    if resolution.reason == "ambiguous_project_member"
                    else "Jira users"
                )
                message = (
                    f"I found multiple {scope} matching '{normalized_hint}': {candidates}. "
                    "Please provide the assignee email."
                )
            else:
                message = (
                    f"I found multiple assignee matches for '{normalized_hint}': {candidates}. "
                    "Please provide the assignee email."
                )
            return build_tool_error(
                error_code="assignee_ambiguous",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message=message,
                details={
                    "tool_name": self.spec.name,
                    "assignee_hint": normalized_hint,
                    "candidates": resolution.candidates or [],
                    "reason": resolution.reason,
                },
            )

        if is_owner and resolution.reason == "project_member_missing_jira_mapping":
            member_label = (
                resolution.project_member_email
                or resolution.project_member_name
                or normalized_hint
            )
            message = (
                f"'{member_label}' is an active ProMarshal member, but Jira account mapping is missing "
                "for this project. Please reconnect/sync Jira member mapping or provide a Jira-linked email."
            )
        elif is_owner and resolution.reason == "jira_workspace_lookup_failed":
            message = (
                f"I couldn't resolve Jira assignee for '{normalized_hint}' because Jira user lookup failed. "
                "Please retry after integration health recovers, or provide a mapped member email."
            )
        elif is_owner and resolution.reason == "jira_mapping_conflict":
            existing = str(resolution.mapping_existing_account_id or "").strip() or "unknown"
            candidate = str(resolution.mapping_candidate_account_id or "").strip() or "unknown"
            message = (
                f"I found a Jira mapping conflict for '{normalized_hint}' "
                f"(stored account `{existing}` vs detected `{candidate}`). "
                "I did not overwrite the stored mapping. Please run a mapping sync or use Jira-linked email."
            )
        elif is_owner and resolution.reason == "not_found":
            message = (
                f"I couldn't find '{normalized_hint}' in active project members with Jira mapping or Jira workspace users. "
                "Please provide the assignee email."
            )
        else:
            message = (
                f"I couldn't resolve a Jira assignee for '{normalized_hint}'. "
                "Please provide the assignee email."
            )

        return build_tool_error(
            error_code="assignee_not_mapped",
            error_class="validation",
            retryable=False,
            http_status=400,
            user_message=message,
            details={
                "tool_name": self.spec.name,
                "assignee_hint": normalized_hint,
                "reason": resolution.reason,
                "project_member_matched": bool(resolution.project_member_matched),
                "project_member_email": resolution.project_member_email,
                "project_member_name": resolution.project_member_name,
                "jira_mapping_present": bool(resolution.jira_mapping_present),
                "mapping_backfill_attempted": bool(resolution.mapping_backfill_attempted),
                "mapping_backfill_succeeded": bool(resolution.mapping_backfill_succeeded),
                "mapping_conflict": bool(resolution.mapping_conflict),
                "mapping_existing_account_id": resolution.mapping_existing_account_id,
                "mapping_candidate_account_id": resolution.mapping_candidate_account_id,
            },
        )

    def _effective_assignee_mode(self, parsed: JiraSearchTasksInput) -> AssigneeMode:
        return parsed.assignee_mode or AssigneeMode.ANY

    def _resolve_specific_assignee_email(
        self,
        *,
        project: Dict[str, Any],
        assignee_hint: str,
    ) -> Optional[str]:
        hint = str(assignee_hint).strip().lower()
        if not hint:
            return None
        if "@" in hint:
            return hint

        member_matches = []
        for member in project.get("members") or []:
            if not isinstance(member, dict):
                continue
            name = str(member.get("name") or "").strip().lower()
            email = str(member.get("email") or "").strip().lower()
            if hint in name or hint in email:
                if email:
                    member_matches.append(email)
        deduped = sorted(set(member_matches))
        if len(deduped) == 1:
            return deduped[0]
        return None

    def _in_date_range(self, *, value: Any, date_range: Optional[DateRange]) -> bool:
        if date_range is None:
            return True
        candidate: Optional[datetime] = None
        if isinstance(value, datetime):
            candidate = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                candidate = parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed
            except Exception:
                candidate = None
        if candidate is None:
            return True
        start = date_range.start
        end = date_range.end
        if candidate.tzinfo is not None:
            candidate = candidate.replace(tzinfo=None)
        if start.tzinfo is not None:
            start = start.replace(tzinfo=None)
        if end.tzinfo is not None:
            end = end.replace(tzinfo=None)
        return start <= candidate <= end

    def _serialize_brain_doc(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(doc)
        payload["_id"] = str(payload.get("_id"))
        return serialize_task_like(payload)

    def _resolve_datetime_from_input(
        self,
        *,
        raw_value: Any,
        timezone_name: Optional[str],
        field_name: str,
    ) -> tuple[Optional[datetime], Optional[Dict[str, Any]]]:
        if raw_value is None:
            return None, build_tool_error(
                error_code="missing_required_field",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message=f"`{field_name}` is required.",
                details={"tool_name": self.spec.name, "field": field_name},
            )

        if isinstance(raw_value, datetime):
            resolved = raw_value
        else:
            text_value = normalize_text(str(raw_value))
            if not text_value:
                return None, build_tool_error(
                    error_code="missing_required_field",
                    error_class="validation",
                    retryable=False,
                    http_status=400,
                    user_message=f"`{field_name}` is required.",
                    details={"tool_name": self.spec.name, "field": field_name},
                )
            resolved = None
            try:
                # Supports YYYY-MM-DD, ISO datetime, and timezone-qualified strings.
                parsed_iso = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
                resolved = parsed_iso
            except Exception:
                temporal = parse_temporal_hints(
                    text=text_value,
                    timezone_name=timezone_name,
                )
                resolved = temporal.target_datetime

            if resolved is None:
                return None, build_tool_error(
                    error_code="invalid_datetime_input",
                    error_class="validation",
                    retryable=False,
                    http_status=400,
                    user_message=(
                        f"I couldn't parse `{field_name}`. Use an ISO date "
                        "(for example `2026-04-05`) or phrases like `today`/`tomorrow`."
                    ),
                    details={
                        "tool_name": self.spec.name,
                        "field": field_name,
                        "received": text_value,
                        "project_timezone": str(timezone_name or "UTC"),
                    },
                )

        if resolved.tzinfo is not None:
            resolved = resolved.astimezone(timezone.utc).replace(tzinfo=None)
        return resolved, None

    async def _search_brain_tasks(
        self,
        *,
        project_id: str,
        project_context: Optional[Dict[str, Any]] = None,
        jira_project_key: str,
        jira_site_url: Optional[str],
        parsed: JiraSearchTasksInput,
        assignee_mode: AssigneeMode,
        include_unassigned: bool,
        assignee_hint: Optional[str],
        assignee_email: Optional[str],
        assignee_account_id: Optional[str],
        assignee_name_exact: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        assignee_filter = (assignee_hint or "").strip().lower()
        status_filter: Optional[List[str]] = None
        is_closed_filter: Optional[bool] = None
        if parsed.status == SearchStatus.PENDING:
            is_closed_filter = False
        elif parsed.status == SearchStatus.DONE:
            is_closed_filter = True
        elif parsed.status:
            status_filter = [parsed.status.value]
            if parsed.status in {SearchStatus.TODO, SearchStatus.IN_PROGRESS}:
                is_closed_filter = False

        filters = ListTasksFilters(
            assignee_email=assignee_email if assignee_mode == AssigneeMode.SPECIFIC else None,
            assignee_user_id=(
                assignee_account_id if assignee_mode == AssigneeMode.SPECIFIC else None
            ),
            assignee_account_id=(
                assignee_account_id if assignee_mode == AssigneeMode.SPECIFIC else None
            ),
            assignee_name_exact=(
                str(assignee_name_exact or "").strip() if assignee_mode == AssigneeMode.SPECIFIC else None
            ),
            status_filter=status_filter,
            provider_types=list(parsed.provider_types or []),
            due_date_mode=str(parsed.due_date_mode.value if hasattr(parsed.due_date_mode, "value") else parsed.due_date_mode or "").strip().lower() or None,
            is_closed=is_closed_filter,
            limit=parsed.max_results,
        )
        tasks = await TaskService.list_tasks(
            project_id=project_id,
            filters=filters,
            provider_hint="jira",
            project_context=project_context,
        )
        task_dicts = [serialize_task_like(task) for task in tasks]
        normalized_project_key = str(jira_project_key or "").strip().upper()
        normalized_site_prefix = str(jira_site_url or "").strip().lower().rstrip("/")

        filtered: List[Dict[str, Any]] = []
        keyword = (parsed.keyword or "").lower()
        for item in task_dicts:
            external_id = str(item.get("external_id") or "").strip().upper()
            if not external_id.startswith(f"{normalized_project_key}-"):
                continue
            if normalized_site_prefix:
                task_url = str(item.get("url") or "").strip().lower()
                if task_url and not task_url.startswith(f"{normalized_site_prefix}/browse/"):
                    continue
            task_assignee_email = str(item.get("assignee_email") or "").strip().lower()
            task_assignee_name = str(item.get("assignee_name") or "").strip().lower()

            if assignee_mode == AssigneeMode.UNASSIGNED and (
                task_assignee_email or task_assignee_name
            ):
                continue
            if assignee_mode == AssigneeMode.ANY and not include_unassigned and (
                not task_assignee_email and not task_assignee_name
            ):
                continue

            if assignee_mode == AssigneeMode.SPECIFIC and assignee_filter and "@" not in assignee_filter:
                if (
                    assignee_filter not in task_assignee_name
                    and assignee_filter not in task_assignee_email
                ):
                    continue

            if parsed.priority and str(item.get("priority") or "").lower() != parsed.priority.value:
                continue
            if keyword:
                haystack = " ".join(
                    filter(None, [
                        str(item.get("external_id") or ""),
                        str(item.get("title") or ""),
                        str(item.get("status") or ""),
                        str(item.get("description") or ""),  # optional — absent on older tasks
                    ])
                ).lower()
                if keyword not in haystack:
                    continue
            if not self._in_date_range(value=item.get("updated_at"), date_range=parsed.date_range):
                continue
            filtered.append(item)

        return filtered[: parsed.max_results]

    async def _search_jira_live_tasks(
        self,
        *,
        project: Dict[str, Any],
        jira_project_key: str,
        parsed: JiraSearchTasksInput,
        assignee_mode: AssigneeMode,
        include_unassigned: bool,
        assignee_hint: Optional[str],
        assignee_email: Optional[str],
        assignee_account_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        adapter = JiraAdapter(project)
        await adapter.ensure_token_valid()

        clauses: List[str] = []
        clauses.append(f'project = "{str(jira_project_key).strip().upper()}"')

        if assignee_mode == AssigneeMode.UNASSIGNED:
            clauses.append("assignee is EMPTY")
        elif assignee_mode == AssigneeMode.ANY and not include_unassigned:
            clauses.append("assignee is not EMPTY")
        elif assignee_mode == AssigneeMode.SPECIFIC:
            account_id = str(assignee_account_id or "").strip() or None
            if not account_id:
                resolved_assignee_email = assignee_email or self._resolve_specific_assignee_email(
                    project=project,
                    assignee_hint=str(assignee_hint or ""),
                )
                if not resolved_assignee_email:
                    print(
                        "Cortex jira_search_work_items live path skipped because assignee "
                        f"could not be resolved uniquely: {assignee_hint}"
                    )
                    return []

                account_id = await resolve_assignee_account_id(
                    project,
                    assignee_email=resolved_assignee_email,
                )
                if not account_id:
                    print(
                        "Cortex jira_search_work_items live path skipped because assignee "
                        f"account id was not found: {resolved_assignee_email}"
                    )
                    return []
            clauses.append(f'assignee = "{account_id}"')

        if parsed.status:
            if parsed.status == SearchStatus.PENDING:
                clauses.append("statusCategory != Done")
            elif parsed.status == SearchStatus.DONE:
                clauses.append("statusCategory = Done")
            else:
                status_map = {
                    "todo": '"To Do"',
                    "in_progress": '"In Progress"',
                }
                status_name = status_map.get(parsed.status.value)
                if status_name:
                    clauses.append(f"status = {status_name}")

        if parsed.provider_types:
            quoted_types = []
            for provider_type in parsed.provider_types:
                normalized_label = _normalize_provider_type_label(provider_type)
                if not normalized_label:
                    continue
                escaped = str(normalized_label).replace('"', '\\"')
                quoted_types.append(f'"{escaped}"')
            if quoted_types:
                clauses.append(f"issuetype in ({', '.join(quoted_types)})")

        if parsed.priority:
            priority_map = {
                "low": '"Low"',
                "medium": '"Medium"',
                "high": '"High"',
                "critical": '"Highest"',
            }
            priority_name = priority_map.get(parsed.priority.value)
            if priority_name:
                clauses.append(f"priority = {priority_name}")

        due_date_mode = str(
            parsed.due_date_mode.value if hasattr(parsed.due_date_mode, "value") else parsed.due_date_mode or ""
        ).strip().lower()
        if due_date_mode == DueDateMode.MISSING.value:
            clauses.append("duedate is EMPTY")
        elif due_date_mode == DueDateMode.PRESENT.value:
            clauses.append("duedate is not EMPTY")

        if parsed.keyword:
            safe_keyword = str(parsed.keyword).replace('"', '\\"')
            clauses.append(f'summary ~ "{safe_keyword}"')

        if parsed.date_range:
            clauses.append(f'updated >= "{parsed.date_range.start.date().isoformat()}"')
            clauses.append(f'updated <= "{parsed.date_range.end.date().isoformat()}"')

        jql = " AND ".join(clauses)
        jql = f"{jql} ORDER BY updated DESC"

        issues = await jira_oauth_service.search_issues(
            access_token=adapter.access_token,
            cloud_id=adapter.cloud_id,
            jql=jql,
            max_results=parsed.max_results,
        )

        normalizer = JiraNormalizer(cloud_id=adapter.cloud_id, site_url=adapter.site_url)
        normalized = [serialize_task_like(normalizer.normalize_task(issue)) for issue in issues]
        return normalized[: parsed.max_results]


class JiraCreateTaskTool(_BaseJiraTool):
    input_model = JiraCreateTaskInput
    spec = ToolSpec(
        name="jira_create_work_item",
        description=(
            "Create a Jira work item in the active project (assignee is optional; accepts email or display name). "
            "Optional provider_type: Story | Epic | Bug | Task | Sub-task. "
            "Optional sprint_directive: auto | active_sprint | backlog."
        ),
        provider="jira",
        capabilities=["workitem_create"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=["jira"],
        requires_confirmation=True,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: JiraCreateTaskInput,
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
        jira_project_key, _jira_site_url, jira_scope_error = self._resolve_required_jira_scope(
            project=project,
            project_id=project_id,
        )
        if jira_scope_error:
            return jira_scope_error

        assignee_hint = str(parsed.assignee or "").strip()
        requested_assignee_hint: Optional[str] = None
        assignee_resolution: Optional[AssigneeResolutionResult] = None
        assignee_account_id: Optional[str] = None
        if assignee_hint:
            effective_assignee_hint, self_alias_error = self._resolve_assignee_hint_for_write(
                assignee_hint=assignee_hint,
                project=project,
                execution_context=execution_context,
                user_id=user_id,
            )
            if self_alias_error is not None:
                return self_alias_error
            requested_assignee_hint = str(effective_assignee_hint or assignee_hint).strip() or None

            assignee_resolution = await resolve_assignee_identity(
                project,
                assignee_hint=str(effective_assignee_hint or assignee_hint),
            )
            assignee_account_id = assignee_resolution.account_id
            if not assignee_account_id:
                return self._build_assignee_resolution_error(
                    assignee_hint=str(effective_assignee_hint or assignee_hint),
                    resolution=assignee_resolution,
                    execution_context=execution_context,
                )

        provider_type_preflight_error = await self._preflight_provider_type_for_create(
            parsed=parsed,
            project=project,
            project_id=project_id,
            user_message=str(
                execution_context.get("latest_user_message")
                or execution_context.get("user_message")
                or ""
            ).strip(),
        )
        if provider_type_preflight_error is not None:
            return provider_type_preflight_error

        actor_email = (
            self._resolve_actor_email_from_context(
                project=project,
                execution_context=execution_context,
                user_id=user_id,
            )
            or user_id
            or "unknown_actor"
        )
        requested_provider_type = str(parsed.provider_type or "").strip().lower()
        requested_work_item_type = str(parsed.work_item_type or "").strip().lower()
        requested_issue_type_key = requested_provider_type.replace("-", " ").replace("_", " ")
        is_subtask_request = requested_work_item_type == "subtask" or requested_issue_type_key in {
            "subtask",
            "sub task",
        }
        resolved_sprint_directive = (
            parsed.sprint_directive
            or self._infer_sprint_directive_from_context(execution_context)
        )
        try:
            parent_rule_filter = await resolve_parent_rule_filter(
                project=project,
                create_payload={
                    "provider": "jira",
                    "provider_type": parsed.provider_type,
                    "work_item_type": parsed.work_item_type,
                },
            )
        except Exception as exc:
            print(f"Cortex parent-rule resolution failed (jira_create_work_item): {exc}")
            parent_rule_filter = {}
        if not isinstance(parent_rule_filter, dict):
            parent_rule_filter = {}
        parent_required = bool((parent_rule_filter or {}).get("parent_required"))
        if not parent_required and (
            not parent_rule_filter
            or is_subtask_request
        ):
            # Conservative fallback when metadata is missing or stale/misclassified.
            parent_required = is_subtask_request
        parent_allowed = bool((parent_rule_filter or {}).get("parent_allowed"))
        has_parent_provided = bool(str(parsed.parent_external_id or "").strip())
        if has_parent_provided:
            parent_reference = await self._resolve_parent_reference_state(
                project=project,
                parent_external_id=str(parsed.parent_external_id or "").strip(),
            )
            if not bool((parent_reference or {}).get("exists")):
                actor_user_id_for_pending = (
                    self._resolve_actor_user_id_from_context(
                        project=project,
                        execution_context=execution_context,
                        user_id=user_id,
                    )
                    or user_id
                )
                await self._send_parent_resolution_progress_message(
                    execution_context=execution_context,
                    provider_type=parsed.provider_type,
                    work_item_type=parsed.work_item_type,
                )
                pending_result = await queue_work_item_parent_clarification(
                    project=project,
                    actor_user_id=actor_user_id_for_pending,
                    actor_email=actor_email,
                    user_message=str(
                        execution_context.get("latest_user_message")
                        or execution_context.get("user_message")
                        or ""
                    ).strip(),
                    create_payload={
                        "provider": "jira",
                        "title": parsed.title,
                        "description": parsed.description,
                        "assignee_account_id": assignee_account_id,
                        "requested_assignee_hint": requested_assignee_hint,
                        "provider_type": parsed.provider_type,
                        "work_item_type": parsed.work_item_type,
                        "priority": parsed.priority.value,
                        "sprint_directive": resolved_sprint_directive.value if resolved_sprint_directive else None,
                    },
                )
                not_available = build_parent_reference_not_available_message(
                    str(parsed.parent_external_id or "")
                )
                pending_text = str(pending_result.get("response_text") or "").strip()
                response_text = not_available if not pending_text else f"{not_available}\n\n{pending_text}"
                return {
                    "ok": True,
                    "tool_name": self.spec.name,
                    "source": "brain",
                    "requires_clarification": True,
                    "clarification_type": "work_item_parent",
                    "response_text": response_text,
                    "candidate_parents": pending_result.get("candidates") or [],
                }
        if parent_required and not str(parsed.parent_external_id or "").strip():
            actor_user_id_for_pending = (
                self._resolve_actor_user_id_from_context(
                    project=project,
                    execution_context=execution_context,
                    user_id=user_id,
                )
                or user_id
            )
            await self._send_parent_resolution_progress_message(
                execution_context=execution_context,
                provider_type=parsed.provider_type,
                work_item_type=parsed.work_item_type,
            )
            pending_result = await queue_work_item_parent_clarification(
                project=project,
                actor_user_id=actor_user_id_for_pending,
                actor_email=actor_email,
                user_message=str(
                    execution_context.get("latest_user_message")
                    or execution_context.get("user_message")
                    or ""
                ).strip(),
                create_payload={
                    "provider": "jira",
                    "title": parsed.title,
                    "description": parsed.description,
                    "assignee_account_id": assignee_account_id,
                    "requested_assignee_hint": requested_assignee_hint,
                    "provider_type": parsed.provider_type,
                    "work_item_type": parsed.work_item_type,
                    "priority": parsed.priority.value,
                    "sprint_directive": resolved_sprint_directive.value if resolved_sprint_directive else None,
                },
            )
            return {
                "ok": True,
                "tool_name": self.spec.name,
                "source": "brain",
                "requires_clarification": True,
                "clarification_type": "work_item_parent",
                "response_text": str(pending_result.get("response_text") or "").strip(),
                "candidate_parents": pending_result.get("candidates") or [],
            }
        result = await TaskService.create_task_with_sync_status(
            task_data=CreateTaskDTO(
                title=parsed.title,
                description=parsed.description,
                assignee_account_id=assignee_account_id,
                provider_type=parsed.provider_type,
                work_item_type=parsed.work_item_type,
                parent_external_id=parsed.parent_external_id,
                priority=parsed.priority.value,
                project_id=project_id,
                sprint_directive=resolved_sprint_directive,
            ),
            project=project,
            created_by=actor_email,
        )
        task = serialize_task_like(result.task)
        owner_insight = self._owner_mapping_insight(
            resolution=assignee_resolution,
            execution_context=execution_context,
        )
        sprint_placement = self._sprint_placement_payload(result.sprint_placement)
        sprint_insight = self._sprint_placement_insight(result.sprint_placement)
        created_provider_type = str(task.get("provider_type") or parsed.provider_type or "work item").strip()
        summary = f"Created Jira {created_provider_type} {task.get('external_id')}"
        parent_key = str(task.get("parent_key") or parsed.parent_external_id or "").strip().upper()
        if parent_key:
            summary = f"{summary} under {parent_key}"
        if owner_insight:
            summary = f"{summary}. {owner_insight}"
        if sprint_insight:
            summary = f"{summary}. {sprint_insight}"
        optional_parent_recommendation = parent_allowed and not parent_required and not has_parent_provided
        if optional_parent_recommendation:
            summary = (
                f"{summary}. Best practice: you can map this under a parent anytime once you finalize the parent."
            )

        payload: Dict[str, Any] = {
            "ok": True,
            "source": "jira_live",
            "tool_name": self.spec.name,
            "task": task,
            "brain_sync_succeeded": bool(result.brain_sync_succeeded),
            "webhook_compensation_expected": not bool(result.brain_sync_succeeded),
            "committed_action": {
                "summary": summary,
                "tool_name": self.spec.name,
                "external_id": task.get("external_id"),
            },
        }
        if parent_key:
            payload["parent_external_id"] = parent_key
        if owner_insight:
            payload["owner_insight"] = owner_insight
        if optional_parent_recommendation:
            payload["parent_mapping_recommendation"] = (
                "Best practice: you can map this under a parent anytime once you finalize the parent."
            )
        if sprint_placement:
            payload["sprint_placement"] = sprint_placement
        if sprint_insight:
            payload["sprint_insight"] = sprint_insight
        self._mark_task_recommendation_dirty(
            project_id=project_id,
            task_key=task.get("external_id") or "",
        )
        return payload


class JiraUpdateStatusTool(_BaseJiraTool):
    input_model = JiraUpdateStatusInput
    spec = ToolSpec(
        name="jira_update_status",
        description=(
            "Update status of an existing Jira task. "
            "Optional sprint_directive: auto | active_sprint | backlog."
        ),
        provider="jira",
        capabilities=["workitem_update_status"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=["jira"],
        requires_confirmation=False,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: JiraUpdateStatusInput,
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
        _jira_project_key, _jira_site_url, jira_scope_error = self._resolve_required_jira_scope(
            project=project,
            project_id=project_id,
        )
        if jira_scope_error:
            return jira_scope_error

        actor_email = (
            self._resolve_actor_email_from_context(
                project=project,
                execution_context=execution_context,
                user_id=user_id,
            )
            or user_id
            or None
        )
        result = await TaskService.mutate_task(
            task_data=UpdateTaskDTO(
                task_id=parsed.external_id,
                status=parsed.status,
                comment=None,
                project_id=project_id,
                sprint_directive=(
                    parsed.sprint_directive
                    or self._infer_sprint_directive_from_context(execution_context)
                ),
            ),
            project=project,
            updated_by=actor_email,
            source_label="promarshal",
        )
        task = serialize_task_like(result.task)
        sprint_placement = self._sprint_placement_payload(result.sprint_placement)
        sprint_insight = self._sprint_placement_insight(result.sprint_placement)
        summary = f"Updated {parsed.external_id} to {parsed.status}"
        if sprint_insight:
            summary = f"{summary}. {sprint_insight}"
        payload: Dict[str, Any] = {
            "ok": True,
            "source": "jira_live",
            "tool_name": self.spec.name,
            "task": task,
            "status_updated": bool(result.status_updated),
            "brain_sync_succeeded": bool(result.brain_sync_succeeded),
            "webhook_compensation_expected": not bool(result.brain_sync_succeeded),
            "committed_action": {
                "summary": summary,
                "tool_name": self.spec.name,
                "external_id": parsed.external_id,
            },
        }
        if sprint_placement:
            payload["sprint_placement"] = sprint_placement
        if sprint_insight:
            payload["sprint_insight"] = sprint_insight
        self._mark_task_recommendation_dirty(
            project_id=project_id,
            task_key=task.get("external_id") or parsed.external_id,
        )
        return payload


class JiraAddCommentTool(_BaseJiraTool):
    input_model = JiraAddCommentInput
    spec = ToolSpec(
        name="jira_add_comment",
        description="Add a comment to an existing Jira task.",
        provider="jira",
        capabilities=["workitem_add_comment"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=["jira"],
        requires_confirmation=False,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: JiraAddCommentInput,
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

        actor_email = (
            self._resolve_actor_email_from_context(
                project=project,
                execution_context=execution_context,
                user_id=user_id,
            )
            or user_id
            or None
        )
        final_comment_text = self._compose_comment_text(parsed.comment)
        result = await TaskService.mutate_task(
            task_data=UpdateTaskDTO(
                task_id=parsed.external_id,
                status=None,
                comment=final_comment_text,
                project_id=project_id,
            ),
            project=project,
            updated_by=actor_email,
            source_label="promarshal",
        )
        task = serialize_task_like(result.task)
        posted_comment_text = str(result.posted_comment_text or final_comment_text or "").strip()
        self._mark_task_recommendation_dirty(
            project_id=project_id,
            task_key=task.get("external_id") or parsed.external_id,
        )
        return {
            "ok": True,
            "source": "jira_live",
            "tool_name": self.spec.name,
            "task": task,
            "comment_added": bool(result.comment_added),
            "posted_comment_text": posted_comment_text,
            "brain_sync_succeeded": bool(result.brain_sync_succeeded),
            "webhook_compensation_expected": not bool(result.brain_sync_succeeded),
            "committed_action": {
                "summary": f"Added comment to {parsed.external_id}: {posted_comment_text}" if posted_comment_text else f"Added comment to {parsed.external_id}",
                "tool_name": self.spec.name,
                "external_id": parsed.external_id,
            },
        }


class JiraUpdateDescriptionTool(_BaseJiraTool):
    input_model = JiraUpdateDescriptionInput
    spec = ToolSpec(
        name="jira_update_description",
        description="Update the description of an existing Jira task.",
        provider="jira",
        capabilities=["workitem_update_description"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=["jira"],
        requires_confirmation=False,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: JiraUpdateDescriptionInput,
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

        actor_email = (
            self._resolve_actor_email_from_context(
                project=project,
                execution_context=execution_context,
                user_id=user_id,
            )
            or user_id
            or None
        )
        result = await TaskService.mutate_task(
            task_data=UpdateTaskDTO(
                task_id=parsed.external_id,
                status=None,
                comment=None,
                description=parsed.description,
                project_id=project_id,
            ),
            project=project,
            updated_by=actor_email,
            source_label="promarshal",
        )
        task = serialize_task_like(result.task)
        self._mark_task_recommendation_dirty(
            project_id=project_id,
            task_key=task.get("external_id") or parsed.external_id,
        )
        return {
            "ok": True,
            "source": "jira_live",
            "tool_name": self.spec.name,
            "task": task,
            "description_updated": bool(result.description_updated),
            "brain_sync_succeeded": bool(result.brain_sync_succeeded),
            "webhook_compensation_expected": not bool(result.brain_sync_succeeded),
            "committed_action": {
                "summary": f"Updated description for {parsed.external_id}",
                "tool_name": self.spec.name,
                "external_id": parsed.external_id,
            },
        }


class JiraUpdateDueDateTool(_BaseJiraTool):
    input_model = JiraUpdateDueDateInput
    spec = ToolSpec(
        name="jira_update_due_date",
        description="Set or update due date on an existing Jira work item.",
        provider="jira",
        capabilities=["workitem_update_due_date"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=["jira"],
        requires_confirmation=False,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: JiraUpdateDueDateInput,
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

        resolved_due_date, date_error = self._resolve_datetime_from_input(
            raw_value=parsed.due_date,
            timezone_name=str(project.get("timezone") or "UTC").strip() or "UTC",
            field_name="due_date",
        )
        if date_error is not None:
            return date_error

        actor_email = (
            self._resolve_actor_email_from_context(
                project=project,
                execution_context=execution_context,
                user_id=user_id,
            )
            or user_id
            or None
        )
        result = await TaskService.mutate_task(
            task_data=UpdateTaskDTO(
                task_id=parsed.external_id,
                due_date=resolved_due_date,
                clear_due_date=False,
                project_id=project_id,
            ),
            project=project,
            updated_by=actor_email,
            source_label="promarshal",
        )
        task = serialize_task_like(result.task)
        self._mark_task_recommendation_dirty(
            project_id=project_id,
            task_key=task.get("external_id") or parsed.external_id,
        )
        return {
            "ok": True,
            "source": "jira_live",
            "tool_name": self.spec.name,
            "task": task,
            "due_date_updated": bool(result.due_date_updated),
            "brain_sync_succeeded": bool(result.brain_sync_succeeded),
            "webhook_compensation_expected": not bool(result.brain_sync_succeeded),
            "committed_action": {
                "summary": f"Updated due date for {parsed.external_id}",
                "tool_name": self.spec.name,
                "external_id": parsed.external_id,
            },
        }


class JiraClearDueDateTool(_BaseJiraTool):
    input_model = JiraClearDueDateInput
    spec = ToolSpec(
        name="jira_clear_due_date",
        description="Clear due date on an existing Jira work item.",
        provider="jira",
        capabilities=["workitem_clear_due_date"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=["jira"],
        requires_confirmation=False,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: JiraClearDueDateInput,
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

        actor_email = (
            self._resolve_actor_email_from_context(
                project=project,
                execution_context=execution_context,
                user_id=user_id,
            )
            or user_id
            or None
        )
        result = await TaskService.mutate_task(
            task_data=UpdateTaskDTO(
                task_id=parsed.external_id,
                clear_due_date=True,
                project_id=project_id,
            ),
            project=project,
            updated_by=actor_email,
            source_label="promarshal",
        )
        task = serialize_task_like(result.task)
        self._mark_task_recommendation_dirty(
            project_id=project_id,
            task_key=task.get("external_id") or parsed.external_id,
        )
        return {
            "ok": True,
            "source": "jira_live",
            "tool_name": self.spec.name,
            "task": task,
            "due_date_updated": bool(result.due_date_updated),
            "brain_sync_succeeded": bool(result.brain_sync_succeeded),
            "webhook_compensation_expected": not bool(result.brain_sync_succeeded),
            "committed_action": {
                "summary": f"Cleared due date for {parsed.external_id}",
                "tool_name": self.spec.name,
                "external_id": parsed.external_id,
            },
        }


class JiraUpdateStartDateTool(_BaseJiraTool):
    input_model = JiraUpdateStartDateInput
    spec = ToolSpec(
        name="jira_update_start_date",
        description="Set or update start date on an existing Jira work item.",
        provider="jira",
        capabilities=["workitem_update_start_date"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=["jira"],
        requires_confirmation=False,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: JiraUpdateStartDateInput,
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

        resolved_start_date, date_error = self._resolve_datetime_from_input(
            raw_value=parsed.start_date,
            timezone_name=str(project.get("timezone") or "UTC").strip() or "UTC",
            field_name="start_date",
        )
        if date_error is not None:
            return date_error

        actor_email = (
            self._resolve_actor_email_from_context(
                project=project,
                execution_context=execution_context,
                user_id=user_id,
            )
            or user_id
            or None
        )
        result = await TaskService.mutate_task(
            task_data=UpdateTaskDTO(
                task_id=parsed.external_id,
                start_date=resolved_start_date,
                clear_start_date=False,
                project_id=project_id,
            ),
            project=project,
            updated_by=actor_email,
            source_label="promarshal",
        )
        task = serialize_task_like(result.task)
        self._mark_task_recommendation_dirty(
            project_id=project_id,
            task_key=task.get("external_id") or parsed.external_id,
        )
        return {
            "ok": True,
            "source": "jira_live",
            "tool_name": self.spec.name,
            "task": task,
            "start_date_updated": bool(result.start_date_updated),
            "brain_sync_succeeded": bool(result.brain_sync_succeeded),
            "webhook_compensation_expected": not bool(result.brain_sync_succeeded),
            "committed_action": {
                "summary": f"Updated start date for {parsed.external_id}",
                "tool_name": self.spec.name,
                "external_id": parsed.external_id,
            },
        }


class JiraClearStartDateTool(_BaseJiraTool):
    input_model = JiraClearStartDateInput
    spec = ToolSpec(
        name="jira_clear_start_date",
        description="Clear start date on an existing Jira work item.",
        provider="jira",
        capabilities=["workitem_clear_start_date"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=["jira"],
        requires_confirmation=False,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: JiraClearStartDateInput,
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

        actor_email = (
            self._resolve_actor_email_from_context(
                project=project,
                execution_context=execution_context,
                user_id=user_id,
            )
            or user_id
            or None
        )
        result = await TaskService.mutate_task(
            task_data=UpdateTaskDTO(
                task_id=parsed.external_id,
                clear_start_date=True,
                project_id=project_id,
            ),
            project=project,
            updated_by=actor_email,
            source_label="promarshal",
        )
        task = serialize_task_like(result.task)
        self._mark_task_recommendation_dirty(
            project_id=project_id,
            task_key=task.get("external_id") or parsed.external_id,
        )
        return {
            "ok": True,
            "source": "jira_live",
            "tool_name": self.spec.name,
            "task": task,
            "start_date_updated": bool(result.start_date_updated),
            "brain_sync_succeeded": bool(result.brain_sync_succeeded),
            "webhook_compensation_expected": not bool(result.brain_sync_succeeded),
            "committed_action": {
                "summary": f"Cleared start date for {parsed.external_id}",
                "tool_name": self.spec.name,
                "external_id": parsed.external_id,
            },
        }


class JiraSearchTasksTool(_BaseJiraTool):
    input_model = JiraSearchTasksInput
    spec = ToolSpec(
        name="jira_search_work_items",
        description=(
            "Find and list specific tasks by topic, assignee, status, or priority. "
            "When the user mentions a topic, theme, or keyword (e.g. 'launch checklist', 'payments', 'auth'), "
            "always pass it as the `keyword` argument — it searches task titles and descriptions. "
            "Use assignee_mode (any|specific|unassigned|me); include assignee_value for specific. "
            "Use this for: listing tasks, finding a task, showing what someone is working on. "
            "For analytical questions (workload, distribution, progress, team overview, stalled, overdue) "
            "use jira_analyze_work_items instead — it returns aggregated summaries, not task lists."
        ),
        provider="jira",
        capabilities=["workitem_search"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=["jira"],
        requires_confirmation=False,
        idempotency=IdempotencyClass.READ_ONLY.value,
        task_read_scope=True,
        lifecycle_status_arg="status",
        lifecycle_non_closed_value=SearchStatus.PENDING.value,
        lifecycle_closed_value=SearchStatus.DONE.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: JiraSearchTasksInput,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        project_id, user_id = self._parse_context(execution_context)
        if not project_id:
            return build_tool_error(
                error_code="missing_project_context",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message="Project context is required for task search.",
                details={"tool_name": self.spec.name},
            )

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
        jira_project_key, jira_site_url, jira_scope_error = self._resolve_required_jira_scope(
            project=project,
            project_id=project_id,
        )
        if jira_scope_error:
            return jira_scope_error

        assignee_mode = self._effective_assignee_mode(parsed)
        include_unassigned = (
            parsed.include_unassigned
            if parsed.include_unassigned is not None
            else assignee_mode == AssigneeMode.UNASSIGNED
        )
        user_message = str(
            execution_context.get("latest_user_message")
            or execution_context.get("user_message")
            or ""
        ).strip()
        self_scope_requested = bool(execution_context.get("self_scope_requested")) or is_task_read_self_scope_request(user_message)
        if self_scope_requested:
            assignee_mode = AssigneeMode.ME
            include_unassigned = False
            print(
                "Cortex jira_search_work_items self-scope guardrail applied: "
                f"project_id={project_id} user_id={user_id}"
            )
        assignee_hint = str(parsed.assignee_value or "").strip().lower() or None
        assignee_email: Optional[str] = None
        assignee_account_id: Optional[str] = None
        assignee_name_exact: Optional[str] = None
        if assignee_mode == AssigneeMode.ME:
            actor_identity = execution_context.get("actor_identity") if isinstance(execution_context.get("actor_identity"), dict) else {}
            actor_identity_email = str(actor_identity.get("canonical_email") or "").strip().lower() or None
            actor_identity_jira_account_id = str(actor_identity.get("jira_account_id") or "").strip() or None
            assignee_account_id = self._resolve_actor_jira_account_id_from_context(
                project=project,
                execution_context=execution_context,
                user_id=user_id,
            )
            if not assignee_account_id and actor_identity_jira_account_id:
                assignee_account_id = actor_identity_jira_account_id
            actor_email = self._resolve_actor_email_from_context(
                project=project,
                execution_context=execution_context,
                user_id=user_id,
            )
            if not actor_email and actor_identity_email:
                actor_email = actor_identity_email
            if not actor_email and not assignee_account_id:
                return build_tool_error(
                    error_code="actor_identity_not_resolved",
                    error_class="validation",
                    retryable=False,
                    http_status=400,
                    user_message=(
                        "I couldn't resolve your assignee identity for `my work items` in this project."
                    ),
                    details={"tool_name": self.spec.name, "project_id": project_id, "user_id": user_id},
                )
            assignee_mode = AssigneeMode.SPECIFIC
            assignee_hint = actor_email.strip().lower() if actor_email else None
            assignee_email = assignee_hint
            if not assignee_account_id:
                assignee_account_id = await resolve_assignee_account_id(
                    project,
                    assignee_email=str(assignee_hint or ""),
                )
        elif assignee_mode == AssigneeMode.SPECIFIC and assignee_hint:
            if "@" in assignee_hint:
                assignee_email = assignee_hint
                assignee_account_id = await resolve_assignee_account_id(
                    project,
                    assignee_email=assignee_hint,
                )
            else:
                resolved_assignee_email = self._resolve_specific_assignee_email(
                    project=project,
                    assignee_hint=assignee_hint,
                )
                if resolved_assignee_email:
                    assignee_email = resolved_assignee_email
                    assignee_account_id = await resolve_assignee_account_id(
                        project,
                        assignee_email=resolved_assignee_email,
                    )
                else:
                    assignee_name_exact = assignee_hint

        freshness_requested = self._freshness_demanded(
            freshness=parsed.freshness,
            freshness_reason=parsed.freshness_reason,
        )
        read_policy = self._resolved_read_source_policy(execution_context)
        effective_mode = str(read_policy.get("effective_mode") or "brain_first").strip().lower()
        if effective_mode not in {"brain_first", "live_first", "brain_only", "live_only"}:
            effective_mode = "brain_first"

        async def _run_brain() -> Dict[str, Any]:
            items = await self._search_brain_tasks(
                project_id=project_id,
                project_context=project,
                jira_project_key=jira_project_key,
                jira_site_url=jira_site_url,
                parsed=parsed,
                assignee_mode=assignee_mode,
                include_unassigned=bool(include_unassigned),
                assignee_hint=assignee_hint,
                assignee_email=assignee_email,
                assignee_account_id=assignee_account_id,
                assignee_name_exact=assignee_name_exact,
            )
            if assignee_mode == AssigneeMode.UNASSIGNED:
                scope_mode = "unassigned_only"
            elif assignee_mode == AssigneeMode.SPECIFIC:
                scope_mode = "assigned_to_specific"
            elif include_unassigned:
                scope_mode = "all_including_unassigned"
            else:
                scope_mode = "assigned_only"
            return {
                "ok": True,
                "tool_name": self.spec.name,
                "source": "brain",
                "freshness_mode": "latest_requested_brain" if freshness_requested else "default",
                "items": items,
                "count": len(items),
                "scope_mode": scope_mode,
                "requested_provider_types": list(parsed.provider_types or []),
                "applied_provider_types": list(parsed.provider_types or []),
                "type_filter_applied": bool(parsed.provider_types),
            }

        async def _run_live(*, fallback_reason: str) -> Dict[str, Any]:
            items = await self._search_jira_live_tasks(
                project=project,
                jira_project_key=jira_project_key,
                parsed=parsed,
                assignee_mode=assignee_mode,
                include_unassigned=bool(include_unassigned),
                assignee_hint=assignee_hint,
                assignee_email=assignee_email,
                assignee_account_id=assignee_account_id,
            )
            if assignee_mode == AssigneeMode.UNASSIGNED:
                scope_mode = "unassigned_only"
            elif assignee_mode == AssigneeMode.SPECIFIC:
                scope_mode = "assigned_to_specific"
            elif include_unassigned:
                scope_mode = "all_including_unassigned"
            else:
                scope_mode = "assigned_only"
            payload: Dict[str, Any] = {
                "ok": True,
                "tool_name": self.spec.name,
                "source": "jira_live",
                "freshness_mode": "latest" if freshness_requested else "fallback_live",
                "items": items,
                "count": len(items),
                "scope_mode": scope_mode,
                "requested_provider_types": list(parsed.provider_types or []),
                "applied_provider_types": list(parsed.provider_types or []),
                "type_filter_applied": bool(parsed.provider_types),
            }
            if fallback_reason:
                payload["fallback_reason"] = fallback_reason
            return payload

        if effective_mode in {"live_only", "live_first"}:
            try:
                return await _run_live(fallback_reason="")
            except Exception as exc:
                if effective_mode == "live_only":
                    return self._build_read_source_unavailable_error(
                        mode=effective_mode,
                        reason=str(exc),
                    )
                print(f"Cortex Jira live search fallback triggered ({self.spec.name}): {exc}")
                try:
                    return await _run_brain()
                except Exception as brain_exc:
                    return self._build_read_source_unavailable_error(
                        mode=effective_mode,
                        reason=str(brain_exc),
                    )

        # brain_first/brain_only
        try:
            return await _run_brain()
        except Exception as exc:
            if effective_mode == "brain_only":
                return self._build_read_source_unavailable_error(
                    mode=effective_mode,
                    reason=str(exc),
                )
            print(f"Cortex Brain search fallback triggered ({self.spec.name}): {exc}")
            return await _run_live(fallback_reason="brain_search_failed")


class JiraAnalyzeTasksTool(_BaseJiraTool):
    input_model = JiraAnalyzeTasksInput
    spec = ToolSpec(
        name="jira_analyze_work_items",
        description=(
            "Analyze the project's task data and return aggregated counts — never a raw task list. "
            "Use this for any analytical or overview question: workload distribution, priority breakdown, "
            "status overview, sprint health, stalled tasks, overdue items, team load balance, "
            "unassigned task count, blocked work, or any question needing counts and trends. "
            "For capacity-risk questions (workload, overburdened, burnout risk), evaluate risk from "
            "assigned open/in-progress work per member; treat unassigned work as allocation pressure "
            "unless explicitly included in capacity scoring. "
            "Returns grouped summaries by assignee, status, priority, plus stalled and overdue details. "
            "Do NOT use this for finding or listing specific tasks — use jira_search_work_items for that."
        ),
        provider="jira",
        capabilities=["workitem_search", "workitem_analyze"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=["jira"],
        requires_confirmation=False,
        idempotency=IdempotencyClass.READ_ONLY.value,
        task_read_scope=True,
    )

    async def _execute_validated(
        self,
        *,
        parsed: JiraAnalyzeTasksInput,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        project_id, user_id = self._parse_context(execution_context)
        if not project_id:
            return build_tool_error(
                error_code="missing_project_context",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message="Project context is required for task analysis.",
                details={"tool_name": self.spec.name},
            )

        project = await load_project(project_id)
        if not project:
            return build_tool_error(
                error_code="project_not_found",
                error_class="not_found",
                retryable=False,
                http_status=404,
                user_message="I couldn't find the active project for this analysis.",
                details={"project_id": project_id, "tool_name": self.spec.name},
            )

        assignee_mode = parsed.assignee_mode or AssigneeMode.ANY
        assignee_hint = str(parsed.assignee_value or "").strip().lower() or None
        assignee_email: Optional[str] = None
        assignee_account_id: Optional[str] = None
        assignee_name_exact: Optional[str] = None

        user_message = str(
            execution_context.get("latest_user_message")
            or execution_context.get("user_message")
            or ""
        ).strip()
        self_scope_requested = bool(execution_context.get("self_scope_requested")) or is_task_read_self_scope_request(user_message)
        if self_scope_requested:
            assignee_mode = AssigneeMode.ME

        if assignee_mode == AssigneeMode.ME:
            actor_identity = execution_context.get("actor_identity") if isinstance(execution_context.get("actor_identity"), dict) else {}
            actor_identity_email = str(actor_identity.get("canonical_email") or "").strip().lower() or None
            actor_identity_jira_account_id = str(actor_identity.get("jira_account_id") or "").strip() or None
            assignee_account_id = self._resolve_actor_jira_account_id_from_context(
                project=project,
                execution_context=execution_context,
                user_id=user_id,
            )
            if not assignee_account_id and actor_identity_jira_account_id:
                assignee_account_id = actor_identity_jira_account_id
            actor_email = self._resolve_actor_email_from_context(
                project=project,
                execution_context=execution_context,
                user_id=user_id,
            )
            if not actor_email and actor_identity_email:
                actor_email = actor_identity_email
            if not actor_email and not assignee_account_id:
                return build_tool_error(
                    error_code="actor_identity_not_resolved",
                    error_class="validation",
                    retryable=False,
                    http_status=400,
                    user_message=(
                        "I couldn't resolve your assignee identity for `my work items` in this project."
                    ),
                    details={"tool_name": self.spec.name, "project_id": project_id, "user_id": user_id},
                )
            assignee_mode = AssigneeMode.SPECIFIC
            assignee_hint = actor_email.strip().lower() if actor_email else None
            assignee_email = assignee_hint
            if not assignee_account_id:
                assignee_account_id = await resolve_assignee_account_id(
                    project,
                    assignee_email=str(assignee_hint or ""),
                )
        elif assignee_mode == AssigneeMode.SPECIFIC and assignee_hint:
            if "@" in assignee_hint:
                assignee_email = assignee_hint
                assignee_account_id = await resolve_assignee_account_id(
                    project,
                    assignee_email=assignee_hint,
                )
            else:
                resolved_assignee_email = self._resolve_specific_assignee_email(
                    project=project,
                    assignee_hint=assignee_hint,
                )
                if resolved_assignee_email:
                    assignee_email = resolved_assignee_email
                    assignee_account_id = await resolve_assignee_account_id(
                        project,
                        assignee_email=resolved_assignee_email,
                    )
                else:
                    assignee_name_exact = assignee_hint

        filters = ListTasksFilters(
            assignee_email=assignee_email if assignee_mode == AssigneeMode.SPECIFIC else None,
            assignee_user_id=(
                assignee_account_id if assignee_mode == AssigneeMode.SPECIFIC else None
            ),
            assignee_account_id=(
                assignee_account_id if assignee_mode == AssigneeMode.SPECIFIC else None
            ),
            assignee_name_exact=(
                str(assignee_name_exact or "").strip() if assignee_mode == AssigneeMode.SPECIFIC else None
            ),
            provider_types=list(parsed.provider_types or []),
            due_date_mode=str(parsed.due_date_mode.value if hasattr(parsed.due_date_mode, "value") else parsed.due_date_mode or "").strip().lower() or None,
            is_closed=None if parsed.include_closed else False,
            limit=500,
        )
        tasks = await TaskService.list_tasks(
            project_id=project_id,
            filters=filters,
            provider_hint="jira",
            project_context=project,
        )
        task_dicts = [serialize_task_like(task) for task in tasks]
        if assignee_mode == AssigneeMode.UNASSIGNED:
            task_dicts = [
                item
                for item in task_dicts
                if not str(item.get("assignee_email") or "").strip()
                and not str(item.get("assignee_name") or "").strip()
            ]

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stale_threshold = parsed.stale_days

        by_assignee: Dict[str, Dict[str, Any]] = {}
        by_status: Dict[str, int] = {}
        by_priority: Dict[str, int] = {}
        stalled: List[Dict[str, Any]] = []
        overdue: List[Dict[str, Any]] = []
        capacity_by_assignee: Dict[str, Dict[str, int]] = {}
        at_risk_threshold = int(parsed.at_risk_in_progress_threshold)
        include_unassigned_in_capacity = bool(parsed.include_unassigned_in_capacity)

        for item in task_dicts:
            assignee_name = str(item.get("assignee_name") or "").strip() or None
            assignee_email = str(item.get("assignee_email") or "").strip() or None
            assignee_label = assignee_name or assignee_email or "Unassigned"

            if assignee_label not in by_assignee:
                by_assignee[assignee_label] = {"total": 0, "by_status": {}, "overdue_count": 0}
            by_assignee[assignee_label]["total"] += 1

            raw_status = str(item.get("status") or "unknown").strip().lower()
            normalized_status = STATUS_ALIASES.get(
                raw_status.replace("-", "_").replace(" ", "_"),
                raw_status.replace("-", "_").replace(" ", "_"),
            )
            by_status[normalized_status] = by_status.get(normalized_status, 0) + 1
            by_assignee[assignee_label]["by_status"][normalized_status] = (
                by_assignee[assignee_label]["by_status"].get(normalized_status, 0) + 1
            )

            raw_priority = str(item.get("priority") or "unknown").strip().lower()
            by_priority[raw_priority] = by_priority.get(raw_priority, 0) + 1

            is_closed = bool(item.get("is_closed"))
            include_in_capacity = (
                not is_closed
                and (assignee_label != "Unassigned" or include_unassigned_in_capacity)
            )
            if include_in_capacity:
                bucket = capacity_by_assignee.setdefault(
                    assignee_label,
                    {
                        "open_count": 0,
                        "in_progress_count": 0,
                        "stalled_count": 0,
                        "overdue_count": 0,
                    },
                )
                bucket["open_count"] += 1
                if normalized_status == SearchStatus.IN_PROGRESS.value or "progress" in normalized_status:
                    bucket["in_progress_count"] += 1

            # Stalled: in_progress and not updated within stale_threshold days
            if not is_closed and (
                normalized_status == SearchStatus.IN_PROGRESS.value or "progress" in normalized_status
            ):
                updated_at = item.get("updated_at")
                if updated_at:
                    try:
                        if isinstance(updated_at, str):
                            updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                        if hasattr(updated_at, "tzinfo") and updated_at.tzinfo is not None:
                            updated_at = updated_at.astimezone(timezone.utc).replace(tzinfo=None)
                        days_stale = (now - updated_at).days
                        if days_stale >= stale_threshold:
                            stalled.append({
                                "external_id": item.get("external_id"),
                                "title": item.get("title"),
                                "assignee": assignee_label,
                                "days_since_update": days_stale,
                            })
                            if include_in_capacity:
                                capacity_by_assignee[assignee_label]["stalled_count"] += 1
                    except Exception:
                        pass

            # Overdue: has due_date in the past and not closed
            if not is_closed:
                due_date = item.get("due_date")
                if due_date:
                    try:
                        if isinstance(due_date, str):
                            due_date = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
                        if hasattr(due_date, "tzinfo") and due_date.tzinfo is not None:
                            due_date = due_date.astimezone(timezone.utc).replace(tzinfo=None)
                        if due_date < now:
                            overdue.append({
                                "external_id": item.get("external_id"),
                                "title": item.get("title"),
                                "assignee": assignee_label,
                                "due_date": str(item.get("due_date")),
                                "days_overdue": (now - due_date).days,
                            })
                            by_assignee[assignee_label]["overdue_count"] = (
                                int(by_assignee[assignee_label].get("overdue_count") or 0) + 1
                            )
                            if include_in_capacity:
                                capacity_by_assignee[assignee_label]["overdue_count"] += 1
                    except Exception:
                        pass

        unassigned_count = by_assignee.get("Unassigned", {}).get("total", 0)
        by_assignee_list = sorted(
            [{"assignee": k, **v} for k, v in by_assignee.items()],
            key=lambda x: x["total"],
            reverse=True,
        )
        capacity_by_assignee_list = sorted(
            [{"assignee": k, **v} for k, v in capacity_by_assignee.items()],
            key=lambda x: (x.get("in_progress_count", 0), x.get("open_count", 0)),
            reverse=True,
        )
        at_risk_assignees = [
            row for row in capacity_by_assignee_list
            if int(row.get("in_progress_count", 0)) >= at_risk_threshold
        ]
        unassigned_status_breakdown = by_assignee.get("Unassigned", {}).get("by_status", {})

        return {
            "ok": True,
            "source": "brain",
            "tool_name": self.spec.name,
            "scope": "all_tasks" if parsed.include_closed else "open_tasks",
            "scope_work_items": "all_work_items" if parsed.include_closed else "open_work_items",
            "scope_mode": (
                "unassigned_only"
                if assignee_mode == AssigneeMode.UNASSIGNED
                else "assigned_to_specific"
                if assignee_mode == AssigneeMode.SPECIFIC
                else "all_including_unassigned"
            ),
            "total_tasks": len(task_dicts),
            "total_work_items": len(task_dicts),
            "by_assignee": by_assignee_list,
            "by_status": by_status,
            "by_priority": by_priority,
            "unassigned_count": unassigned_count,
            "stalled_count": len(stalled),
            "stalled_tasks": stalled,
            "stalled_work_items": stalled,
            "overdue_count": len(overdue),
            "overdue_tasks": overdue,
            "overdue_work_items": overdue,
            "overdue_is_subset_of_open": True,
            "capacity_basis": (
                "assigned_open_tasks_only"
                if not include_unassigned_in_capacity
                else "open_tasks_including_unassigned"
            ),
            "capacity_threshold_in_progress": at_risk_threshold,
            "capacity_by_assignee": capacity_by_assignee_list,
            "at_risk_assignees": at_risk_assignees,
            "allocation_pressure": {
                "unassigned_total": int(unassigned_count),
                "unassigned_in_progress": int(unassigned_status_breakdown.get("in_progress", 0)),
                "unassigned_todo": int(unassigned_status_breakdown.get("todo", 0)),
                "unassigned_by_status": unassigned_status_breakdown,
            },
        }


class JiraAssignTaskTool(_BaseJiraTool):
    input_model = JiraAssignTaskInput
    spec = ToolSpec(
        name="jira_assign_work_item",
        description="Assign or reassign an existing Jira work item (for example PRM-80). Do not use for ACTION-<n> action-item refs.",
        provider="jira",
        capabilities=["workitem_assign"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=["jira"],
        requires_confirmation=True,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: JiraAssignTaskInput,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        if str(parsed.external_id or "").strip().lower().startswith("action-"):
            return build_tool_error(
                error_code="invalid_target_type",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message=(
                    "That reference looks like an action item (`ACTION-*`). "
                    "Use action-item update/assign instead of Jira assign."
                ),
                details={"tool_name": self.spec.name, "external_id": parsed.external_id},
            )
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

        effective_assignee_hint, self_alias_error = self._resolve_assignee_hint_for_write(
            assignee_hint=str(parsed.assignee),
            project=project,
            execution_context=execution_context,
            user_id=user_id,
        )
        if self_alias_error is not None:
            return self_alias_error

        assignee_resolution = await resolve_assignee_identity(
            project,
            assignee_hint=str(effective_assignee_hint or parsed.assignee),
        )
        assignee_account_id = assignee_resolution.account_id
        if not assignee_account_id:
            return self._build_assignee_resolution_error(
                assignee_hint=str(effective_assignee_hint or parsed.assignee),
                resolution=assignee_resolution,
                execution_context=execution_context,
            )

        resolved_assignee_label = (
            assignee_resolution.resolved_email
            or assignee_resolution.resolved_name
            or str(parsed.assignee).strip()
        )

        actor_email = (
            self._resolve_actor_email_from_context(
                project=project,
                execution_context=execution_context,
                user_id=user_id,
            )
            or user_id
            or None
        )
        result = await TaskService.mutate_task(
            task_data=UpdateTaskDTO(
                task_id=parsed.external_id,
                status=None,
                comment=None,
                assignee_account_id=assignee_account_id,
                project_id=project_id,
            ),
            project=project,
            updated_by=actor_email,
            source_label="promarshal",
        )
        task = serialize_task_like(result.task)
        owner_insight = self._owner_mapping_insight(
            resolution=assignee_resolution,
            execution_context=execution_context,
        )
        summary = f"Assigned {parsed.external_id} to {resolved_assignee_label}"
        if owner_insight:
            summary = f"{summary}. {owner_insight}"
        self._mark_task_recommendation_dirty(
            project_id=project_id,
            task_key=task.get("external_id") or parsed.external_id,
        )

        payload: Dict[str, Any] = {
            "ok": True,
            "source": "jira_live",
            "tool_name": self.spec.name,
            "task": task,
            "assignee_updated": bool(result.assignee_updated),
            "brain_sync_succeeded": bool(result.brain_sync_succeeded),
            "webhook_compensation_expected": not bool(result.brain_sync_succeeded),
            "committed_action": {
                "summary": summary,
                "tool_name": self.spec.name,
                "external_id": parsed.external_id,
            },
        }
        if owner_insight:
            payload["owner_insight"] = owner_insight
        return payload


class JiraUpdateParentTool(_BaseJiraTool):
    input_model = JiraUpdateParentInput
    spec = ToolSpec(
        name="jira_update_parent_work_item",
        description="Link an existing Jira work item under a parent work item (for example child PRM-120 under parent PRM-2).",
        provider="jira",
        capabilities=["workitem_update_parent"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=["jira"],
        requires_confirmation=True,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: JiraUpdateParentInput,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        if str(parsed.external_id or "").strip().lower().startswith("action-"):
            return build_tool_error(
                error_code="invalid_target_type",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message=(
                    "That reference looks like an action item (`ACTION-*`). "
                    "Use action-item update instead of Jira parent linking."
                ),
                details={"tool_name": self.spec.name, "external_id": parsed.external_id},
            )
        if str(parsed.parent_external_id or "").strip().lower().startswith("action-"):
            return build_tool_error(
                error_code="invalid_parent_type",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message=(
                    "The parent reference looks like an action item (`ACTION-*`). "
                    "Choose a Jira work item key (for example `PRM-123`) as parent."
                ),
                details={"tool_name": self.spec.name, "parent_external_id": parsed.parent_external_id},
            )
        child_key = str(parsed.external_id or "").strip().upper()
        parent_key = str(parsed.parent_external_id or "").strip().upper()
        if child_key == parent_key:
            return build_tool_error(
                error_code="invalid_parent_link",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message="A work item cannot be linked as its own parent.",
                details={"tool_name": self.spec.name, "external_id": child_key, "parent_external_id": parent_key},
            )

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
        _jira_project_key, _jira_site_url, jira_scope_error = self._resolve_required_jira_scope(
            project=project,
            project_id=project_id,
        )
        if jira_scope_error:
            return jira_scope_error
        parent_reference = await self._resolve_parent_reference_state(
            project=project,
            parent_external_id=parent_key,
        )
        if not bool((parent_reference or {}).get("exists")):
            return build_tool_error(
                error_code="invalid_parent_reference",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message=build_parent_reference_not_available_message(parent_key),
                details={
                    "tool_name": self.spec.name,
                    "external_id": child_key,
                    "parent_external_id": parent_key,
                    "parent_lookup_source": str(parent_reference.get("source") or "").strip() or "unverified",
                    "parent_lookup_reason": str(parent_reference.get("reason") or "").strip() or None,
                },
            )
        adapter = JiraAdapter(project)
        await adapter.ensure_token_valid()

        try:
            child_issue = await jira_oauth_service.get_issue(
                access_token=adapter.access_token,
                cloud_id=adapter.cloud_id,
                issue_key=child_key,
            )
            parent_issue = await jira_oauth_service.get_issue(
                access_token=adapter.access_token,
                cloud_id=adapter.cloud_id,
                issue_key=parent_key,
            )
        except Exception:
            return build_tool_error(
                error_code="work_item_lookup_failed",
                error_class="integration",
                retryable=True,
                http_status=502,
                user_message="I couldn't validate one of the work item keys in Jira right now.",
                details={"tool_name": self.spec.name, "external_id": child_key, "parent_external_id": parent_key},
            )

        normalizer = JiraNormalizer(cloud_id=adapter.cloud_id, site_url=adapter.site_url)
        child_task = serialize_task_like(normalizer.normalize_task(child_issue))
        parent_task = serialize_task_like(normalizer.normalize_task(parent_issue))

        try:
            parent_rule_filter = await resolve_parent_rule_filter(
                project=project,
                create_payload={
                    "provider": "jira",
                    "provider_type": child_task.get("provider_type"),
                    "work_item_type": child_task.get("work_item_type"),
                },
            )
        except Exception as exc:
            print(f"Cortex parent-rule resolution failed (jira_update_parent_work_item): {exc}")
            parent_rule_filter = {}
        if not isinstance(parent_rule_filter, dict):
            parent_rule_filter = {}
        allowed_types = list((parent_rule_filter or {}).get("allowed_parent_provider_types") or [])
        if allowed_types:
            allowed_tokens = {
                re.sub(r"[^a-z0-9]+", "", str(item or "").strip().lower())
                for item in allowed_types
                if str(item or "").strip()
            }
            parent_token = re.sub(
                r"[^a-z0-9]+", "",
                str(parent_task.get("provider_type") or "").strip().lower(),
            )
            if parent_token not in allowed_tokens:
                allowed_display = ", ".join(str(item or "").strip() for item in allowed_types if str(item or "").strip())
                allowed_text = allowed_display or "none"
                return build_tool_error(
                    error_code="invalid_parent_link",
                    error_class="validation",
                    retryable=False,
                    http_status=400,
                    user_message=(
                        f"Cannot map {child_key} under {parent_key}: parent type "
                        f"'{str(parent_task.get('provider_type') or 'Unknown').strip()}' is not allowed. "
                        f"Allowed parent types: {allowed_text}."
                    ),
                    details={
                        "tool_name": self.spec.name,
                        "external_id": child_key,
                        "parent_external_id": parent_key,
                        "allowed_parent_provider_types": allowed_types,
                        "parent_provider_type": parent_task.get("provider_type"),
                    },
                )

        actor_email = (
            self._resolve_actor_email_from_context(
                project=project,
                execution_context=execution_context,
                user_id=user_id,
            )
            or user_id
            or None
        )
        try:
            result = await TaskService.mutate_task(
                task_data=UpdateTaskDTO(
                    task_id=child_key,
                    parent_external_id=parent_key,
                    project_id=project_id,
                ),
                project=project,
                updated_by=actor_email,
                source_label="promarshal",
            )
        except ValueError as exc:
            return build_tool_error(
                error_code="invalid_parent_link",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message=str(exc),
                details={"tool_name": self.spec.name, "external_id": child_key, "parent_external_id": parent_key},
            )

        task = serialize_task_like(result.task)
        if not bool(result.parent_updated):
            return build_tool_error(
                error_code="parent_update_failed",
                error_class="integration",
                retryable=True,
                http_status=502,
                user_message=f"I couldn't link {child_key} under {parent_key} right now.",
                details={"tool_name": self.spec.name, "external_id": child_key, "parent_external_id": parent_key},
            )
        self._mark_task_recommendation_dirty(
            project_id=project_id,
            task_key=task.get("external_id") or child_key,
        )
        return {
            "ok": True,
            "source": "jira_live",
            "tool_name": self.spec.name,
            "task": task,
            "parent_updated": bool(result.parent_updated),
            "parent_external_id": parent_key,
            "brain_sync_succeeded": bool(result.brain_sync_succeeded),
            "webhook_compensation_expected": not bool(result.brain_sync_succeeded),
            "committed_action": {
                "summary": f"Mapped {child_key} under {parent_key}",
                "tool_name": self.spec.name,
                "external_id": child_key,
            },
        }


class JiraGetTaskDetailsTool(_BaseJiraTool):
    input_model = JiraGetTaskDetailsInput
    spec = ToolSpec(
        name="jira_get_work_item_details",
        description="Get work item details with Brain-first routing and Jira-live fallback.",
        provider="jira",
        capabilities=["workitem_get_details"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=["jira"],
        requires_confirmation=False,
        idempotency=IdempotencyClass.READ_ONLY.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: JiraGetTaskDetailsInput,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        project_id, _user_id = self._parse_context(execution_context)
        if not project_id:
            return build_tool_error(
                error_code="missing_project_context",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message="Project context is required for work item details.",
                details={"tool_name": self.spec.name},
            )

        freshness_requested = self._freshness_demanded(
            freshness=parsed.freshness,
            freshness_reason=parsed.freshness_reason,
        )
        read_policy = self._resolved_read_source_policy(execution_context)
        effective_mode = str(read_policy.get("effective_mode") or "brain_first").strip().lower()
        if effective_mode not in {"brain_first", "live_first", "brain_only", "live_only"}:
            effective_mode = "brain_first"

        async def _run_brain() -> Dict[str, Any]:
            collection = get_brain_collection(project_id)
            doc = await collection.find_one(
                {"integration_type": "jira", "external_id": parsed.external_id}
            )
            if doc:
                return {
                    "ok": True,
                    "tool_name": self.spec.name,
                    "source": "brain",
                    "freshness_mode": "latest_requested_brain" if freshness_requested else "default",
                    "task": self._serialize_brain_doc(doc),
                }
            raise LookupError("brain_entity_missing")

        async def _run_live(*, fallback_reason: str) -> Dict[str, Any]:
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

            adapter = JiraAdapter(project)
            await adapter.ensure_token_valid()
            issue = await jira_oauth_service.get_issue(
                access_token=adapter.access_token,
                cloud_id=adapter.cloud_id,
                issue_key=parsed.external_id,
            )
            normalizer = JiraNormalizer(cloud_id=adapter.cloud_id, site_url=adapter.site_url)
            task = serialize_task_like(normalizer.normalize_task(issue))
            payload: Dict[str, Any] = {
                "ok": True,
                "tool_name": self.spec.name,
                "source": "jira_live",
                "freshness_mode": "latest" if freshness_requested else "fallback_live",
                "task": task,
            }
            if fallback_reason:
                payload["fallback_reason"] = fallback_reason
            return payload

        if effective_mode in {"live_only", "live_first"}:
            try:
                return await _run_live(fallback_reason="")
            except Exception as exc:
                if effective_mode == "live_only":
                    return self._build_read_source_unavailable_error(
                        mode=effective_mode,
                        reason=str(exc),
                    )
                print(f"Cortex Jira live read fallback triggered for {parsed.external_id}: {exc}")
                try:
                    return await _run_brain()
                except Exception as brain_exc:
                    return self._build_read_source_unavailable_error(
                        mode=effective_mode,
                        reason=str(brain_exc),
                    )

        # brain_first/brain_only
        try:
            return await _run_brain()
        except Exception as exc:
            if effective_mode == "brain_only":
                return self._build_read_source_unavailable_error(
                    mode=effective_mode,
                    reason=str(exc),
                )
            print(f"Cortex Brain read fallback triggered for {parsed.external_id}: {exc}")
            return await _run_live(fallback_reason=str(exc) or "brain_lookup_failed")


def get_launch_jira_tools() -> List[CortexTool]:
    """Return launch Jira toolset."""
    return [
        JiraCreateTaskTool(),
        JiraUpdateStatusTool(),
        JiraAddCommentTool(),
        JiraUpdateDescriptionTool(),
        JiraUpdateDueDateTool(),
        JiraClearDueDateTool(),
        JiraUpdateStartDateTool(),
        JiraClearStartDateTool(),
        JiraAssignTaskTool(),
        JiraUpdateParentTool(),
        JiraSearchTasksTool(),
        JiraAnalyzeTasksTool(),
        JiraGetTaskDetailsTool(),
    ]
