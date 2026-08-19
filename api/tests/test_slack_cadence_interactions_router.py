import unittest
from unittest.mock import AsyncMock, patch

from app.integrations.slack.interactions_router import process_slack_interaction_payload


class SlackCadenceInteractionsRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_update_modal_is_not_blocked_by_dedupe(self):
        payload = {
            "type": "block_actions",
            "actions": [{"action_id": "open_update_modal"}],
        }
        with patch(
            "app.integrations.slack.interactions_router.is_duplicate_cadence_interaction",
            return_value=True,
        ) as dedupe_mock, patch(
            "app.cadence.interaction_handler.handle_button_click_direct",
            new=AsyncMock(return_value=None),
        ) as handler_mock:
            response = await process_slack_interaction_payload(payload)

        self.assertEqual(response.status_code, 200)
        handler_mock.assert_awaited_once_with(payload)
        dedupe_mock.assert_not_called()

    async def test_view_backlog_remains_deduped(self):
        payload = {
            "type": "block_actions",
            "actions": [{"action_id": "view_backlog"}],
        }
        with patch(
            "app.integrations.slack.interactions_router.is_duplicate_cadence_interaction",
            return_value=True,
        ) as dedupe_mock, patch(
            "app.cadence.interaction_handler.handle_button_click_direct",
            new=AsyncMock(return_value=None),
        ) as handler_mock:
            response = await process_slack_interaction_payload(payload)

        self.assertEqual(response.status_code, 200)
        dedupe_mock.assert_called_once_with(payload)
        handler_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
