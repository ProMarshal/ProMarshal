import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from app.tasks.schemas import CreateTaskDTO, UpdateTaskDTO
from app.tasks.service import TaskService


def _jira_task(task_key: str) -> dict:
    now = datetime.utcnow()
    return {
        "external_id": task_key,
        "provider": "jira",
        "title": "Task",
        "status": "todo",
        "assignee_email": None,
        "assignee_name": None,
        "priority": "medium",
        "due_date": None,
        "url": "https://example.atlassian.net/browse/" + task_key,
        "source": "jira",
        "schema_version": "1.0",
        "created_at": now,
        "updated_at": now,
    }


class TaskServiceRecommendationDirtyFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_task_with_sync_failure_marks_dirty(self):
        project = {"project_id": "proj-1111-2222", "project_name": "P1"}
        task_data = CreateTaskDTO(
            title="Create task",
            project_id="proj-1111-2222",
        )

        with patch(
            "app.tasks.service.JiraAdapter.__init__",
            return_value=None,
        ), patch(
            "app.tasks.service.JiraAdapter.create_task",
            new=AsyncMock(return_value=(_jira_task("ABC-101"), None)),
        ), patch(
            "app.tasks.service.BrainSync.sync_task_to_brain",
            new=AsyncMock(side_effect=RuntimeError("sync failed")),
        ), patch(
            "app.tasks.service.mark_project_task_dirty",
            return_value=True,
        ) as mark_dirty_mock:
            result = await TaskService.create_task_with_sync_status(
                task_data=task_data,
                project=project,
                created_by="user@example.com",
            )

        self.assertFalse(result.brain_sync_succeeded)
        mark_dirty_mock.assert_called_once_with("proj-1111-2222", "ABC-101")

    async def test_create_task_with_assignee_sends_assignment_notification(self):
        project = {"project_id": "proj-1111-2222", "project_name": "P1"}
        task_data = CreateTaskDTO(
            title="Create task",
            project_id="proj-1111-2222",
        )
        created_task = _jira_task("ABC-102")
        created_task["assignee_email"] = "member@example.com"
        created_task["assignee_name"] = "Member One"

        with patch(
            "app.tasks.service.JiraAdapter.__init__",
            return_value=None,
        ), patch(
            "app.tasks.service.JiraAdapter.create_task",
            new=AsyncMock(return_value=(created_task, None)),
        ), patch(
            "app.tasks.service.BrainSync.sync_task_to_brain",
            new=AsyncMock(return_value=(created_task, None)),
        ), patch(
            "app.tasks.service.send_work_item_assignment_notification",
            new=AsyncMock(return_value=True),
        ) as notify_mock:
            result = await TaskService.create_task_with_sync_status(
                task_data=task_data,
                project=project,
                created_by="user@example.com",
            )

        self.assertTrue(result.brain_sync_succeeded)
        notify_mock.assert_awaited_once()
        self.assertIsNone(notify_mock.await_args.kwargs["old_task"])
        self.assertEqual(notify_mock.await_args.kwargs["task"]["external_id"], "ABC-102")

    async def test_mutate_task_with_sync_failure_marks_dirty(self):
        project = {"project_id": "proj-1111-2222", "project_name": "P1"}
        task_data = UpdateTaskDTO(
            task_id="ABC-202",
            status="done",
            project_id="proj-1111-2222",
        )

        with patch(
            "app.tasks.service.JiraAdapter.__init__",
            return_value=None,
        ), patch(
            "app.tasks.service.JiraAdapter.mutate_task",
            new=AsyncMock(
                return_value=(_jira_task("ABC-202"), True, False, None, False, False, False, False, False, None)
            ),
        ), patch(
            "app.tasks.service.BrainSync.sync_task_to_brain",
            new=AsyncMock(side_effect=RuntimeError("sync failed")),
        ), patch(
            "app.tasks.service.mark_project_task_dirty",
            return_value=True,
        ) as mark_dirty_mock:
            result = await TaskService.mutate_task(
                task_data=task_data,
                project=project,
                updated_by="user@example.com",
            )

        self.assertFalse(result.brain_sync_succeeded)
        mark_dirty_mock.assert_called_once_with("proj-1111-2222", "ABC-202")

    async def test_mutate_task_with_assignee_change_sends_assignment_notification(self):
        project = {"project_id": "proj-1111-2222", "project_name": "P1"}
        task_data = UpdateTaskDTO(
            task_id="ABC-203",
            status="done",
            project_id="proj-1111-2222",
        )
        updated_task = _jira_task("ABC-203")
        updated_task["assignee_email"] = "new@example.com"
        updated_task["assignee_name"] = "New Assignee"
        previous_task = {
            "external_id": "ABC-203",
            "assignee_email": "old@example.com",
            "assignee_name": "Old Assignee",
        }

        with patch(
            "app.tasks.service.JiraAdapter.__init__",
            return_value=None,
        ), patch(
            "app.tasks.service.JiraAdapter.mutate_task",
            new=AsyncMock(
                return_value=(updated_task, True, False, None, False, False, False, False, False, None)
            ),
        ), patch(
            "app.tasks.service.BrainSync.sync_task_to_brain",
            new=AsyncMock(return_value=(updated_task, previous_task)),
        ), patch(
            "app.tasks.service.send_work_item_assignment_notification",
            new=AsyncMock(return_value=True),
        ) as notify_mock:
            result = await TaskService.mutate_task(
                task_data=task_data,
                project=project,
                updated_by="user@example.com",
            )

        self.assertTrue(result.brain_sync_succeeded)
        notify_mock.assert_awaited_once()
        self.assertEqual(notify_mock.await_args.kwargs["old_task"]["assignee_email"], "old@example.com")
        self.assertEqual(notify_mock.await_args.kwargs["task"]["assignee_email"], "new@example.com")

    async def test_mutate_task_passes_transition_id_to_jira_adapter(self):
        project = {"project_id": "proj-1111-2222", "project_name": "P1"}
        task_data = UpdateTaskDTO(
            task_id="ABC-204",
            status="In Progress",
            transition_id="31",
            project_id="proj-1111-2222",
        )

        with patch(
            "app.tasks.service.JiraAdapter.__init__",
            return_value=None,
        ), patch(
            "app.tasks.service.JiraAdapter.mutate_task",
            new=AsyncMock(
                return_value=(_jira_task("ABC-204"), True, False, None, False, False, False, False, False, None)
            ),
        ) as mutate_mock, patch(
            "app.tasks.service.BrainSync.sync_task_to_brain",
            new=AsyncMock(return_value=(_jira_task("ABC-204"), None)),
        ), patch(
            "app.tasks.service.send_work_item_assignment_notification",
            new=AsyncMock(return_value=False),
        ):
            await TaskService.mutate_task(
                task_data=task_data,
                project=project,
                updated_by="user@example.com",
            )

        self.assertEqual(mutate_mock.await_args.kwargs["transition_id"], "31")


if __name__ == "__main__":
    unittest.main()
