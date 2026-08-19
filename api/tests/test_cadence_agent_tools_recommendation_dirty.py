import unittest
from unittest.mock import AsyncMock, patch

from app.cadence.agent_tools import (
    CadenceAddCommentTool,
    CadenceAssignTaskTool,
    CadenceUpdateTaskStatusTool,
)


class CadenceAgentToolsRecommendationDirtyTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_status_marks_dirty_on_success(self):
        tool = CadenceUpdateTaskStatusTool()
        session = {
            "session_id": "sess-1",
            "project_id": "proj-1111-2222",
            "current_task_index": 0,
            "tasks": [{"external_id": "ABC-1", "status": "todo"}],
        }
        with patch(
            "app.cadence.agent_tools._load_fresh_session",
            new=AsyncMock(return_value=session),
        ), patch(
            "app.cadence.agent_tools.cadence_action_adapter_registry.dispatch",
            new=AsyncMock(return_value={"ok": True, "status_updated": True}),
        ), patch(
            "app.cadence.agent_tools._persist_task_field",
            new=AsyncMock(return_value=True),
        ), patch(
            "app.cadence.agent_tools.mark_project_task_dirty",
            return_value=True,
        ) as dirty_mock:
            result = await tool.execute_with_context(
                {"status": "done", "task_key": "ABC-1"},
                {"session_id": "sess-1", "project_id": "proj-1111-2222"},
            )

        self.assertTrue(result.get("ok"))
        dirty_mock.assert_called_once_with("proj-1111-2222", "ABC-1")

    async def test_add_comment_marks_dirty_on_success(self):
        tool = CadenceAddCommentTool()
        session = {
            "session_id": "sess-2",
            "project_id": "proj-1111-2222",
            "current_task_index": 0,
            "tasks": [{"external_id": "ABC-2", "status": "in_progress"}],
        }
        with patch(
            "app.cadence.agent_tools._load_fresh_session",
            new=AsyncMock(return_value=session),
        ), patch(
            "app.cadence.agent_tools.cadence_action_adapter_registry.dispatch",
            new=AsyncMock(return_value={"ok": True, "comment_added": True}),
        ), patch(
            "app.cadence.agent_tools._persist_task_field",
            new=AsyncMock(return_value=True),
        ), patch(
            "app.cadence.agent_tools.mark_project_task_dirty",
            return_value=True,
        ) as dirty_mock:
            result = await tool.execute_with_context(
                {"comment": "Blocked by API dependency"},
                {"session_id": "sess-2", "project_id": "proj-1111-2222"},
            )

        self.assertTrue(result.get("ok"))
        dirty_mock.assert_called_once_with("proj-1111-2222", "ABC-2")

    async def test_assign_marks_dirty_on_success(self):
        tool = CadenceAssignTaskTool()
        session = {
            "session_id": "sess-3",
            "project_id": "proj-1111-2222",
            "current_task_index": 0,
            "tasks": [{"external_id": "ABC-3", "status": "in_progress"}],
        }
        with patch(
            "app.cadence.agent_tools._load_fresh_session",
            new=AsyncMock(return_value=session),
        ), patch(
            "app.cadence.agent_tools.cadence_action_adapter_registry.dispatch",
            new=AsyncMock(return_value={"ok": True, "assignee_updated": True}),
        ), patch(
            "app.cadence.agent_tools.mark_project_task_dirty",
            return_value=True,
        ) as dirty_mock:
            result = await tool.execute_with_context(
                {"assignee": "me"},
                {"session_id": "sess-3", "project_id": "proj-1111-2222"},
            )

        self.assertTrue(result.get("ok"))
        dirty_mock.assert_called_once_with("proj-1111-2222", "ABC-3")


if __name__ == "__main__":
    unittest.main()
