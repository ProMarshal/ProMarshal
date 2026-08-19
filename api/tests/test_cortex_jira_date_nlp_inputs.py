import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.cortex.tools.providers.jira_tools import (
    JiraUpdateDueDateTool,
    JiraUpdateStartDateTool,
)


class CortexJiraDateNlpInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_due_date_accepts_natural_language_today(self):
        tool = JiraUpdateDueDateTool()
        project = {
            "project_id": "proj-1111-2222",
            "timezone": "Asia/Kolkata",
            "integrations": {"jira": {}},
        }
        mutation_result = SimpleNamespace(
            task={"external_id": "ABC-10", "title": "Task"},
            due_date_updated=True,
            brain_sync_succeeded=True,
        )

        with (
            patch("app.cortex.tools.providers.jira_tools.load_project", new=AsyncMock(return_value=project)),
            patch("app.cortex.tools.providers.jira_tools.TaskService.mutate_task", new=AsyncMock(return_value=mutation_result)) as mutate_mock,
            patch("app.cortex.tools.providers.jira_tools.mark_project_task_dirty"),
        ):
            result = await tool.execute_with_context(
                {
                    "external_id": "ABC-10",
                    "due_date": "today",
                    "operation_id": "op-test-due-nlp-1",
                },
                {"project_id": "proj-1111-2222", "user_id": "U1"},
            )

        self.assertTrue(result.get("ok"))
        task_data = mutate_mock.await_args.kwargs.get("task_data")
        self.assertIsNotNone(task_data.due_date)

    async def test_update_start_date_accepts_natural_language_tomorrow(self):
        tool = JiraUpdateStartDateTool()
        project = {
            "project_id": "proj-1111-2222",
            "timezone": "UTC",
            "integrations": {"jira": {}},
        }
        mutation_result = SimpleNamespace(
            task={"external_id": "ABC-11", "title": "Task"},
            start_date_updated=True,
            brain_sync_succeeded=True,
        )

        with (
            patch("app.cortex.tools.providers.jira_tools.load_project", new=AsyncMock(return_value=project)),
            patch("app.cortex.tools.providers.jira_tools.TaskService.mutate_task", new=AsyncMock(return_value=mutation_result)) as mutate_mock,
            patch("app.cortex.tools.providers.jira_tools.mark_project_task_dirty"),
        ):
            result = await tool.execute_with_context(
                {
                    "external_id": "ABC-11",
                    "start_date": "tomorrow",
                    "operation_id": "op-test-start-nlp-1",
                },
                {"project_id": "proj-1111-2222", "user_id": "U1"},
            )

        self.assertTrue(result.get("ok"))
        task_data = mutate_mock.await_args.kwargs.get("task_data")
        self.assertIsNotNone(task_data.start_date)

    async def test_update_due_date_rejects_unparseable_date_text(self):
        tool = JiraUpdateDueDateTool()
        project = {
            "project_id": "proj-1111-2222",
            "timezone": "UTC",
            "integrations": {"jira": {}},
        }

        with patch("app.cortex.tools.providers.jira_tools.load_project", new=AsyncMock(return_value=project)):
            result = await tool.execute_with_context(
                {
                    "external_id": "ABC-12",
                    "due_date": "someday maybe soon-ish",
                    "operation_id": "op-test-due-nlp-2",
                },
                {"project_id": "proj-1111-2222", "user_id": "U1"},
            )

        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_code"), "invalid_datetime_input")


if __name__ == "__main__":
    unittest.main()
