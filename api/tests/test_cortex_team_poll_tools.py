import importlib
import unittest
from unittest.mock import AsyncMock, patch


tools_module = importlib.import_module("app.cortex.tools.providers.team_poll_tools")


class CortexTeamPollToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_tool_requires_confirmation(self):
        tool = tools_module.TeamPollCreateTool()
        self.assertTrue(tool.normalized_spec().requires_confirmation)

    async def test_create_tool_calls_orchestrator_and_returns_committed_action(self):
        tool = tools_module.TeamPollCreateTool()
        project = {
            "project_id": "proj-1234-5678",
            "members": [
                {
                    "user_id": "UOWNER",
                    "email": "owner@example.com",
                    "status": "active",
                    "role": "owner",
                }
            ],
            "integrations": {"slack": {"team_id": "T123"}},
        }
        create_mock = AsyncMock(
            return_value={
                "ok": True,
                "poll_id": "poll-1",
                "question_text": "Any blockers today?",
                "delivery": {"sent": 1, "failed": 0},
            }
        )
        with patch.object(tools_module, "load_project", new=AsyncMock(return_value=project)), patch.object(
            tools_module, "get_database", return_value=object()
        ), patch.object(
            tools_module, "create_team_poll", new=create_mock
        ):
            result = await tool.execute_with_context(
                {"question_text": "Any blockers today?", "operation_id": "op_test_team_poll_1"},
                {"project_id": "proj-1234-5678", "user_id": "UOWNER"},
            )
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("poll_id"), "poll-1")
        self.assertIn("committed_action", result)
        create_mock.assert_awaited_once()

    async def test_status_tool_returns_not_found_error_when_no_active_poll(self):
        tool = tools_module.TeamPollStatusTool()
        project = {
            "project_id": "proj-1234-5678",
            "members": [{"user_id": "UOWNER", "email": "owner@example.com", "status": "active", "role": "owner"}],
        }
        status_mock = AsyncMock(return_value={"ok": False, "reason": "no_active_poll"})
        with patch.object(tools_module, "load_project", new=AsyncMock(return_value=project)), patch.object(
            tools_module, "get_database", return_value=object()
        ), patch.object(
            tools_module, "get_owner_poll_status", new=status_mock
        ):
            result = await tool.execute_with_context(
                {},
                {"project_id": "proj-1234-5678", "user_id": "UOWNER"},
            )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_code"), "team_poll_status_failed")


if __name__ == "__main__":
    unittest.main()
