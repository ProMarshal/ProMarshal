import unittest
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.cortex.orchestrator import CortexOrchestrator
from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec
from app.cortex.tools.registry import CortexToolRegistry


class _WriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    operation_id: str = Field(min_length=3)


class _ReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keyword: Optional[str] = Field(default=None, max_length=200)


class _WriteTool(CortexTool):
    input_model = _WriteInput
    spec = ToolSpec(
        name="dummy_write_preflight_tool",
        description="dummy write tool",
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        return {"ok": True}


class _ReadTool(CortexTool):
    input_model = _ReadInput
    spec = ToolSpec(
        name="dummy_read_preflight_tool",
        description="dummy read tool",
        idempotency=IdempotencyClass.READ_ONLY.value,
    )

    async def execute(self, args):
        return {"ok": True}


class _FakeExecutor:
    def __init__(self, responses_by_tool: Dict[str, Dict[str, Any]]):
        self.responses_by_tool = responses_by_tool
        self.calls = 0

    async def prepare_tool_args_for_execution(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        _ = (args, execution_context)
        self.calls += 1
        return dict(self.responses_by_tool.get(tool_name) or {})


class CortexOrchestratorWritePreflightTests(unittest.IsolatedAsyncioTestCase):
    def _build_orchestrator(self, *, responses_by_tool: Dict[str, Dict[str, Any]]) -> CortexOrchestrator:
        registry = CortexToolRegistry()
        registry.register(_WriteTool())
        registry.register(_ReadTool())

        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        orchestrator.tool_registry = registry
        orchestrator.executor = _FakeExecutor(responses_by_tool=responses_by_tool)
        orchestrator.SYSTEM_MANAGED_TOOL_FIELDS = {"operation_id"}
        return orchestrator

    async def test_preflight_collects_write_quality_missing(self):
        orchestrator = self._build_orchestrator(
            responses_by_tool={
                "dummy_write_preflight_tool": {
                    "_tool_error": {
                        "error_code": "insufficient_argument_quality",
                        "details": {
                            "insufficient_fields": ["dummy_write_preflight_tool.title"],
                        },
                    }
                }
            }
        )

        prepared, missing, unresolved, failed = await orchestrator._preflight_write_calls_for_proposal(
            calls=[{"tool_name": "dummy_write_preflight_tool", "args": {"title": "do it"}}],
            execution_context={"user_message": "create task"},
        )

        self.assertEqual(prepared, {})
        self.assertIn("dummy_write_preflight_tool.title", missing)
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(failed, [])

    async def test_preflight_only_prepares_write_calls(self):
        orchestrator = self._build_orchestrator(
            responses_by_tool={
                "dummy_write_preflight_tool": {"title": "cadence summary"},
            }
        )

        prepared, missing, unresolved, failed = await orchestrator._preflight_write_calls_for_proposal(
            calls=[
                {"tool_name": "dummy_read_preflight_tool", "args": {"keyword": "cadence"}},
                {"tool_name": "dummy_write_preflight_tool", "args": {"title": "cadence summary"}},
            ],
            execution_context={"user_message": "list and create"},
        )

        self.assertEqual(missing, [])
        self.assertEqual(unresolved, [])
        self.assertEqual(failed, [])
        self.assertNotIn(0, prepared)
        self.assertIn(1, prepared)
        self.assertEqual(prepared[1].get("title"), "cadence summary")
        self.assertEqual(orchestrator.executor.calls, 1)

    async def test_prepare_allows_custom_status_token_for_update_status(self):
        orchestrator = self._build_orchestrator(
            responses_by_tool={}
        )

        prepared, missing, failed, canonical = await orchestrator._prepare_tool_args_for_orchestrator_execution(
            tool_name="jira_update_status",
            args={"external_id": "SCRUM-1", "status": "ship_it_now"},
            execution_context={
                "project_id": "proj-1",
                "user_id": "user-1",
                "latest_user_message": "move SCRUM-1 to ship_it_now",
            },
        )

        self.assertIsNotNone(prepared)
        self.assertEqual(prepared, {})
        self.assertEqual(missing, [])
        self.assertIsNone(failed)
        self.assertEqual(canonical.get("external_id"), "SCRUM-1")
        self.assertEqual(canonical.get("status"), "ship_it_now")


if __name__ == "__main__":
    unittest.main()
