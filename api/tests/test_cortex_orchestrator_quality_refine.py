import unittest
from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.cortex.orchestrator import CortexOrchestrator
from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec
from app.cortex.tools.registry import CortexToolRegistry


class _DummyWriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    operation_id: str = Field(min_length=3)


class _DummyWriteTool(CortexTool):
    input_model = _DummyWriteInput
    spec = ToolSpec(
        name="dummy_write_tool_refine",
        description="dummy write tool for refine tests",
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        return {"ok": True}


class _FakeExecutor:
    def __init__(self, response: Dict[str, Any]):
        self.response = response

    async def prepare_tool_args_for_execution(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        _ = (tool_name, args, execution_context)
        return dict(self.response)


class _FakeProposalService:
    def __init__(self, repaired_tool_calls):
        self.repaired_tool_calls = repaired_tool_calls
        self.calls = 0

    async def repair_tool_args(
        self,
        *,
        executor: Any,
        tool_calls: Any,
        tool_contracts: Any,
        original_request: str,
        followup_message: str,
        message_context_source: str,
        context: Dict[str, Any],
    ):
        _ = (
            executor,
            tool_calls,
            tool_contracts,
            original_request,
            followup_message,
            message_context_source,
            context,
        )
        self.calls += 1
        return self.repaired_tool_calls


class CortexOrchestratorQualityRefineTests(unittest.IsolatedAsyncioTestCase):
    def _build_orchestrator(self, *, executor_response: Dict[str, Any]) -> CortexOrchestrator:
        registry = CortexToolRegistry()
        registry.register(_DummyWriteTool())

        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        orchestrator.tool_registry = registry
        orchestrator.executor = _FakeExecutor(executor_response)
        orchestrator.proposal_service = _FakeProposalService(repaired_tool_calls=[])
        orchestrator.SYSTEM_MANAGED_TOOL_FIELDS = {"operation_id"}
        return orchestrator

    async def test_refine_uses_shared_executor_quality_success(self):
        orchestrator = self._build_orchestrator(
            executor_response={"title": "cadence summary"}
        )
        refined, missing, prevalidated = await orchestrator._refine_proposal_calls_with_quality(
            calls=[{"tool_name": "dummy_write_tool_refine", "args": {"title": "cadence summary"}}],
            visible_tool_descriptors=[],
            execution_context={"user_message": "create cadence summary task"},
        )
        self.assertEqual(missing, [])
        self.assertEqual(len(refined), 1)
        self.assertEqual(refined[0]["args"].get("title"), "cadence summary")
        self.assertIn("title", prevalidated)
        self.assertIn("dummy_write_tool_refine.title", prevalidated)

    async def test_refine_collects_missing_from_tool_error(self):
        orchestrator = self._build_orchestrator(
            executor_response={
                "_tool_error": {
                    "error_code": "insufficient_argument_quality",
                    "details": {"insufficient_fields": ["dummy_write_tool_refine.title"]},
                }
            }
        )
        refined, missing, _ = await orchestrator._refine_proposal_calls_with_quality(
            calls=[{"tool_name": "dummy_write_tool_refine", "args": {"title": "do it"}}],
            visible_tool_descriptors=[],
            execution_context={"user_message": "create task"},
        )
        self.assertEqual(len(refined), 1)
        self.assertIn("dummy_write_tool_refine.title", missing)

    async def test_refine_preserves_field_meta_from_proposal_call(self):
        orchestrator = self._build_orchestrator(
            executor_response={"title": "cadence summary"}
        )
        refined, missing, _ = await orchestrator._refine_proposal_calls_with_quality(
            calls=[
                {
                    "tool_name": "dummy_write_tool_refine",
                    "args": {"title": "cadence summary"},
                    "field_meta": {
                        "title": {
                            "provenance": "explicit_user_text",
                            "confidence": 0.95,
                        }
                    },
                }
            ],
            visible_tool_descriptors=[],
            execution_context={"user_message": "create cadence summary task"},
        )
        self.assertEqual(missing, [])
        self.assertEqual(refined[0].get("field_meta", {}).get("title", {}).get("provenance"), "explicit_user_text")

    async def test_repair_calls_skips_llm_repair_when_budget_exhausted(self):
        original_budget = settings.cortex_max_repair_attempts_per_turn
        try:
            settings.cortex_max_repair_attempts_per_turn = 1
            orchestrator = self._build_orchestrator(executor_response={})
            fake_proposal = _FakeProposalService(
                repaired_tool_calls=[
                    {"tool_name": "dummy_write_tool_refine", "args": {"title": "from repair"}}
                ]
            )
            orchestrator.proposal_service = fake_proposal
            context = {
                "user_message": "create task",
                "latest_user_message": "create task",
                "message_context_source": "user_turn",
                "repair_attempts_used": 1,
            }
            repaired = await orchestrator._repair_calls_with_context_if_needed(
                calls=[{"tool_name": "dummy_write_tool_refine", "args": {}}],
                execution_context=context,
            )
            self.assertEqual(fake_proposal.calls, 0)
            self.assertEqual(repaired[0]["args"], {})
        finally:
            settings.cortex_max_repair_attempts_per_turn = original_budget

    async def test_repair_calls_uses_llm_repair_when_budget_available(self):
        original_budget = settings.cortex_max_repair_attempts_per_turn
        try:
            settings.cortex_max_repair_attempts_per_turn = 1
            orchestrator = self._build_orchestrator(executor_response={})
            fake_proposal = _FakeProposalService(
                repaired_tool_calls=[
                    {"tool_name": "dummy_write_tool_refine", "args": {"title": "from repair"}}
                ]
            )
            orchestrator.proposal_service = fake_proposal
            context = {
                "user_message": "create task",
                "latest_user_message": "create task",
                "message_context_source": "user_turn",
                "repair_attempts_used": 0,
            }
            repaired = await orchestrator._repair_calls_with_context_if_needed(
                calls=[{"tool_name": "dummy_write_tool_refine", "args": {}}],
                execution_context=context,
            )
            self.assertEqual(fake_proposal.calls, 1)
            self.assertEqual(repaired[0]["args"].get("title"), "from repair")
            self.assertEqual(int(context.get("repair_attempts_used") or 0), 1)
        finally:
            settings.cortex_max_repair_attempts_per_turn = original_budget


if __name__ == "__main__":
    unittest.main()

