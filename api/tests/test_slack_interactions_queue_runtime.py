import importlib
import unittest
from unittest.mock import AsyncMock, patch


interactions_module = importlib.import_module("app.integrations.slack.interactions_router")


class _FakeProjectsCollection:
    async def find_one(self, query):
        return {
            "project_id": query.get("project_id"),
            "project_name": "Demo",
            "integrations": {"jira": {"status": "connected"}},
        }


class _FakeDb:
    projects = _FakeProjectsCollection()


class SlackInteractionsQueueRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_cadence_modal_uses_dramatiq_when_enabled(self):
        payload = {
            "type": "view_submission",
            "view": {"callback_id": "cadence_update_modal"},
        }

        with patch.object(interactions_module, "is_duplicate_cadence_interaction", return_value=False), patch.object(
            interactions_module,
            "dramatiq_enqueue",
            return_value={"accepted": True, "job_id": "dramatiq-msg-1"},
        ) as dramatiq_mock:
            response = await interactions_module.process_slack_interaction_payload(payload)

        self.assertEqual(response.status_code, 200)
        dramatiq_mock.assert_called_once()

    async def test_create_task_modal_uses_dramatiq_when_enabled(self):
        payload = {
            "view": {
                "private_metadata": "proj-1234-5678|team-1|owner@example.com|C123",
                "state": {
                    "values": {
                        "title_block": {"title_input": {"value": "Create release task"}},
                        "description_block": {"description_input": {"value": "desc"}},
                        "assignee_block": {
                            "assignee_select": {"selected_option": {"value": "jira-account-1"}}
                        },
                        "priority_block": {
                            "priority_select": {"selected_option": {"value": "high"}}
                        },
                    }
                },
            },
            "user": {"id": "U123"},
        }

        with patch.object(interactions_module, "get_database", return_value=_FakeDb()), patch.object(
            interactions_module,
            "dramatiq_enqueue",
            return_value={"accepted": True, "job_id": "dramatiq-msg-2"},
        ) as dramatiq_mock:
            response = await interactions_module.handle_create_task_modal_submission(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body.decode("utf-8"), '{"response_action":"clear"}')
        dramatiq_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
