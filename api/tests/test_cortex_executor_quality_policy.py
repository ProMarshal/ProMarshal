import unittest
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.agent_runtime.executor import AgentExecutor
from app.cortex.quality.engine import QualityEngine
from app.cortex.quality.models import OperationProfile
from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec


class _FakeProposalService:
    async def assess_field_quality(self, **kwargs):
        return {"is_sufficient": False, "confidence": 0.2, "reason": "too vague"}

    async def repair_tool_args(self, **kwargs):
        return None


class _ReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keyword: Optional[str] = Field(default=None, max_length=200)


class _ReadAssigneeScopeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignee_mode: Optional[str] = Field(default=None, max_length=32)
    assignee_value: Optional[str] = Field(default=None, max_length=320)
    keyword: Optional[str] = Field(default=None, max_length=200)


class _WriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    operation_id: str = Field(min_length=3)


class _ReadTool(CortexTool):
    input_model = _ReadInput
    spec = ToolSpec(
        name="read_tool_for_executor_quality",
        description="read test tool",
        idempotency=IdempotencyClass.READ_ONLY.value,
    )

    async def execute(self, args):
        return {"ok": True, "args": args}


class _ReadAssigneeScopeTool(CortexTool):
    input_model = _ReadAssigneeScopeInput
    spec = ToolSpec(
        name="read_assignee_scope_tool",
        description="read tool with assignee scope",
        idempotency=IdempotencyClass.READ_ONLY.value,
    )

    async def execute(self, args):
        return {"ok": True, "args": args}


class _WriteTool(CortexTool):
    input_model = _WriteInput
    spec = ToolSpec(
        name="write_tool_for_executor_quality",
        description="write test tool",
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        return {"ok": True, "args": args}


class _WriteCommentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1, max_length=40)
    comment: str = Field(min_length=1, max_length=2000)
    operation_id: Optional[str] = Field(default=None, max_length=128)


