import unittest
from unittest.mock import AsyncMock, patch

from app.cortex.tools.providers.jira_tools import JiraCreateTaskInput, JiraCreateTaskTool


class JiraCreateProviderTypePreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_returns_clarification_error_for_invalid_type(self):
        tool = JiraCreateTaskTool()
        parsed = JiraCreateTaskInput(
            title="UI Testing",
            provider_type="Task",
            operation_id="operation-123",
        )
        project = {
            "project_id": "proj-1111-2222",
            "integrations": {
                "jira": {
                    "default_project_key": "PIT",
                    "site_url": "https://example.atlassian.net",
                    "cloud_id": "cloud-1",
                }
            },
        }

        fake_adapter = type("FakeAdapter", (), {})()
        fake_adapter.preview_issue_type_for_create = AsyncMock(
            return_value={
                "jira_project_key": "PIT",
                "requested_issue_type": "Task",
                "matched_issue_type": None,
                "allowed_issue_types": ["Bug", "Story"],
                "cache_source": "refreshed",
            }
        )
        with patch(
            "app.cortex.tools.providers.jira_tools.JiraAdapter",
            return_value=fake_adapter,
        ):
            with patch.object(
                tool,
                "_rank_provider_type_suggestions",
                AsyncMock(
                    return_value=[
                        {"value": "Bug", "confidence": 0.93, "reason": "matches defect intent"},
                        {"value": "Story", "confidence": 0.42, "reason": "secondary match"},
                    ]
                ),
            ):
                error_payload = await tool._preflight_provider_type_for_create(
                    parsed=parsed,
                    project=project,
                    project_id="proj-1111-2222",
                    user_message="create task ui testing",
                )

        self.assertIsInstance(error_payload, dict)
        self.assertFalse(bool((error_payload or {}).get("ok")))
        self.assertEqual(
            str((error_payload or {}).get("error_code") or ""),
            "provider_type_clarification_required",
        )
        details = (error_payload or {}).get("details") or {}
        self.assertEqual(str(details.get("suggested_value") or ""), "Bug")
        self.assertEqual(list(details.get("allowed_values") or []), ["Bug", "Story"])
        proposed_call = details.get("proposed_tool_call") or {}
        self.assertEqual(str(proposed_call.get("tool_name") or ""), "jira_create_work_item")
        self.assertEqual(
            str((proposed_call.get("args") or {}).get("provider_type") or ""),
            "Task",
        )

    async def test_preflight_accepts_matched_type_and_normalizes_parsed_value(self):
        tool = JiraCreateTaskTool()
        parsed = JiraCreateTaskInput(
            title="UI Testing",
            provider_type="Task",
            operation_id="operation-456",
        )
        project = {
            "project_id": "proj-1111-2222",
            "integrations": {
                "jira": {
                    "default_project_key": "PIT",
                    "site_url": "https://example.atlassian.net",
                    "cloud_id": "cloud-1",
                }
            },
        }

        fake_adapter = type("FakeAdapter", (), {})()
        fake_adapter.preview_issue_type_for_create = AsyncMock(
            return_value={
                "jira_project_key": "PIT",
                "requested_issue_type": "Task",
                "matched_issue_type": "Bug",
                "allowed_issue_types": ["Bug", "Story"],
                "cache_source": "cache",
            }
        )
        with patch(
            "app.cortex.tools.providers.jira_tools.JiraAdapter",
            return_value=fake_adapter,
        ):
            error_payload = await tool._preflight_provider_type_for_create(
                parsed=parsed,
                project=project,
                project_id="proj-1111-2222",
                user_message="create task ui testing",
            )

        self.assertIsNone(error_payload)
        self.assertEqual(parsed.provider_type, "Bug")


if __name__ == "__main__":
    unittest.main()
