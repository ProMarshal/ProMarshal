import unittest
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.agent_runtime.executor import AgentExecutor
from app.cortex.quality.models import OperationProfile
from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec


class _JiraStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1, max_length=64)
    status: str = Field(min_length=1, max_length=64)
    operation_id: Optional[str] = Field(default=None, min_length=3, max_length=128)


class _JiraStatusTool(CortexTool):
    input_model = _JiraStatusInput
    spec = ToolSpec(
        name="jira_update_status",
        description="update task status",
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        return {"ok": True, "args": dict(args or {})}


class CortexExecutorParseGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.executor = AgentExecutor.__new__(AgentExecutor)
        self._quality_enabled = bool(settings.cortex_quality_engine_enabled)
        settings.cortex_quality_engine_enabled = False

    def tearDown(self) -> None:
        settings.cortex_quality_engine_enabled = self._quality_enabled

    async def test_write_parse_gate_normalizes_status_alias(self):
        result = await self.executor._prepare_tool_args_with_quality(
            tool=_JiraStatusTool(),
            args={"external_id": "PROJ-1", "status": "in review"},
            execution_context={"project_id": "p1", "user_id": "u1"},
            forced_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
        )
        self.assertEqual(str(result.get("status") or ""), "in_progress")
        self.assertEqual(str(result.get("external_id") or ""), "PROJ-1")

    async def test_write_parse_gate_allows_custom_status_tokens(self):
        result = await self.executor._prepare_tool_args_with_quality(
            tool=_JiraStatusTool(),
            args={"external_id": "PROJ-1", "status": "qa complete maybe"},
            execution_context={"project_id": "p1", "user_id": "u1"},
            forced_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
        )
        self.assertNotIn("_tool_error", result)
        self.assertEqual(str(result.get("status") or ""), "qa complete maybe")


if __name__ == "__main__":
    unittest.main()