class _WriteCommentTool(CortexTool):
    input_model = _WriteCommentInput
    spec = ToolSpec(
        name="jira_add_comment",
        description="write comment test tool",
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        return {"ok": True, "args": args}


class CortexExecutorQualityPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.executor = AgentExecutor.__new__(AgentExecutor)
        self.executor.proposal_service = _FakeProposalService()
        self.executor.quality_engine = QualityEngine(proposal_service=self.executor.proposal_service)
        self._flags = {
            "cortex_quality_engine_enabled": settings.cortex_quality_engine_enabled,
            "cortex_quality_read_enabled": settings.cortex_quality_read_enabled,
            "cortex_quality_read_fail_open_default": settings.cortex_quality_read_fail_open_default,
            "cortex_quality_semantic_enabled": settings.cortex_quality_semantic_enabled,
        }
        settings.cortex_quality_engine_enabled = True
        settings.cortex_quality_read_enabled = True
        settings.cortex_quality_read_fail_open_default = True
        settings.cortex_quality_semantic_enabled = True

    def tearDown(self):
        for key, value in self._flags.items():
            setattr(settings, key, value)

    async def test_read_blocking_quality_fails_open(self):
        result = await self.executor._prepare_tool_args_with_quality(
            tool=_ReadTool(),
            args={},
            execution_context={"user_message": "show tasks"},
            forced_profile=OperationProfile.READ_ONLY,
        )
        self.assertEqual(result, {})

    async def test_write_blocking_quality_returns_tool_error(self):
        result = await self.executor._prepare_tool_args_with_quality(
            tool=_WriteTool(),
            args={"title": "do it"},
            execution_context={"user_message": "create a task"},
            forced_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
        )
        self.assertIn("_tool_error", result)
        error = result.get("_tool_error") or {}
        self.assertEqual(error.get("error_code"), "insufficient_argument_quality")

    async def test_explicit_comment_prevalidated_field_avoids_false_block(self):
        result = await self.executor._prepare_tool_args_with_quality(
            tool=_WriteCommentTool(),
            args={"external_id": "PRM-59", "comment": "please attach error logs and screenshot"},
            execution_context={
                "user_message": "add comment to PRM-59",
                "quality_prevalidated_fields": ["comment", "jira_add_comment.comment"],
            },
            forced_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
        )
        self.assertNotIn("_tool_error", result)
        self.assertEqual(str(result.get("external_id") or ""), "PRM-59")
        self.assertIn("attach error logs", str(result.get("comment") or ""))

    async def test_comment_missing_field_error_uses_friendly_label_not_internal_token(self):
        result = await self.executor._prepare_tool_args_with_quality(
            tool=_WriteCommentTool(),
            args={"external_id": "PRM-59"},
            execution_context={"user_message": "add a comment"},
            forced_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
        )
        self.assertIn("_tool_error", result)
        err = result.get("_tool_error") or {}
        message = str(err.get("user_message") or "")
        self.assertTrue(
            ("Please share: comment" in message)
            or ("I need a bit more detail before I can continue." in message)
        )
        self.assertNotIn("jira_add_comment.comment", message)

    async def test_command_shell_comment_text_is_rejected_with_comment_clarification(self):
        result = await self.executor._prepare_tool_args_with_quality(
            tool=_WriteCommentTool(),
            args={"external_id": "PRM-59", "comment": "update PRM-59 comments"},
            execution_context={"user_message": "update PRM-59 comments"},
            forced_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
        )
        self.assertIn("_tool_error", result)
        err = result.get("_tool_error") or {}
        self.assertEqual(str(err.get("error_code") or ""), "insufficient_argument_quality")
        message = str(err.get("user_message") or "")
        self.assertIn("Please share: comment", message)

    async def test_command_shell_target_only_comment_variants_are_rejected(self):
        rejected_inputs = [
            "add comment for prm59",
            "add comment on PRM-59",
            "comment for PRM-59",
            "for PRM-59",
            "PRM-59",
            "task PRM-59 comments",
            "add a comment",
            "comment",
            "please add comment",
        ]
        for raw in rejected_inputs:
            with self.subTest(raw=raw):
                result = await self.executor._prepare_tool_args_with_quality(
                    tool=_WriteCommentTool(),
                    args={"external_id": "PRM-59", "comment": raw},
                    execution_context={"user_message": raw},
                    forced_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
                )
                self.assertIn("_tool_error", result)
                err = result.get("_tool_error") or {}
                self.assertEqual(str(err.get("error_code") or ""), "insufficient_argument_quality")
                message = str(err.get("user_message") or "")
                self.assertIn("Please share: comment", message)

    async def test_actionable_comment_text_variants_are_allowed(self):
        allowed_inputs = [
            "please attach error logs and screenshots",
            "need more info on error and logs",
            "for PRM-59 please attach logs",
            "@sridevi, please add logs if available",
        ]
        for raw in allowed_inputs:
            with self.subTest(raw=raw):
                result = await self.executor._prepare_tool_args_with_quality(
                    tool=_WriteCommentTool(),
                    args={"external_id": "PRM-59", "comment": raw},
                    execution_context={"user_message": f"add comment on PRM-59: {raw}"},
                    forced_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
                )
                self.assertNotIn("_tool_error", result)

    async def test_read_personal_scope_default_sets_assignee_mode_me(self):
        result = await self.executor._prepare_tool_args_with_quality(
            tool=_ReadAssigneeScopeTool(),
            args={"keyword": "launch checklist"},
            execution_context={"user_message": "can you check if i have any tasks"},
            forced_profile=OperationProfile.READ_ONLY,
        )
        self.assertEqual(str(result.get("assignee_mode") or ""), "me")
        self.assertNotIn("assignee_value", result)

    async def test_read_personal_scope_default_does_not_override_explicit_assignee(self):
        result = await self.executor._prepare_tool_args_with_quality(
            tool=_ReadAssigneeScopeTool(),
            args={"assignee_mode": "specific", "assignee_value": "owner@example.com"},
            execution_context={"user_message": "can you check if i have any tasks"},
            forced_profile=OperationProfile.READ_ONLY,
        )
        self.assertEqual(str(result.get("assignee_mode") or ""), "specific")
        self.assertEqual(str(result.get("assignee_value") or ""), "owner@example.com")

    async def test_tool_schema_hides_system_managed_operation_id(self):
        schema = self.executor._tool_params_json_schema(_WriteTool())
        properties = schema.get("properties") or {}
        required_fields = schema.get("required") or []
        self.assertNotIn("operation_id", properties)
        self.assertNotIn("operation_id", required_fields)
        self.assertIn("title", properties)
        self.assertIn("title", required_fields)


if __name__ == "__main__":
    unittest.main()
