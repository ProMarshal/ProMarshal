import unittest
from unittest.mock import AsyncMock, patch

from app.cortex.tools.providers.jira_tools import JiraAnalyzeTasksInput, JiraAnalyzeTasksTool


class JiraAnalyzeWorkItemsInputTests(unittest.TestCase):
    def test_analyze_input_normalizes_provider_types(self):
        parsed = JiraAnalyzeTasksInput.model_validate(
            {"provider_types": ["stories", "defects", "sub tasks"]}
        )
        self.assertEqual(parsed.provider_types, ["Story", "Bug", "Sub-task"])


class JiraAnalyzeWorkItemsToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_applies_provider_type_filters(self):
        tool = JiraAnalyzeTasksTool()
        execution_context = {
            "project_id": "proj-1000-1000",
            "user_id": "u-123",
            "user_message": "how is workload distributed for stories",
        }
        project = {"project_id": "proj-1000-1000", "integrations": {"jira": {"default_project_key": "PRM"}}}

        with patch(
            "app.cortex.tools.providers.jira_tools.load_project",
            new=AsyncMock(return_value=project),
        ), patch(
            "app.cortex.tools.providers.jira_tools.TaskService.list_tasks",
            new=AsyncMock(return_value=[]),
        ) as list_mock:
            result = await tool.execute_with_context(
                {"provider_types": ["stories"]},
                execution_context,
            )

        self.assertTrue(bool(result.get("ok")))
        filters = list_mock.call_args.kwargs.get("filters")
        self.assertEqual(list(filters.provider_types or []), ["Story"])

    async def test_analyze_specific_non_email_assignee_uses_name_exact_fallback(self):
        tool = JiraAnalyzeTasksTool()
        execution_context = {
            "project_id": "proj-1000-1000",
            "user_id": "u-123",
            "user_message": "analyze workload for sridevi",
        }
        project = {"project_id": "proj-1000-1000", "integrations": {"jira": {"default_project_key": "PRM"}}}

        with patch(
            "app.cortex.tools.providers.jira_tools.load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tool,
            "_resolve_specific_assignee_email",
            return_value=None,
        ), patch(
            "app.cortex.tools.providers.jira_tools.TaskService.list_tasks",
            new=AsyncMock(return_value=[]),
        ) as list_mock:
            result = await tool.execute_with_context(
                {
                    "assignee_mode": "specific",
                    "assignee_value": "sridevi",
                },
                execution_context,
            )

        self.assertTrue(bool(result.get("ok")))
        filters = list_mock.call_args.kwargs.get("filters")
        self.assertEqual(str(filters.assignee_name_exact or "").lower(), "sridevi")

    async def test_analyze_normalizes_status_buckets_and_marks_overdue_subset(self):
        tool = JiraAnalyzeTasksTool()
        execution_context = {
            "project_id": "proj-1000-1000",
            "user_id": "u-123",
            "user_message": "how is the workload distributed among the team",
        }
        project = {"project_id": "proj-1000-1000", "integrations": {"jira": {"default_project_key": "PRM"}}}
        tasks = [
            {
                "external_id": "PRM-1",
                "title": "Story one",
                "status": "To Do",
                "priority": "Medium",
                "assignee_name": "Prabhu Raj",
                "due_date": "2025-01-01T00:00:00+00:00",
                "is_closed": False,
            },
            {
                "external_id": "PRM-2",
                "title": "Story two",
                "status": "In Progress",
                "priority": "High",
                "assignee_name": "Prabhu Raj",
                "is_closed": False,
            },
        ]

        with patch(
            "app.cortex.tools.providers.jira_tools.load_project",
            new=AsyncMock(return_value=project),
        ), patch(
            "app.cortex.tools.providers.jira_tools.TaskService.list_tasks",
            new=AsyncMock(return_value=tasks),
        ):
            result = await tool.execute_with_context(
                {},
                execution_context,
            )

        self.assertTrue(bool(result.get("ok")))
        by_status = result.get("by_status") or {}
        self.assertEqual(by_status.get("todo"), 1)
        self.assertEqual(by_status.get("in_progress"), 1)
        self.assertTrue(bool(result.get("overdue_is_subset_of_open")))
        assignee_rows = result.get("by_assignee") or []
        prabhu = next((row for row in assignee_rows if row.get("assignee") == "Prabhu Raj"), {})
        self.assertEqual(int(prabhu.get("overdue_count") or 0), 1)


if __name__ == "__main__":
    unittest.main()
