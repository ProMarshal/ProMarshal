import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.cortex.tools.providers.jira_tools import JiraCreateTaskTool, JiraUpdateParentTool


def _jira_issue(issue_key: str, issue_type: str) -> dict:
    return {
        "key": issue_key,
        "fields": {
            "summary": f"{issue_key} summary",
            "status": {"name": "To Do"},
            "priority": {"name": "Medium"},
            "issuetype": {"name": issue_type},
            "project": {"key": "PRM"},
            "updated": "2026-04-02T00:00:00.000+0000",
            "assignee": None,
            "comment": {"comments": []},
            "parent": None,
            "description": None,
        },
    }


class JiraParentUpdateToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_with_invalid_parent_key_asks_for_parent_clarification(self):
        project = {
            "project_id": "proj-1000-1000",
            "integrations": {"jira": {"default_project_key": "PRM", "site_url": "https://example.atlassian.net"}},
        }
        tool = JiraCreateTaskTool()
        args = {
            "title": "Track release checklist",
            "provider_type": "Task",
            "parent_external_id": "VALUE1-2",
            "operation_id": "op_parent_invalid_create_001",
        }
        execution_context = {
            "project_id": "proj-1000-1000",
            "user_id": "u-123",
            "user_message": "create task track release checklist under value1-2",
            "latest_user_message": "create task track release checklist under value1-2",
        }

        with patch(
            "app.cortex.tools.providers.jira_tools.load_project",
            new=AsyncMock(return_value=project),
        ), patch(
            "app.cortex.tools.providers.jira_tools.resolve_parent_rule_filter",
            new=AsyncMock(
                return_value={
                    "parent_allowed": True,
                    "parent_required": False,
                    "allowed_parent_provider_types": ["Epic"],
                }
            ),
        ), patch(
            "app.cortex.tools.providers.jira_tools.resolve_parent_reference",
            new=AsyncMock(
                return_value={
                    "exists": False,
                    "source": "unverified",
                    "reason": "parent_not_found",
                    "parent_external_id": "VALUE1-2",
                }
            ),
        ), patch(
            "app.cortex.tools.providers.jira_tools.queue_work_item_parent_clarification",
            new=AsyncMock(
                return_value={
                    "response_text": "Here are the most relevant parent options for this task:\n1. PRM-2 (Epic, To Do) - Platform foundation",
                    "candidates": [{"external_id": "PRM-2", "title": "Platform foundation", "provider_type": "Epic"}],
                }
            ),
        ), patch(
            "app.cortex.tools.providers.jira_tools.TaskService.create_task_with_sync_status",
            new=AsyncMock(),
        ) as create_call:
            result = await tool.execute_with_context(args, execution_context)

        self.assertTrue(bool(result.get("ok")))
        self.assertTrue(bool(result.get("requires_clarification")))
        self.assertEqual(str(result.get("clarification_type") or ""), "work_item_parent")
        self.assertIn("not available", str(result.get("response_text") or "").lower())
        create_call.assert_not_awaited()

    async def test_create_optional_parent_adds_best_practice_recommendation(self):
        project = {
            "project_id": "proj-1000-1000",
            "integrations": {"jira": {"default_project_key": "PRM", "site_url": "https://example.atlassian.net"}},
        }
        tool = JiraCreateTaskTool()
        args = {
            "title": "Track release checklist",
            "provider_type": "Task",
            "operation_id": "op_optional_parent_note_001",
        }
        execution_context = {
            "project_id": "proj-1000-1000",
            "user_id": "u-123",
            "user_message": "create task track release checklist",
        }
        create_result = SimpleNamespace(
            task={
                "external_id": "PRM-120",
                "provider_type": "Task",
                "status": "todo",
                "title": "Track release checklist",
            },
            brain_sync_succeeded=True,
            sprint_placement=None,
        )
        with patch(
            "app.cortex.tools.providers.jira_tools.load_project",
            new=AsyncMock(return_value=project),
        ), patch(
            "app.cortex.tools.providers.jira_tools.resolve_parent_rule_filter",
            new=AsyncMock(
                return_value={
                    "parent_allowed": True,
                    "parent_required": False,
                    "allowed_parent_provider_types": ["Epic"],
                }
            ),
        ), patch(
            "app.cortex.tools.providers.jira_tools.resolve_parent_reference",
            new=AsyncMock(return_value={"exists": True, "source": "brain", "parent_external_id": "PRM-2"}),
        ), patch(
            "app.cortex.tools.providers.jira_tools.TaskService.create_task_with_sync_status",
            new=AsyncMock(return_value=create_result),
        ):
            result = await tool.execute_with_context(args, execution_context)

        self.assertTrue(bool(result.get("ok")))
        self.assertIn("parent_mapping_recommendation", result)
        summary = str(((result.get("committed_action") or {}).get("summary") or ""))
        self.assertIn("Best practice: you can map this under a parent anytime", summary)

    async def test_update_parent_rejects_invalid_parent_type_with_validation_message(self):
        project = {
            "project_id": "proj-1000-1000",
            "integrations": {"jira": {"default_project_key": "PRM", "site_url": "https://example.atlassian.net"}},
        }
        tool = JiraUpdateParentTool()
        args = {
            "external_id": "PRM-120",
            "parent_external_id": "PRM-2",
            "operation_id": "op_parent_update_invalid_001",
        }
        execution_context = {"project_id": "proj-1000-1000", "user_id": "u-123"}

        adapter = AsyncMock()
        adapter.ensure_token_valid = AsyncMock(return_value=None)
        adapter.access_token = "token"
        adapter.cloud_id = "cloud"
        adapter.site_url = "https://example.atlassian.net"

        with patch(
            "app.cortex.tools.providers.jira_tools.load_project",
            new=AsyncMock(return_value=project),
        ), patch(
            "app.cortex.tools.providers.jira_tools.resolve_parent_reference",
            new=AsyncMock(return_value={"exists": True, "source": "brain", "parent_external_id": "PRM-2"}),
        ), patch(
            "app.cortex.tools.providers.jira_tools.JiraAdapter",
            return_value=adapter,
        ), patch(
            "app.cortex.tools.providers.jira_tools.jira_oauth_service.get_issue",
            new=AsyncMock(side_effect=[_jira_issue("PRM-120", "Task"), _jira_issue("PRM-2", "Epic")]),
        ), patch(
            "app.cortex.tools.providers.jira_tools.resolve_parent_rule_filter",
            new=AsyncMock(
                return_value={
                    "parent_allowed": True,
                    "parent_required": False,
                    "allowed_parent_provider_types": ["Story"],
                }
            ),
        ):
            result = await tool.execute_with_context(args, execution_context)

        self.assertFalse(bool(result.get("ok")))
        self.assertIn("not allowed", str(result.get("user_message") or "").lower())

    async def test_update_parent_succeeds_when_link_is_valid(self):
        project = {
            "project_id": "proj-1000-1000",
            "integrations": {"jira": {"default_project_key": "PRM", "site_url": "https://example.atlassian.net"}},
        }
        tool = JiraUpdateParentTool()
        args = {
            "external_id": "PRM-120",
            "parent_external_id": "PRM-3",
            "operation_id": "op_parent_update_success_001",
        }
        execution_context = {"project_id": "proj-1000-1000", "user_id": "u-123"}

        adapter = AsyncMock()
        adapter.ensure_token_valid = AsyncMock(return_value=None)
        adapter.access_token = "token"
        adapter.cloud_id = "cloud"
        adapter.site_url = "https://example.atlassian.net"

        mutation_result = SimpleNamespace(
            task={
                "external_id": "PRM-120",
                "provider_type": "Task",
                "status": "todo",
                "parent_key": "PRM-3",
            },
            parent_updated=True,
            brain_sync_succeeded=True,
        )

        with patch(
            "app.cortex.tools.providers.jira_tools.load_project",
            new=AsyncMock(return_value=project),
        ), patch(
            "app.cortex.tools.providers.jira_tools.resolve_parent_reference",
            new=AsyncMock(return_value={"exists": True, "source": "brain", "parent_external_id": "PRM-3"}),
        ), patch(
            "app.cortex.tools.providers.jira_tools.JiraAdapter",
            return_value=adapter,
        ), patch(
            "app.cortex.tools.providers.jira_tools.jira_oauth_service.get_issue",
            new=AsyncMock(side_effect=[_jira_issue("PRM-120", "Task"), _jira_issue("PRM-3", "Story")]),
        ), patch(
            "app.cortex.tools.providers.jira_tools.resolve_parent_rule_filter",
            new=AsyncMock(
                return_value={
                    "parent_allowed": True,
                    "parent_required": False,
                    "allowed_parent_provider_types": ["Task", "Story", "Epic"],
                }
            ),
        ), patch(
            "app.cortex.tools.providers.jira_tools.TaskService.mutate_task",
            new=AsyncMock(return_value=mutation_result),
        ):
            result = await tool.execute_with_context(args, execution_context)

        self.assertTrue(bool(result.get("ok")))
        self.assertTrue(bool(result.get("parent_updated")))
        self.assertEqual(result.get("parent_external_id"), "PRM-3")
        self.assertIn("Mapped PRM-120 under PRM-3", str(((result.get("committed_action") or {}).get("summary") or "")))

    async def test_update_parent_rejects_when_parent_reference_not_available(self):
        project = {
            "project_id": "proj-1000-1000",
            "integrations": {"jira": {"default_project_key": "PRM", "site_url": "https://example.atlassian.net"}},
        }
        tool = JiraUpdateParentTool()
        args = {
            "external_id": "PRM-120",
            "parent_external_id": "VALUE1-2",
            "operation_id": "op_parent_update_missing_parent_001",
        }
        execution_context = {"project_id": "proj-1000-1000", "user_id": "u-123"}

        with patch(
            "app.cortex.tools.providers.jira_tools.load_project",
            new=AsyncMock(return_value=project),
        ), patch(
            "app.cortex.tools.providers.jira_tools.resolve_parent_reference",
            new=AsyncMock(
                return_value={
                    "exists": False,
                    "source": "unverified",
                    "reason": "parent_not_found",
                    "parent_external_id": "VALUE1-2",
                }
            ),
        ):
            result = await tool.execute_with_context(args, execution_context)

        self.assertFalse(bool(result.get("ok")))
        self.assertEqual(str(result.get("error_code") or ""), "invalid_parent_reference")
        self.assertIn("not available", str(result.get("user_message") or "").lower())


if __name__ == "__main__":
    unittest.main()
