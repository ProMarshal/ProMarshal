"""Cortex Team Poll tools for owner-initiated poll operations."""

from __future__ import annotations

from typing import Any, Dict, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.config import settings
from app.core.database import get_database
from app.cortex.tools.base import CortexTool, IdempotencyClass, MemberRole, ToolSpec
from app.cortex.tools.error_envelope import build_tool_error, build_validation_error
from app.cortex.tools.runtime_helpers import load_project
from app.cortex.tools.schemas import OPERATION_ID_PATTERN, normalize_operation_id, normalize_text
from app.team_poll.orchestrator import close_owner_poll, create_team_poll, get_owner_poll_status


class TeamPollCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_text: Optional[str] = Field(default=None, max_length=300)
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)

    @field_validator("operation_id", mode="before")
    @classmethod
    def normalize_op_id(cls, value: str) -> str:
        return normalize_operation_id(str(value))

    @field_validator("question_text", mode="before")
    @classmethod
    def normalize_question_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value))
        return text or None


class TeamPollStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poll_id: Optional[str] = Field(default=None, max_length=120)

    @field_validator("poll_id", mode="before")
    @classmethod
    def normalize_poll_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value))
        return text or None


class TeamPollCloseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poll_id: Optional[str] = Field(default=None, max_length=120)
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)

    @field_validator("operation_id", mode="before")
    @classmethod
    def normalize_op_id(cls, value: str) -> str:
        return normalize_operation_id(str(value))

    @field_validator("poll_id", mode="before")
    @classmethod
    def normalize_poll_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = normalize_text(str(value))
        return text or None


