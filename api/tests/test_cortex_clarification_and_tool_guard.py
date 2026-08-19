import unittest
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.agent_runtime.executor import AgentExecutor
from app.cortex.clarification_formatting import (
    build_missing_fields_response,
    render_missing_field_labels,
)
from app.cortex.orchestrator import CortexOrchestrator
from app.cortex.proposals.models import ProposedToolCall
from app.cortex.quality.engine import QualityEngine
from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec


class _WriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1)
    comment: str = Field(min_length=1)
    operation_id: Optional[str] = None


class _WriteTool(CortexTool):
    input_model = _WriteInput
    spec = ToolSpec(
        name="guard_test_write_tool",
        description="guard test write tool",
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        return {"ok": True, "args": args}


class ClarificationAndGuardTests(unittest.TestCase):
    def test_render_missing_field_labels_hides_internal_tokens(self):
        labels = render_missing_field_labels(
            ["jira_add_comment.comment", "jira_add_comment.external_id", "assignee_mode"]
        )
        self.assertIn("comment", labels)
        self.assertIn("task ID", labels)
        self.assertIn("assignee", labels)
        self.assertNotIn("jira_add_comment.comment", labels)

    def test_build_missing_fields_response_uses_friendly_labels(self):
        text = build_missing_fields_response(["jira_add_comment.comment", "jira_add_comment.external_id"])
        self.assertIn("comment", text.lower())
        self.assertIn("task id", text.lower())
        self.assertNotIn("jira_add_comment", text.lower())

    def test_before_tool_call_guard_blocks_duplicate_calls(self):
        executor = AgentExecutor.__new__(AgentExecutor)
        tool = _WriteTool()
        context = {
            "_tool_call_guard_hashes": [],
            "_tool_call_guard_write_count": 0,
            "_executor_turn_started_at_perf": 0.0,
        }
        first_error, _ = executor._before_tool_call_guard(
            tool=tool,
            args={"external_id": "PRM-51", "comment": "Please add logs"},
            execution_context=context,
            tool_call_trace={"count": 0},
        )
        self.assertIsNone(first_error)

        second_error, _ = executor._before_tool_call_guard(
            tool=tool,
            args={"external_id": "PRM-51", "comment": "Please add logs"},
            execution_context=context,
            tool_call_trace={"count": 1},
        )
        self.assertIsNotNone(second_error)
        self.assertEqual(second_error.get("error_code"), "duplicate_tool_call_blocked")

    def test_before_tool_call_guard_respects_write_budget(self):
        original_write_budget = settings.cortex_max_write_tool_calls_per_turn
        try:
            settings.cortex_max_write_tool_calls_per_turn = 1
            executor = AgentExecutor.__new__(AgentExecutor)
            tool = _WriteTool()
            context = {
                "_tool_call_guard_hashes": [],
                "_tool_call_guard_write_count": 1,
                "_executor_turn_started_at_perf": 0.0,
            }
            error, _ = executor._before_tool_call_guard(
                tool=tool,
                args={"external_id": "PRM-51", "comment": "Please add logs"},
                execution_context=context,
                tool_call_trace={"count": 1},
            )
            self.assertIsNotNone(error)
            self.assertEqual(error.get("error_code"), "write_tool_call_budget_exceeded")
        finally:
            settings.cortex_max_write_tool_calls_per_turn = original_write_budget

    def test_proposed_tool_call_accepts_field_meta(self):
        call = ProposedToolCall.model_validate(
            {
                "capability": "workitem_add_comment",
                "tool_name": "jira_add_comment",
                "args": {"external_id": "PRM-51", "comment": "Please add logs"},
                "field_meta": {
                    "comment": {"provenance": "explicit_user_text", "confidence": 0.95},
                    "external_id": {"provenance": "deterministic_parse", "confidence": 0.9},
                },
                "confidence": 0.9,
            }
        )
        self.assertEqual(call.field_meta["comment"].provenance, "explicit_user_text")
        self.assertAlmostEqual(call.field_meta["external_id"].confidence, 0.9)

    def test_proposal_prevalidated_tokens_from_field_meta(self):
        original_conf = settings.cortex_proposal_field_meta_min_confidence
        try:
            settings.cortex_proposal_field_meta_min_confidence = 0.6
            orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
            calls = [
                ProposedToolCall.model_validate(
                    {
                        "capability": "workitem_add_comment",
                        "tool_name": "jira_add_comment",
                        "args": {"comment": "Need logs"},
                        "field_meta": {
                            "comment": {"provenance": "explicit_user_text", "confidence": 0.92},
                            "external_id": {"provenance": "llm_inferred", "confidence": 0.99},
                        },
                    }
                )
            ]
            tokens = orchestrator._proposal_prevalidated_tokens(calls)
            self.assertIn("comment", tokens)
            self.assertIn("jira_add_comment.comment", tokens)
            self.assertNotIn("external_id", tokens)
        finally:
            settings.cortex_proposal_field_meta_min_confidence = original_conf

    def test_orchestrator_repair_budget_is_bounded(self):
        original_limit = settings.cortex_max_repair_attempts_per_turn
        try:
            settings.cortex_max_repair_attempts_per_turn = 1
            orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
            context = {"repair_attempts_used": 0}
            self.assertTrue(orchestrator._consume_repair_budget(context))
            self.assertFalse(orchestrator._consume_repair_budget(context))
        finally:
            settings.cortex_max_repair_attempts_per_turn = original_limit

    def test_quality_engine_repair_budget_is_bounded(self):
        original_limit = settings.cortex_max_repair_attempts_per_turn
        try:
            settings.cortex_max_repair_attempts_per_turn = 1
            quality_engine = QualityEngine.__new__(QualityEngine)
            context = {"repair_attempts_used": 0}
            self.assertTrue(quality_engine._consume_repair_budget(context))
            self.assertFalse(quality_engine._consume_repair_budget(context))
        finally:
            settings.cortex_max_repair_attempts_per_turn = original_limit


if __name__ == "__main__":
    unittest.main()
