import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.responses import JSONResponse

from app.integrations.slack import command_handlers
from app.integrations.slack.interactions_router import (
    handle_project_switch_modal_submission,
    process_slack_interaction_payload,
)


class SlackProjectSwitchModalTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_switch_opens_modal_when_trigger_available(self):
        payload = {
            "text": "switch",
            "user_id": "U123",
            "team_id": "T123",
            "channel_id": "D123",
            "trigger_id": "1337.abc",
        }
        candidate_projects = [
            {"project_id": "proj-1111-1111", "project_name": "Alpha"},
            {"project_id": "proj-2222-2222", "project_name": "Beta"},
        ]
        with patch(
            "app.integrations.slack.command_handlers._list_member_projects",
            new=AsyncMock(return_value=(candidate_projects, "proj-1111-1111", "user@example.com", None)),
        ), patch(
            "app.integrations.slack.command_handlers._open_project_switch_modal",
            new=AsyncMock(return_value=True),
        ):
            response = await command_handlers.dispatch_command(payload)

        self.assertEqual(response.get("response_type"), "ephemeral")
        self.assertIn("Opening project switcher", response.get("text", ""))

    async def test_interactions_router_routes_project_switch_modal_submission(self):
        payload = {
            "type": "view_submission",
            "view": {"callback_id": "project_switch_modal"},
        }
        expected = JSONResponse(content={"response_action": "clear"}, status_code=200)
        with patch(
            "app.integrations.slack.interactions_router.handle_project_switch_modal_submission",
            new=AsyncMock(return_value=expected),
        ) as submit_mock:
            response = await process_slack_interaction_payload(payload)

        self.assertEqual(response.status_code, 200)
        submit_mock.assert_awaited_once_with(payload)

    async def test_interactions_router_opens_modal_for_project_switch_button_action(self):
        payload = {
            "type": "block_actions",
            "team": {"id": "T123"},
            "user": {"id": "U123"},
            "channel": {"id": "D123"},
            "trigger_id": "1337.abc",
            "actions": [{"action_id": "project_switch_open_modal"}],
        }
        with patch(
            "app.integrations.slack.interactions_router.open_project_switch_modal_for_user",
            new=AsyncMock(return_value=True),
        ) as open_modal:
            response = await process_slack_interaction_payload(payload)

        self.assertEqual(response.status_code, 200)
        open_modal.assert_awaited_once()

    async def test_project_switch_modal_submission_persists_and_clears(self):
        payload = {
            "type": "view_submission",
            "team": {"id": "T123"},
            "user": {"id": "U123"},
            "view": {
                "callback_id": "project_switch_modal",
                "state": {
                    "values": {
                        "project_block": {
                            "project_select": {
                                "selected_option": {"value": "proj-2222-2222"}
                            }
                        }
                    }
                },
            },
        }
        fake_db = SimpleNamespace(
            projects=SimpleNamespace(
                find_one=AsyncMock(
                    return_value={
                        "project_id": "proj-2222-2222",
                        "integrations": {"slack": {"access_token": "encrypted"}},
                    }
                )
            )
        )
        fake_slack_client = SimpleNamespace(chat_postMessage=AsyncMock(return_value={"ok": True}))
        with patch(
            "app.integrations.slack.interactions_router.get_database",
            return_value=fake_db,
        ), patch(
            "app.integrations.slack.interactions_router.list_slack_member_projects",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    ok=True,
                    projects=[{"project_id": "proj-2222-2222", "project_name": "Beta"}],
                    user_email="user@example.com",
                )
            ),
        ), patch(
            "app.integrations.slack.interactions_router.persist_slack_active_project",
            new=AsyncMock(return_value=True),
        ) as persist_mock, patch(
            "app.integrations.slack.interactions_router.slack_bot_service.get_slack_client",
            return_value=fake_slack_client,
        ):
            response = await handle_project_switch_modal_submission(payload)

        self.assertEqual(response.status_code, 200)
        self.assertIn("clear", response.body.decode("utf-8"))
        persist_mock.assert_awaited_once()
        fake_slack_client.chat_postMessage.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
