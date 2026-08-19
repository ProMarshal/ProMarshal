import unittest
from unittest.mock import patch

from app.cortex.domain.tasks.query_intent import AssigneeMode
from app.cortex.tools.providers.jira_tools import JiraSearchTasksInput, JiraSearchTasksTool


class _RecordingJiraSearchTasksTool(JiraSearchTasksTool):
    def __init__(self):
        super().__init__()
        self.recorded_call = {}

    async def _search_brain_tasks(
        self,
        *,
        project_id,
        project_context,
        jira_project_key,
        jira_site_url,
        parsed,
        assignee_mode,
        include_unassigned,
        assignee_hint,
        assignee_email,
        assignee_account_id,
        assignee_name_exact=None,
    ):
        self.recorded_call = {
            "project_id": project_id,
            "jira_project_key": jira_project_key,
            "jira_site_url": jira_site_url,
            "assignee_mode": assignee_mode,
            "include_unassigned": include_unassigned,
            "assignee_hint": assignee_hint,
            "assignee_email": assignee_email,
            "assignee_account_id": assignee_account_id,
            "assignee_name_exact": assignee_name_exact,
        }
        return []

    def _resolved_read_source_policy(self, execution_context):
        return {"effective_mode": "brain_only"}


class JiraSearchSelfScopeGuardrailTests(unittest.IsolatedAsyncioTestCase):
    async def test_self_scope_message_forces_me_resolution_path(self):
        tool = _RecordingJiraSearchTasksTool()
        parsed = JiraSearchTasksInput(max_results=20)
        project = {
            "project_id": "proj-1",
            "integrations": {
                "jira": {
                    "default_project_key": "SCRUM",
                    "site_url": "https://example.atlassian.net",
                }
            },
            "members": [
                {
                    "status": "active",
                    "email": "member@example.com",
                    "integration_ids": {
                        "slack_user_id": "U123",
                        "jira_account_id": "jira-123",
                    },
                }
            ],
        }

        with patch("app.cortex.tools.providers.jira_tools.load_project", autospec=True, return_value=project):
            result = await tool._execute_validated(
                parsed=parsed,
                execution_context={
                    "project_id": "proj-1",
                    "user_id": "U123",
                    "latest_user_message": "list my tasks",
                },
            )

        self.assertTrue(result.get("ok"))
        self.assertEqual(tool.recorded_call.get("assignee_mode"), AssigneeMode.SPECIFIC)
        self.assertEqual(tool.recorded_call.get("assignee_email"), "member@example.com")
        self.assertEqual(tool.recorded_call.get("assignee_account_id"), "jira-123")
        self.assertIsNone(tool.recorded_call.get("assignee_name_exact"))

    async def test_non_self_scope_message_keeps_any_assignee_mode(self):
        tool = _RecordingJiraSearchTasksTool()
        parsed = JiraSearchTasksInput(max_results=20)
        project = {
            "project_id": "proj-1",
            "integrations": {
                "jira": {
                    "default_project_key": "SCRUM",
                    "site_url": "https://example.atlassian.net",
                }
            },
            "members": [
                {
                    "status": "active",
                    "email": "member@example.com",
                    "integration_ids": {
                        "slack_user_id": "U123",
                        "jira_account_id": "jira-123",
                    },
                }
            ],
        }

        with patch("app.cortex.tools.providers.jira_tools.load_project", autospec=True, return_value=project):
            result = await tool._execute_validated(
                parsed=parsed,
                execution_context={
                    "project_id": "proj-1",
                    "user_id": "U123",
                    "latest_user_message": "list tasks",
                },
            )

        self.assertTrue(result.get("ok"))
        self.assertEqual(tool.recorded_call.get("assignee_mode"), AssigneeMode.ANY)
        self.assertFalse(tool.recorded_call.get("include_unassigned"))
        self.assertIsNone(tool.recorded_call.get("assignee_email"))
        self.assertIsNone(tool.recorded_call.get("assignee_account_id"))

    async def test_live_search_uses_direct_account_id_without_email_lookup(self):
        tool = JiraSearchTasksTool()
        project = {
            "project_id": "proj-1",
            "integrations": {
                "jira": {
                    "default_project_key": "SCRUM",
                    "cloud_id": "cloud",
                    "site_url": "https://example.atlassian.net",
                    "access_token": "enc",
                }
            },
        }

        captured = {}

        class _FakeJiraAdapter:
            def __init__(self, _project):
                self.access_token = "token"
                self.cloud_id = "cloud"
                self.site_url = "https://example.atlassian.net"

            async def ensure_token_valid(self):
                return None

        async def _fake_search_issues(*, access_token, cloud_id, jql, max_results):
            captured["jql"] = jql
            return []

        with (
            patch("app.cortex.tools.providers.jira_tools.JiraAdapter", _FakeJiraAdapter),
            patch("app.cortex.tools.providers.jira_tools.jira_oauth_service.search_issues", autospec=True, side_effect=_fake_search_issues),
        ):
            await tool._search_jira_live_tasks(
                project=project,
                jira_project_key="SCRUM",
                parsed=JiraSearchTasksInput(max_results=10),
                assignee_mode=AssigneeMode.SPECIFIC,
                include_unassigned=False,
                assignee_hint=None,
                assignee_email=None,
                assignee_account_id="acct-123",
            )

        self.assertIn('assignee = "acct-123"', captured.get("jql", ""))

    async def test_live_search_assigned_only_excludes_unassigned_in_jql(self):
        tool = JiraSearchTasksTool()
        project = {
            "project_id": "proj-1",
            "integrations": {
                "jira": {
                    "default_project_key": "SCRUM",
                    "cloud_id": "cloud",
                    "site_url": "https://example.atlassian.net",
                    "access_token": "enc",
                }
            },
        }

        captured = {}

        class _FakeJiraAdapter:
            def __init__(self, _project):
                self.access_token = "token"
                self.cloud_id = "cloud"
                self.site_url = "https://example.atlassian.net"

            async def ensure_token_valid(self):
                return None

        async def _fake_search_issues(*, access_token, cloud_id, jql, max_results):
            captured["jql"] = jql
            return []

        with (
            patch("app.cortex.tools.providers.jira_tools.JiraAdapter", _FakeJiraAdapter),
            patch("app.cortex.tools.providers.jira_tools.jira_oauth_service.search_issues", autospec=True, side_effect=_fake_search_issues),
        ):
            await tool._search_jira_live_tasks(
                project=project,
                jira_project_key="SCRUM",
                parsed=JiraSearchTasksInput(max_results=10),
                assignee_mode=AssigneeMode.ANY,
                include_unassigned=False,
                assignee_hint=None,
                assignee_email=None,
                assignee_account_id=None,
            )

        self.assertIn("assignee is not EMPTY", captured.get("jql", ""))

    async def test_live_search_due_date_missing_adds_duedate_empty_clause(self):
        tool = JiraSearchTasksTool()
        project = {
            "project_id": "proj-1",
            "integrations": {
                "jira": {
                    "default_project_key": "SCRUM",
                    "cloud_id": "cloud",
                    "site_url": "https://example.atlassian.net",
                    "access_token": "enc",
                }
            },
        }

        captured = {}

        class _FakeJiraAdapter:
            def __init__(self, _project):
                self.access_token = "token"
                self.cloud_id = "cloud"
                self.site_url = "https://example.atlassian.net"

            async def ensure_token_valid(self):
                return None

        async def _fake_search_issues(*, access_token, cloud_id, jql, max_results):
            captured["jql"] = jql
            return []

        with (
            patch("app.cortex.tools.providers.jira_tools.JiraAdapter", _FakeJiraAdapter),
            patch("app.cortex.tools.providers.jira_tools.jira_oauth_service.search_issues", autospec=True, side_effect=_fake_search_issues),
        ):
            await tool._search_jira_live_tasks(
                project=project,
                jira_project_key="SCRUM",
                parsed=JiraSearchTasksInput(max_results=10, due_date_mode="missing"),
                assignee_mode=AssigneeMode.ANY,
                include_unassigned=True,
                assignee_hint=None,
                assignee_email=None,
                assignee_account_id=None,
            )

        self.assertIn("duedate is EMPTY", captured.get("jql", ""))


if __name__ == "__main__":
    unittest.main()
