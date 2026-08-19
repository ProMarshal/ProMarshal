import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.cortex.tools.providers.jira_tools import JiraAddCommentTool, JiraUpdateStatusTool


class CortexJiraMutationRecommendationDirtyTests(unittest.IsolatedAsyncioTestCase):
    async def test_jira_add_comment_marks_task_dirty(self):
        tool = JiraAddCommentTool()
        project = {"project_id": "proj-1111-2222", "integrations": {"jira": {}}}
        mutation_result = SimpleNamespace(
            task={"external_id": "ABC-1", "title": "Task"},
            comment_added=True,
            posted_comment_text="@sridevi, please attach the error logs.",
            brain_sync_succeeded=False,
        )

        with (
            patch("app.cortex.tools.providers.jira_tools.load_project", new=AsyncMock(return_value=project)),
            patch("app.cortex.tools.providers.jira_tools.TaskService.mutate_task", new=AsyncMock(return_value=mutation_result)),
            patch("app.cortex.tools.providers.jira_tools.mark_project_task_dirty") as mock_mark_dirty,
        ):
            result = await tool.execute_with_context(
                {"external_id": "ABC-1", "comment": "Waiting on API response", "operation_id": "op-test-01"},
                {"project_id": "proj-1111-2222", "user_id": "U1"},
            )

        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("comment_added"))
        self.assertEqual(
            result.get("posted_comment_text"),
            "@sridevi, please attach the error logs.",
        )
        mock_mark_dirty.assert_called_once_with("proj-1111-2222", "ABC-1")

    async def test_jira_add_comment_normalizes_command_style_comment(self):
        tool = JiraAddCommentTool()
        project = {"project_id": "proj-1111-2222", "integrations": {"jira": {}}}
        mutation_result = SimpleNamespace(
            task={"external_id": "ABC-1", "title": "Task"},
            comment_added=True,
            posted_comment_text="Please attach the error logs to this task.",
            brain_sync_succeeded=True,
        )

        with (
            patch("app.cortex.tools.providers.jira_tools.load_project", new=AsyncMock(return_value=project)),
            patch("app.cortex.tools.providers.jira_tools.TaskService.mutate_task", new=AsyncMock(return_value=mutation_result)) as mutate_mock,
            patch("app.cortex.tools.providers.jira_tools.mark_project_task_dirty"),
        ):
            await tool.execute_with_context(
                {"external_id": "ABC-1", "comment": "ask requestor to attach the error logs to this task", "operation_id": "op-test-03"},
                {"project_id": "proj-1111-2222", "user_id": "U1"},
            )

        call_kwargs = mutate_mock.await_args.kwargs
        task_data = call_kwargs.get("task_data")
        self.assertEqual(task_data.comment, "Please attach the error logs to this task.")

    async def test_jira_update_status_marks_task_dirty(self):
        tool = JiraUpdateStatusTool()
        project = {
            "project_id": "proj-1111-2222",
            "integrations": {"jira": {"default_project_key": "ABC", "site_url": "https://example.atlassian.net"}},
        }
        mutation_result = SimpleNamespace(
            task={"external_id": "ABC-2", "title": "Task"},
            status_updated=True,
            brain_sync_succeeded=False,
            sprint_placement=None,
        )

        with (
            patch("app.cortex.tools.providers.jira_tools.load_project", new=AsyncMock(return_value=project)),
            patch("app.cortex.tools.providers.jira_tools.TaskService.mutate_task", new=AsyncMock(return_value=mutation_result)),
            patch("app.cortex.tools.providers.jira_tools.mark_project_task_dirty") as mock_mark_dirty,
        ):
            result = await tool.execute_with_context(
                {"external_id": "ABC-2", "status": "done", "operation_id": "op-test-02"},
                {"project_id": "proj-1111-2222", "user_id": "U1"},
            )

        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("status_updated"))
        mock_mark_dirty.assert_called_once_with("proj-1111-2222", "ABC-2")


if __name__ == "__main__":
    unittest.main()
