import unittest
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.agent_runtime.executor import AgentExecutor
from app.core.config import settings
from app.cortex.orchestrator import CortexOrchestrator
from app.cortex.quality.models import OperationProfile
from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec


class _StatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    operation_id: Optional[str] = None


class _StatusTool(CortexTool):
    input_model = _StatusInput
    spec = ToolSpec(
        name="jira_update_status",
        description="status update test tool",
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        return {"ok": True, "args": args}


class CortexRuntimeHardeningGapClosureTests(unittest.TestCase):
    def test_target_grounding_blocks_ungrounded_identifier_single_turn(self):
        executor = AgentExecutor.__new__(AgentExecutor)
        tool = _StatusTool()
        error = executor._enforce_write_target_grounding(
            tool=tool,
            args={"external_id": "PRM-59", "status": "done"},
            execution_context={
                "latest_user_message": "move it to done",
                "session_snapshot_target_refs": [],
                "runtime_target_refs": [],
            },
            operation_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
        )
        self.assertIsNotNone(error)
        self.assertEqual(error.get("error_code"), "target_scope_clarification_required")

    def test_target_grounding_allows_followup_when_snapshot_contains_identifier(self):
        executor = AgentExecutor.__new__(AgentExecutor)
        tool = _StatusTool()
        error = executor._enforce_write_target_grounding(
            tool=tool,
            args={"external_id": "PRM-59", "status": "in progress"},
            execution_context={
                "latest_user_message": "move both to in progress",
                "session_snapshot_target_refs": ["PRM-58", "PRM-59"],
                "runtime_target_refs": [],
            },
            operation_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
        )
        self.assertIsNone(error)

    def test_target_grounding_allows_followup_when_runtime_refs_contain_identifier(self):
        executor = AgentExecutor.__new__(AgentExecutor)
        tool = _StatusTool()
        error = executor._enforce_write_target_grounding(
            tool=tool,
            args={"external_id": "PRM-87", "status": "in progress"},
            execution_context={
                "latest_user_message": "continue",
                "session_snapshot_target_refs": [],
                "runtime_target_refs": ["PRM-87"],
            },
            operation_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
        )
        self.assertIsNone(error)

    def test_before_tool_call_guard_respects_total_tool_budget(self):
        original_total_budget = settings.cortex_max_total_tool_calls_per_turn
        try:
            settings.cortex_max_total_tool_calls_per_turn = 1
            executor = AgentExecutor.__new__(AgentExecutor)
            tool = _StatusTool()
            error, _ = executor._before_tool_call_guard(
                tool=tool,
                args={"external_id": "PRM-59", "status": "done"},
                execution_context={
                    "_tool_call_guard_hashes": [],
                    "_tool_call_guard_write_count": 0,
                    "_executor_turn_started_at_perf": 0.0,
                },
                tool_call_trace={"count": 1},
            )
            self.assertIsNotNone(error)
            self.assertEqual(error.get("error_code"), "tool_call_budget_exceeded")
        finally:
            settings.cortex_max_total_tool_calls_per_turn = original_total_budget

    def test_provider_selection_pending_stays_clarification_first_without_provider_choice(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        calls, response = orchestrator._prepare_tool_calls_for_provider_selection_pending(
            pending_question={
                "candidate_providers": ["jira", "linear"],
                "capability": "workitem_create",
                "proposed_tool_calls": [
                    {"capability": "workitem_create", "args": {"title": "test task"}},
                ],
                "execution_context": {},
            },
            user_message="continue",
        )
        self.assertIsNone(calls)
        self.assertIsInstance(response, str)
        self.assertIn("Reply with one provider", response)


if __name__ == "__main__":
    unittest.main()
