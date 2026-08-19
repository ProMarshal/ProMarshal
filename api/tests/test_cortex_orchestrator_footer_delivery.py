import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.cortex.orchestrator import CortexOrchestrator


class CortexOrchestratorFooterDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_deliver_response_appends_footer_for_tool_call_dm(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        response_service = SimpleNamespace(send=AsyncMock(return_value={"ok": True}))
        orchestrator.response_service = response_service
        orchestrator._load_project_context = AsyncMock(
            return_value={"project_id": "proj-1", "project_name": "Alpha"}
        )

        with patch(
            "app.cortex.orchestrator.get_database",
            return_value=object(),
        ), patch(
            "app.cortex.orchestrator.build_project_context_footer",
            new=AsyncMock(return_value='Current Project: Alpha (proj-1) | To switch, type: "switch project"'),
        ):
            await orchestrator._deliver_response(
                text="Here are the current requests.",
                channel_id="D1",
                thread_ts=None,
                metadata={
                    "source": "slack_dm",
                    "project_id": "proj-1",
                    "team_id": "T1",
                    "user_id": "U1",
                    "tool_calls_count": 1,
                },
            )

        response_service.send.assert_awaited_once()
        payload = response_service.send.await_args.args[0]
        self.assertIn("Here are the current requests.", payload.text)
        self.assertIn("Current Project: Alpha (proj-1)", payload.text)

    async def test_deliver_response_skips_footer_for_non_tool_call_dm(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        response_service = SimpleNamespace(send=AsyncMock(return_value={"ok": True}))
        orchestrator.response_service = response_service
        orchestrator._load_project_context = AsyncMock(
            return_value={"project_id": "proj-1", "project_name": "Alpha"}
        )

        with patch(
            "app.cortex.orchestrator.get_database",
            return_value=object(),
        ), patch(
            "app.cortex.orchestrator.build_project_context_footer",
            new=AsyncMock(return_value='Current Project: Alpha (proj-1) | To switch, type: "switch project"'),
        ):
            await orchestrator._deliver_response(
                text="Hello there.",
                channel_id="D1",
                thread_ts=None,
                metadata={
                    "source": "slack_dm",
                    "project_id": "proj-1",
                    "team_id": "T1",
                    "user_id": "U1",
                    "tool_calls_count": 0,
                },
            )

        payload = response_service.send.await_args.args[0]
        self.assertEqual(payload.text, "Hello there.")


if __name__ == "__main__":
    unittest.main()