class _BaseTeamPollTool(CortexTool):
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
            return build_validation_error(tool_name=self.spec.name, error=exc)

        if not bool(getattr(settings, "team_poll_enabled", True)):
            return build_tool_error(
                error_code="feature_disabled",
                error_class="policy",
                retryable=False,
                http_status=403,
                user_message="Team poll is currently disabled for this workspace.",
                details={"tool_name": self.spec.name},
            )

        try:
            return await self._execute_validated(parsed=parsed, execution_context=execution_context or {})
        except Exception as exc:
            return build_tool_error(
                error_code="team_poll_tool_execution_failed",
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
            error_class="internal",
            retryable=False,
            http_status=500,
            user_message=f"Tool `{self.spec.name}` is not implemented.",
            details={"tool_name": self.spec.name},
        )

    def _parse_context(self, execution_context: Dict[str, Any]) -> tuple[str, str]:
        project_id = str(execution_context.get("project_id") or "").strip()
        user_id = str(execution_context.get("user_id") or "").strip()
        return project_id, user_id

    def _resolve_owner_member(
        self,
        *,
        project: Dict[str, Any],
        user_id: str,
        execution_context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        normalized_user_id = str(user_id or "").strip()
        actor_email = str(
            (execution_context.get("actor_identity") or {}).get("canonical_email")
            or execution_context.get("actor_email")
            or ""
        ).strip().lower()
        for member in project.get("members") or []:
            if not isinstance(member, dict):
                continue
            if str(member.get("status") or "").strip().lower() != "active":
                continue
            member_user_id = str(member.get("user_id") or "").strip()
            member_email = str(member.get("email") or "").strip().lower()
            if normalized_user_id and member_user_id == normalized_user_id:
                return member
            if actor_email and member_email and member_email == actor_email:
                return member
        return None

    def _fallback_workspace_id(
        self,
        *,
        project: Dict[str, Any],
        execution_context: Dict[str, Any],
    ) -> str:
        for key in ("workspace_id", "team_id", "slack_team_id"):
            value = str(execution_context.get(key) or "").strip()
            if value:
                return value
        return str((((project.get("integrations") or {}).get("slack") or {}).get("team_id")) or "").strip()


class TeamPollCreateTool(_BaseTeamPollTool):
    spec = ToolSpec(
        name="team_poll_create",
        description="Start a team poll and ask active members for updates.",
        provider="promarshal",
        capabilities=["broadcast_poll"],
        role_requirements=[MemberRole.OWNER.value],
        integration_requirements=["slack"],
        requires_confirmation=True,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )
    input_model = TeamPollCreateInput

    async def _execute_validated(
        self,
        *,
        parsed: TeamPollCreateInput,
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
                user_message="Project context is missing. Please select an active project and try again.",
                details={"project_id": project_id, "tool_name": self.spec.name},
            )
        owner_member = self._resolve_owner_member(project=project, user_id=user_id, execution_context=execution_context)
        if not owner_member:
            return build_tool_error(
                error_code="owner_member_not_resolved",
                error_class="permission",
                retryable=False,
                http_status=403,
                user_message="Only project owners can start a team poll in this workspace.",
                details={"project_id": project_id, "tool_name": self.spec.name},
            )

        question_text = str(parsed.question_text or "").strip() or str(
            execution_context.get("latest_user_message")
            or execution_context.get("user_message")
            or ""
        ).strip()
        if not question_text:
            return build_tool_error(
                error_code="missing_question_text",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message="Please provide the poll question.",
                details={"tool_name": self.spec.name},
            )
        workspace_id = self._fallback_workspace_id(project=project, execution_context=execution_context)
        db = get_database()
        if db is None:
            return build_tool_error(
                error_code="database_unavailable",
                error_class="integration",
                retryable=True,
                http_status=503,
                user_message="Team poll service is temporarily unavailable.",
                details={"tool_name": self.spec.name},
            )
        result = await create_team_poll(
            db,
            project=project,
            owner_member=owner_member,
            question_text=question_text,
            workspace_id=workspace_id,
        )
        if not bool(result.get("ok")):
            return build_tool_error(
                error_code="team_poll_create_failed",
                error_class="integration",
                retryable=False,
                http_status=500,
                user_message="I couldn't start a team poll from that request.",
                details={"tool_name": self.spec.name, "reason": str(result.get("reason") or "create_failed")},
            )
        poll_id = str(result.get("poll_id") or "").strip()
        question = str(result.get("question_text") or "").strip()
        return {
            "ok": True,
            "source": "team_poll",
            "poll_id": poll_id,
            "question_text": question,
            "delivery": result.get("delivery") or {},
            "summary": f"Team poll started ({poll_id}): {question}" if poll_id else "Team poll started.",
            "committed_action": {
                "summary": f"Started team poll {poll_id}" if poll_id else "Started team poll",
                "tool_name": self.spec.name,
                "poll_id": poll_id or None,
            },
        }


class TeamPollStatusTool(_BaseTeamPollTool):
    spec = ToolSpec(
        name="team_poll_status",
        description="Get status of the active team poll or a specific poll id.",
        provider="promarshal",
        capabilities=["broadcast_poll"],
        role_requirements=[MemberRole.OWNER.value],
        integration_requirements=["slack"],
        requires_confirmation=False,
        idempotency=IdempotencyClass.READ_ONLY.value,
    )
    input_model = TeamPollStatusInput

    async def _execute_validated(
        self,
        *,
        parsed: TeamPollStatusInput,
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
                user_message="Project context is missing. Please select an active project and try again.",
                details={"project_id": project_id, "tool_name": self.spec.name},
            )
        owner_member = self._resolve_owner_member(project=project, user_id=user_id, execution_context=execution_context)
        if not owner_member:
            return build_tool_error(
                error_code="owner_member_not_resolved",
                error_class="permission",
                retryable=False,
                http_status=403,
                user_message="Only project owners can check team poll status here.",
                details={"project_id": project_id, "tool_name": self.spec.name},
            )
        owner_user_id = str(owner_member.get("user_id") or owner_member.get("email") or "").strip()
        db = get_database()
        if db is None:
            return build_tool_error(
                error_code="database_unavailable",
                error_class="integration",
                retryable=True,
                http_status=503,
                user_message="Team poll service is temporarily unavailable.",
                details={"tool_name": self.spec.name},
            )
        result = await get_owner_poll_status(
            db,
            project=project,
            owner_user_id=owner_user_id,
            poll_id=parsed.poll_id,
        )
        if not bool(result.get("ok")):
            reason = str(result.get("reason") or "status_failed")
            user_message = "I couldn't find an active poll for that status request."
            if reason == "ambiguous_poll":
                user_message = "Multiple active polls matched. Please specify the poll ID."
            return build_tool_error(
                error_code="team_poll_status_failed",
                error_class="not_found",
                retryable=False,
                http_status=404,
                user_message=user_message,
                details={"tool_name": self.spec.name, "reason": reason, "candidates": result.get("candidates") or []},
            )
        status_payload = result.get("status") or {}
        text = str(status_payload.get("text") or "").strip()
        return {
            "ok": True,
            "source": "team_poll",
            "poll_id": str(result.get("poll_id") or "").strip() or None,
            "status": status_payload,
            "summary": text or "Team poll status fetched.",
        }


class TeamPollCloseTool(_BaseTeamPollTool):
    spec = ToolSpec(
        name="team_poll_close",
        description="Close the active team poll or a specific poll id.",
        provider="promarshal",
        capabilities=["broadcast_poll"],
        role_requirements=[MemberRole.OWNER.value],
        integration_requirements=["slack"],
        requires_confirmation=False,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )
    input_model = TeamPollCloseInput

    async def _execute_validated(
        self,
        *,
        parsed: TeamPollCloseInput,
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
                user_message="Project context is missing. Please select an active project and try again.",
                details={"project_id": project_id, "tool_name": self.spec.name},
            )
        owner_member = self._resolve_owner_member(project=project, user_id=user_id, execution_context=execution_context)
        if not owner_member:
            return build_tool_error(
                error_code="owner_member_not_resolved",
                error_class="permission",
                retryable=False,
                http_status=403,
                user_message="Only project owners can close a team poll in this workspace.",
                details={"project_id": project_id, "tool_name": self.spec.name},
            )
        owner_user_id = str(owner_member.get("user_id") or owner_member.get("email") or "").strip()
        db = get_database()
        if db is None:
            return build_tool_error(
                error_code="database_unavailable",
                error_class="integration",
                retryable=True,
                http_status=503,
                user_message="Team poll service is temporarily unavailable.",
                details={"tool_name": self.spec.name},
            )
        result = await close_owner_poll(
            db,
            project=project,
            owner_user_id=owner_user_id,
            poll_id=parsed.poll_id,
        )
        if not bool(result.get("ok")):
            reason = str(result.get("reason") or "close_failed")
            user_message = "I couldn't close the team poll right now."
            if reason == "ambiguous_poll":
                user_message = "Multiple active polls matched. Please specify the poll ID to close."
            return build_tool_error(
                error_code="team_poll_close_failed",
                error_class="not_found",
                retryable=False,
                http_status=404,
                user_message=user_message,
                details={"tool_name": self.spec.name, "reason": reason, "candidates": result.get("candidates") or []},
            )
        poll_id = str(result.get("poll_id") or "").strip()
        return {
            "ok": True,
            "source": "team_poll",
            "poll_id": poll_id or None,
            "summary": f"Team poll closed ({poll_id})." if poll_id else "Team poll closed.",
            "committed_action": {
                "summary": f"Closed team poll {poll_id}" if poll_id else "Closed team poll",
                "tool_name": self.spec.name,
                "poll_id": poll_id or None,
            },
        }


def get_launch_team_poll_tools() -> list[CortexTool]:
    return [
        TeamPollCreateTool(),
        TeamPollStatusTool(),
        TeamPollCloseTool(),
    ]
