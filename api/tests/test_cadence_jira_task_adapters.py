import unittest
from unittest.mock import AsyncMock, patch

from app.cadence.action_adapters.providers.jira_task_adapters import (
    get_jira_cadence_adapters,
    _resolve_assignee_account_id,
    _session_actor_identity,
)


class CadenceJiraTaskAdaptersTests(unittest.IsolatedAsyncioTestCase):
    def test_jira_cadence_adapters_include_task_assign(self):
        capability_ids = {
            str(getattr(adapter, "capability_id", "") or "").strip()
            for adapter in get_jira_cadence_adapters()
        }
        self.assertIn("workitem_assign", capability_ids)

    def test_session_actor_identity_prefers_member_email(self):
        session = {
            "member_email": "sridevi@example.com",
            "member_name": "Sridevi",
            "user_id": "usr-123",
        }
        self.assertEqual(_session_actor_identity(session), "sridevi@example.com")

    def test_session_actor_identity_falls_back_to_name_then_user_id(self):
        self.assertEqual(
            _session_actor_identity({"member_name": "Sridevi", "user_id": "usr-123"}),
            "Sridevi",
        )
        self.assertEqual(_session_actor_identity({"user_id": "usr-123"}), "usr-123")

    async def test_resolve_assignee_prefers_project_member_mapping(self):
        project = {
            "members": [
                {
                    "name": "Sridevi Panneerselvam",
                    "email": "sridevi@example.com",
                    "status": "active",
                    "integration_ids": {"jira_account_id": "jira-123"},
                }
            ]
        }
        with patch(
            "app.cadence.action_adapters.providers.jira_task_adapters.jira_oauth_service.get_workspace_users",
            new=AsyncMock(return_value=[]),
        ):
            account_id = await _resolve_assignee_account_id(
                access_token="token",
                cloud_id="cloud",
                project=project,
                session={"member_email": "sridevi@example.com"},
                assignee_hint="sridevi",
            )
        self.assertEqual(account_id, "jira-123")

    async def test_resolve_assignee_returns_none_for_ambiguous_member_match(self):
        project = {
            "members": [
                {
                    "name": "Sridevi One",
                    "email": "sridevi1@example.com",
                    "status": "active",
                    "integration_ids": {"jira_account_id": "jira-1"},
                },
                {
                    "name": "Sridevi Two",
                    "email": "sridevi2@example.com",
                    "status": "active",
                    "integration_ids": {"jira_account_id": "jira-2"},
                },
            ]
        }
        with patch(
            "app.cadence.action_adapters.providers.jira_task_adapters.jira_oauth_service.get_workspace_users",
            new=AsyncMock(return_value=[]),
        ):
            account_id = await _resolve_assignee_account_id(
                access_token="token",
                cloud_id="cloud",
                project=project,
                session={},
                assignee_hint="sridevi",
            )
        self.assertIsNone(account_id)


if __name__ == "__main__":
    unittest.main()
