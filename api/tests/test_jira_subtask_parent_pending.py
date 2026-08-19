import unittest
from unittest.mock import AsyncMock, patch

from app.cortex.tools.providers.jira_tools import JiraCreateTaskTool


class JiraSubtaskParentPendingTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_subtask_without_parent_queues_pending_instead_of_creating(self):
        project = {
            "project_id": "proj-1000-1000",
            "integrations": {"jira": {"default_project_key": "PRM", "site_url": "https://example.atlassian.net"}},
        }
        tool = JiraCreateTaskTool()
        args = {
            "title": "Add retry guard",
            "provider_type": "subtask",
            "operation_id": "op_subtask_pending_001",
        }
        execution_context = {
            "project_id": "proj-1000-1000",
            "user_id": "u-123",
            "user_message": "create subtask add retry guard",
        }

        with patch(
            "app.cortex.tools.providers.jira_tools.load_project",
            new=AsyncMock(return_value=project),
        ), patch(
            "app.cortex.tools.providers.jira_tools.queue_work_item_parent_clarification",
            new=AsyncMock(
                return_value={
                    "queued": True,
                    "response_text": "Please pick a parent.",
                    "candidates": [{"external_id": "PRM-10", "title": "Auth flow"}],
                }
            ),
        ) as queue_pending, patch(
            "app.cortex.tools.providers.jira_tools.TaskService.create_task_with_sync_status",
            new=AsyncMock(side_effect=AssertionError("should_not_create_without_parent")),
        ):
            result = await tool.execute_with_context(args, execution_context)

        self.assertTrue(bool(result.get("ok")))
        self.assertTrue(bool(result.get("requires_clarification")))
        self.assertEqual(result.get("clarification_type"), "work_item_parent")
        self.assertIn("Please pick a parent", str(result.get("response_text")))
        queue_pending.assert_awaited_once()

    async def test_subtask_pending_queues_with_internal_actor_user_id(self):
        project = {
            "project_id": "proj-1000-1000",
            "integrations": {"jira": {"default_project_key": "PRM", "site_url": "https://example.atlassian.net"}},
            "members": [
                {
                    "user_id": "member1@example.com",
                    "email": "member1@example.com",
                    "name": "Member One",
                    "status": "active",
                    "integration_ids": {"slack_user_id": "U09KWTH7GCD"},
                }
            ],
        }
        tool = JiraCreateTaskTool()
        args = {
            "title": "Add retry guard",
            "provider_type": "subtask",
            "operation_id": "op_subtask_pending_002",
        }
        execution_context = {
            "project_id": "proj-1000-1000",
            "user_id": "U09KWTH7GCD",
            "user_message": "create subtask add retry guard",
            "actor_email": "member1@example.com",
        }

        with patch(
            "app.cortex.tools.providers.jira_tools.load_project",
            new=AsyncMock(return_value=project),
        ), patch(
            "app.cortex.tools.providers.jira_tools.queue_work_item_parent_clarification",
            new=AsyncMock(
                return_value={
                    "queued": True,
                    "response_text": "Please pick a parent.",
                    "candidates": [{"external_id": "PRM-10", "title": "Auth flow"}],
                }
            ),
        ) as queue_pending, patch(
            "app.cortex.tools.providers.jira_tools.TaskService.create_task_with_sync_status",
            new=AsyncMock(side_effect=AssertionError("should_not_create_without_parent")),
        ):
            result = await tool.execute_with_context(args, execution_context)

        self.assertTrue(bool(result.get("ok")))
        queue_pending.assert_awaited_once()
        called_kwargs = dict(queue_pending.await_args.kwargs or {})
        self.assertEqual(called_kwargs.get("actor_user_id"), "member1@example.com")

    async def test_subtask_pending_sends_intermediate_progress_message_for_slack(self):
        project = {
            "project_id": "proj-1000-1000",
            "integrations": {"jira": {"default_project_key": "PRM", "site_url": "https://example.atlassian.net"}},
        }
        tool = JiraCreateTaskTool()
        args = {
            "title": "Analyze backlog alignment",
            "provider_type": "subtask",
            "operation_id": "op_subtask_pending_003",
        }
        execution_context = {
            "project_id": "proj-1000-1000",
            "user_id": "u-123",
            "user_message": "create subtask analyze backlog alignment",
            "source": "slack_dm",
            "channel_id": "D123",
            "thread_ts": "1712345.001",
        }

        with patch(
            "app.cortex.tools.providers.jira_tools.load_project",
            new=AsyncMock(return_value=project),
        ), patch(
            "app.cortex.tools.providers.jira_tools.queue_work_item_parent_clarification",
            new=AsyncMock(
                return_value={
                    "queued": True,
                    "response_text": "Please pick a parent.",
                    "candidates": [{"external_id": "PRM-10", "title": "Auth flow"}],
                }
            ),
        ) as queue_pending, patch(
            "app.cortex.tools.providers.jira_tools.CortexResponseService.send",
            new=AsyncMock(return_value={"ok": True}),
        ) as send_progress, patch(
            "app.cortex.tools.providers.jira_tools.TaskService.create_task_with_sync_status",
            new=AsyncMock(side_effect=AssertionError("should_not_create_without_parent")),
        ):
            result = await tool.execute_with_context(args, execution_context)

        self.assertTrue(bool(result.get("ok")))
        self.assertTrue(bool(result.get("requires_clarification")))
        queue_pending.assert_awaited_once()
        send_progress.assert_awaited_once()
        payload = send_progress.await_args.args[0]
        self.assertIn("parent", str(payload.text).lower())

    async def test_subtask_pending_does_not_send_progress_for_non_slack_source(self):
        project = {
            "project_id": "proj-1000-1000",
            "integrations": {"jira": {"default_project_key": "PRM", "site_url": "https://example.atlassian.net"}},
        }
        tool = JiraCreateTaskTool()
        args = {
            "title": "Analyze backlog alignment",
            "provider_type": "subtask",
            "operation_id": "op_subtask_pending_004",
        }
        execution_context = {
            "project_id": "proj-1000-1000",
            "user_id": "u-123",
            "user_message": "create subtask analyze backlog alignment",
            "source": "cortex_api",
            "channel_id": "D123",
            "thread_ts": "1712345.001",
        }

        with patch(
            "app.cortex.tools.providers.jira_tools.load_project",
            new=AsyncMock(return_value=project),
        ), patch(
            "app.cortex.tools.providers.jira_tools.queue_work_item_parent_clarification",
            new=AsyncMock(
                return_value={
                    "queued": True,
                    "response_text": "Please pick a parent.",
                    "candidates": [{"external_id": "PRM-10", "title": "Auth flow"}],
                }
            ),
        ) as queue_pending, patch(
            "app.cortex.tools.providers.jira_tools.CortexResponseService.send",
            new=AsyncMock(return_value={"ok": True}),
        ) as send_progress, patch(
            "app.cortex.tools.providers.jira_tools.TaskService.create_task_with_sync_status",
            new=AsyncMock(side_effect=AssertionError("should_not_create_without_parent")),
        ):
            result = await tool.execute_with_context(args, execution_context)

        self.assertTrue(bool(result.get("ok")))
        queue_pending.assert_awaited_once()
        send_progress.assert_not_called()


if __name__ == "__main__":
    unittest.main()
