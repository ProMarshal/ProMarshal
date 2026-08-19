"""Cortex launch Brain toolset with strict bounded input schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Type

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator, model_validator

from app.core.database import get_brain_collection
from app.cortex.tools.base import CortexTool, IdempotencyClass, MemberRole, ToolSpec
from app.cortex.tools.error_envelope import (
    build_tool_error,
    build_validation_error,
)
from app.cortex.tools.runtime_helpers import serialize_task_like
from app.cortex.tools.schemas import (
    EXTERNAL_ID_PATTERN,
    DateRange,
    EntityType,
    RelationshipType,
    Status,
    normalize_text,
    validate_bounded_keyword,
)


class BrainSearchEntitiesInput(BaseModel):
    """Validated input for brain_search_entities."""

    model_config = ConfigDict(extra="forbid")

    entity_type: Optional[EntityType] = None
    status: Optional[Status] = None
    assignee: Optional[EmailStr] = None
    keyword: Optional[str] = Field(default=None, max_length=200)
    date_range: Optional[DateRange] = None
    max_results: int = Field(default=20, ge=1, le=50)

    @field_validator("keyword", mode="before")
    @classmethod
    def normalize_keyword(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        keyword = validate_bounded_keyword(str(value))
        return keyword or None


class BrainGetEntityInput(BaseModel):
    """Validated input for brain_get_entity (one-of external_id/entity_id)."""

    model_config = ConfigDict(extra="forbid")

    external_id: Optional[str] = Field(default=None, pattern=EXTERNAL_ID_PATTERN)
    entity_id: Optional[str] = Field(default=None, min_length=1, max_length=200)

    @field_validator("external_id", mode="before")
    @classmethod
    def normalize_external_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value)).upper()
        return text or None

    @field_validator("entity_id", mode="before")
    @classmethod
    def normalize_entity_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value))
        return text or None

    @model_validator(mode="after")
    def validate_one_of(self) -> "BrainGetEntityInput":
        if bool(self.external_id) == bool(self.entity_id):
            raise ValueError("Exactly one of external_id or entity_id is required")
        return self


class BrainQueryRelationshipsInput(BaseModel):
    """Validated input for brain_query_relationships."""

    model_config = ConfigDict(extra="forbid")

    entity_key: str = Field(min_length=1, max_length=200)
    relationship_type: RelationshipType
    max_results: int = Field(default=20, ge=1, le=50)

    @field_validator("entity_key", mode="before")
    @classmethod
    def normalize_entity_key(cls, value: str) -> str:
        text = normalize_text(str(value))
        if not text:
            raise ValueError("entity_key is required")
        return text


class _BaseBrainTool(CortexTool):
    """Base Brain tool with schema validation + integration error envelope."""

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
                f"Cortex Brain validation error ({self.spec.name}): "
                f"errors={exc.errors()} args={args or {}}"
            )
            return build_validation_error(tool_name=self.spec.name, error=exc)

        try:
            return await self._execute_validated(
                parsed=parsed,
                execution_context=execution_context or {},
            )
        except Exception as exc:
            print(f"Cortex Brain tool execution failed ({self.spec.name}): {exc}")
            return build_tool_error(
                error_code="brain_tool_execution_failed",
                error_class="integration",
                retryable=False,
                http_status=500,
                user_message=f"I couldn't complete `{self.spec.name}` right now.",
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
            user_message="This action is not available yet.",
            details={"tool_name": self.spec.name},
        )

    def _project_id(self, execution_context: Dict[str, Any]) -> str:
        return str(execution_context.get("project_id") or "").strip()

    def _serialize_doc(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(doc)
        payload["_id"] = str(payload.get("_id"))
        return serialize_task_like(payload)

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


class BrainSearchEntitiesTool(_BaseBrainTool):
    input_model = BrainSearchEntitiesInput
    spec = ToolSpec(
        name="brain_search_entities",
        description="Search Brain entities with bounded filters.",
        provider="brain",
        capabilities=["entity_search", "workitem_search"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=[],
        requires_confirmation=False,
        idempotency=IdempotencyClass.READ_ONLY.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: BrainSearchEntitiesInput,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        project_id = self._project_id(execution_context)
        if not project_id:
            return build_tool_error(
                error_code="missing_project_context",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message="Project context is required for Brain search.",
                details={"tool_name": self.spec.name},
            )

        collection = get_brain_collection(project_id)
        query: Dict[str, Any] = {}
        if parsed.entity_type:
            query["entity_type"] = parsed.entity_type.value
        if parsed.status:
            query["status"] = parsed.status.value
        if parsed.assignee:
            query["assignee_email"] = str(parsed.assignee)
        if parsed.keyword:
            query["$or"] = [
                {"title": {"$regex": parsed.keyword, "$options": "i"}},
                {"external_id": {"$regex": parsed.keyword, "$options": "i"}},
                {"project_name": {"$regex": parsed.keyword, "$options": "i"}},
            ]

        cursor = collection.find(query).sort("updated_at", -1).limit(parsed.max_results)
        docs = await cursor.to_list(length=parsed.max_results)
        serialized = [self._serialize_doc(doc) for doc in docs]
        filtered = [
            item
            for item in serialized
            if self._in_date_range(value=item.get("updated_at"), date_range=parsed.date_range)
        ]
        return {
            "ok": True,
            "tool_name": self.spec.name,
            "source": "brain",
            "items": filtered,
            "count": len(filtered),
        }


class BrainGetEntityTool(_BaseBrainTool):
    input_model = BrainGetEntityInput
    spec = ToolSpec(
        name="brain_get_entity",
        description="Fetch one Brain entity by external_id or internal id.",
        provider="brain",
        capabilities=["entity_get", "workitem_get_details"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=[],
        requires_confirmation=False,
        idempotency=IdempotencyClass.READ_ONLY.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: BrainGetEntityInput,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        project_id = self._project_id(execution_context)
        if not project_id:
            return build_tool_error(
                error_code="missing_project_context",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message="Project context is required for Brain lookup.",
                details={"tool_name": self.spec.name},
            )

        collection = get_brain_collection(project_id)
        doc: Optional[Dict[str, Any]] = None
        if parsed.external_id:
            doc = await collection.find_one({"external_id": parsed.external_id})
        elif parsed.entity_id:
            entity_id = parsed.entity_id
            doc = await collection.find_one({"entity_id": entity_id})
            if doc is None and ObjectId.is_valid(entity_id):
                doc = await collection.find_one({"_id": ObjectId(entity_id)})

        if doc is None:
            return build_tool_error(
                error_code="entity_not_found",
                error_class="not_found",
                retryable=False,
                http_status=404,
                user_message="I couldn't find that entity in Brain.",
                details={
                    "tool_name": self.spec.name,
                    "external_id": parsed.external_id,
                    "entity_id": parsed.entity_id,
                },
            )

        return {
            "ok": True,
            "tool_name": self.spec.name,
            "source": "brain",
            "entity": self._serialize_doc(doc),
        }


class BrainQueryRelationshipsTool(_BaseBrainTool):
    input_model = BrainQueryRelationshipsInput
    spec = ToolSpec(
        name="brain_query_relationships",
        description="Query allowed graph relationships for an entity key.",
        provider="brain",
        capabilities=["entity_relationship_query"],
        role_requirements=[MemberRole.OWNER.value, MemberRole.ADMIN.value, MemberRole.MEMBER.value],
        integration_requirements=[],
        requires_confirmation=False,
        idempotency=IdempotencyClass.READ_ONLY.value,
    )

    async def _execute_validated(
        self,
        *,
        parsed: BrainQueryRelationshipsInput,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return build_tool_error(
            error_code="relationships_unavailable",
            error_class="integration",
            retryable=False,
            http_status=501,
            user_message="Relationship queries are not available in this launch slice.",
            details={
                "tool_name": self.spec.name,
                "entity_key": parsed.entity_key,
                "relationship_type": parsed.relationship_type.value,
            },
        )


def get_launch_brain_tools() -> List[CortexTool]:
    """Return launch Brain toolset (3 tools)."""
    return [
        BrainSearchEntitiesTool(),
        BrainGetEntityTool(),
        BrainQueryRelationshipsTool(),
    ]
