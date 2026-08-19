import unittest
from unittest.mock import AsyncMock, patch

from app.integrations.slack import command_handlers


class SlackActionItemCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_actionitem_create_routes_and_calls_service(self):
        payload = {
            "text": "actionitem create Follow up with ops | owner:u-123 | related:Network topic",
            "user_id": "U123",
            "team_id": "T123",
            "channel_id": "D123",
        }
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "role": "owner", "status": "active", "name": "Owner"},
                {"user_id": "u-123", "role": "member", "status": "active", "name": "Prabhu"},
            ],
        }
        member = {"user_id": "u-123", "role": "member"}
        create_mock = AsyncMock(
            return_value={
                "display_key": "ACTION-9",
                "action_item_id": "ai-9",
                "needs_followup": False,
            }
        )
        with patch.object(command_handlers.settings, "action_items_enabled", True), patch(
            "app.integrations.slack.command_handlers.authenticate_user",
            new=AsyncMock(return_value=(project, member, None, "ok")),
        ), patch(
            "app.integrations.slack.command_handlers.ActionItemService.create",
            new=create_mock,
        ):
            response = await command_handlers.dispatch_command(payload)

        self.assertEqual(response.get("response_type"), "ephemeral")
        self.assertIn("Created action item ACTION-9", response.get("text", ""))
        create_mock.assert_awaited_once()

    async def test_dispatch_action_item_alias_close_routes(self):
        payload = {
            "text": "action-item close ACTION-7 done",
            "user_id": "U123",
            "team_id": "T123",
            "channel_id": "D123",
        }
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "role": "owner", "status": "active", "name": "Owner"},
            ],
        }
        member = {"user_id": "u-owner", "role": "owner"}
        close_mock = AsyncMock(return_value={"display_key": "ACTION-7", "action_item_id": "ai-7"})
        with patch.object(command_handlers.settings, "action_items_enabled", True), patch(
            "app.integrations.slack.command_handlers.authenticate_user",
            new=AsyncMock(return_value=(project, member, None, "ok")),
        ), patch(
            "app.integrations.slack.command_handlers._resolve_action_item_by_ref",
            new=AsyncMock(return_value="ai-7"),
        ), patch(
            "app.integrations.slack.command_handlers.ActionItemService.close",
            new=close_mock,
        ):
            response = await command_handlers.dispatch_command(payload)

        self.assertEqual(response.get("response_type"), "ephemeral")
        self.assertIn("Marked action item ACTION-7 as done", response.get("text", ""))
        close_mock.assert_awaited_once()

    async def test_dispatch_actionitem_assign_routes_and_reassigns_owner(self):
        payload = {
            "text": "actionitem assign ACTION-7 u-456",
            "user_id": "U123",
            "team_id": "T123",
            "channel_id": "D123",
        }
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "role": "owner", "status": "active", "name": "Owner"},
                {"user_id": "u-456", "role": "member", "status": "active", "name": "Prabhu"},
            ],
        }
        member = {"user_id": "u-owner", "role": "owner"}
        update_mock = AsyncMock(return_value={"display_key": "ACTION-7", "action_item_id": "ai-7"})
        with patch.object(command_handlers.settings, "action_items_enabled", True), patch(
            "app.integrations.slack.command_handlers.authenticate_user",
            new=AsyncMock(return_value=(project, member, None, "ok")),
        ), patch(
            "app.integrations.slack.command_handlers._resolve_action_item_by_ref",
            new=AsyncMock(return_value="ai-7"),
        ), patch(
            "app.integrations.slack.command_handlers.ActionItemService.update",
            new=update_mock,
        ):
            response = await command_handlers.dispatch_command(payload)

        self.assertEqual(response.get("response_type"), "ephemeral")
        self.assertIn("Reassigned action item ACTION-7 to u-456", response.get("text", ""))
        update_mock.assert_awaited_once()
        args = update_mock.await_args.args
        self.assertEqual(args[0], "proj-1234-5678")
        self.assertEqual(args[1], "ai-7")
        self.assertEqual(args[2].get("owner_user_id"), "u-456")

    async def test_dispatch_actionitem_assign_ambiguous_name_requests_followup(self):
        payload = {
            "text": "actionitem assign ACTION-7 prabhu",
            "user_id": "U123",
            "team_id": "T123",
            "channel_id": "D123",
        }
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "role": "owner", "status": "active", "name": "Owner"},
                {"user_id": "u-111", "role": "member", "status": "active", "name": "Prabhu S"},
                {"user_id": "u-222", "role": "member", "status": "active", "name": "Prabhu K"},
            ],
        }
        member = {"user_id": "u-owner", "role": "owner"}
        with patch.object(command_handlers.settings, "action_items_enabled", True), patch(
            "app.integrations.slack.command_handlers.authenticate_user",
            new=AsyncMock(return_value=(project, member, None, "ok")),
        ), patch(
            "app.integrations.slack.command_handlers._resolve_action_item_by_ref",
            new=AsyncMock(return_value="ai-7"),
        ), patch(
            "app.integrations.slack.command_handlers.ActionItemService.update",
            new=AsyncMock(),
        ) as update_mock:
            response = await command_handlers.dispatch_command(payload)

        self.assertEqual(response.get("response_type"), "ephemeral")
        self.assertIn("is ambiguous", response.get("text", ""))
        self.assertIn("u-111", response.get("text", ""))
        self.assertIn("u-222", response.get("text", ""))
        update_mock.assert_not_awaited()

    async def test_dispatch_actionitem_update_is_not_supported(self):
        payload = {
            "text": "actionitem update ACTION-7 owner:u-999",
            "user_id": "U123",
            "team_id": "T123",
            "channel_id": "D123",
        }
        response = await command_handlers.dispatch_command(payload)
        self.assertEqual(response.get("response_type"), "ephemeral")
        self.assertIn("Unknown ProMarshal command", response.get("text", ""))

    async def test_actionitem_create_with_missing_owner_requires_clarification_before_create(self):
        payload = {
            "text": "actionitem create Capture quick reminder",
            "user_id": "U123",
            "team_id": "T123",
            "channel_id": "D123",
        }
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "role": "owner", "status": "active", "name": "Owner"},
            ],
        }
        member = {"user_id": "u-owner", "role": "owner"}
        with patch.object(command_handlers.settings, "action_items_enabled", True), patch(
            "app.integrations.slack.command_handlers.authenticate_user",
            new=AsyncMock(return_value=(project, member, None, "ok")),
        ), patch(
            "app.integrations.slack.command_handlers.ActionItemService.create",
            new=AsyncMock(),
        ) as create_mock:
            response = await command_handlers.dispatch_command(payload)

        self.assertIn("Owner is required", response.get("text", ""))
        create_mock.assert_not_awaited()

    async def test_actionitem_list_parses_space_separated_kv_tokens(self):
        payload = {
            "text": "actionitem list status:open owner:u-123 limit:5",
            "user_id": "U123",
            "team_id": "T123",
            "channel_id": "D123",
        }
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "role": "owner", "status": "active", "name": "Owner"},
                {"user_id": "u-123", "role": "member", "status": "active", "name": "Prabhu"},
            ],
        }
        member = {"user_id": "u-123", "role": "member"}
        list_mock = AsyncMock(return_value={"items": [], "total": 0, "limit": 5, "offset": 0})
        with patch.object(command_handlers.settings, "action_items_enabled", True), patch(
            "app.integrations.slack.command_handlers.authenticate_user",
            new=AsyncMock(return_value=(project, member, None, "ok")),
        ), patch(
            "app.integrations.slack.command_handlers.ActionItemService.list",
            new=list_mock,
        ):
            await command_handlers.dispatch_command(payload)

        list_mock.assert_awaited_once()
        args = list_mock.await_args.args
        self.assertEqual(args[1].get("status"), "open")
        self.assertEqual(args[1].get("owner_user_id"), "u-123")
        self.assertEqual(args[1].get("limit"), 5)

    async def test_actionitem_list_defaults_to_open_when_status_missing(self):
        payload = {
            "text": "actionitem list limit:5",
            "user_id": "U123",
            "team_id": "T123",
            "channel_id": "D123",
        }
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "role": "owner", "status": "active", "name": "Owner"},
                {"user_id": "u-123", "role": "member", "status": "active", "name": "Prabhu"},
            ],
        }
        member = {"user_id": "u-123", "role": "member"}
        list_mock = AsyncMock(return_value={"items": [], "total": 0, "limit": 5, "offset": 0})
        with patch.object(command_handlers.settings, "action_items_enabled", True), patch(
            "app.integrations.slack.command_handlers.authenticate_user",
            new=AsyncMock(return_value=(project, member, None, "ok")),
        ), patch(
            "app.integrations.slack.command_handlers.ActionItemService.list",
            new=list_mock,
        ):
            await command_handlers.dispatch_command(payload)

        list_mock.assert_awaited_once()
        args = list_mock.await_args.args
        self.assertEqual(args[1].get("status"), "open")

    async def test_actionitem_list_response_includes_owner_label(self):
        payload = {
            "text": "actionitem list status:open limit:5",
            "user_id": "U123",
            "team_id": "T123",
            "channel_id": "D123",
        }
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "role": "owner", "status": "active", "name": "Owner"},
                {"user_id": "u-123", "role": "member", "status": "active", "name": "Prabhu"},
            ],
        }
        member = {"user_id": "u-123", "role": "member"}
        list_mock = AsyncMock(
            return_value={
                "items": [
                    {
                        "action_item_id": "ai-1",
                        "display_key": "ACTION-1",
                        "status": "open",
                        "title": "Follow up with network team",
                        "owner_user_id": "u-123",
                    }
                ],
                "total": 1,
                "limit": 5,
                "offset": 0,
            }
        )
        with patch.object(command_handlers.settings, "action_items_enabled", True), patch(
            "app.integrations.slack.command_handlers.authenticate_user",
            new=AsyncMock(return_value=(project, member, None, "ok")),
        ), patch(
            "app.integrations.slack.command_handlers.ActionItemService.list",
            new=list_mock,
        ):
            response = await command_handlers.dispatch_command(payload)

        text = response.get("text", "")
        self.assertIn("owner: Prabhu", text)

    async def test_actionitem_create_accepts_title_key_with_quotes(self):
        payload = {
            "text": 'actionitem create title:"Follow up with ops team" owner:u-123 related:"Network degradation"',
            "user_id": "U123",
            "team_id": "T123",
            "channel_id": "D123",
        }
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "role": "owner", "status": "active", "name": "Owner"},
                {"user_id": "u-123", "role": "member", "status": "active", "name": "Prabhu"},
            ],
        }
        member = {"user_id": "u-123", "role": "member"}
        create_mock = AsyncMock(
            return_value={"display_key": "ACTION-22", "action_item_id": "ai-22", "needs_followup": False}
        )
        with patch.object(command_handlers.settings, "action_items_enabled", True), patch(
            "app.integrations.slack.command_handlers.authenticate_user",
            new=AsyncMock(return_value=(project, member, None, "ok")),
        ), patch(
            "app.integrations.slack.command_handlers.ActionItemService.create",
            new=create_mock,
        ):
            response = await command_handlers.dispatch_command(payload)

        self.assertIn("Created action item ACTION-22", response.get("text", ""))
        create_mock.assert_awaited_once()
        create_payload = create_mock.await_args.args[1]
        self.assertEqual(create_payload.get("title"), "Follow up with ops team")
        self.assertEqual(create_payload.get("related_to"), "Network degradation")


if __name__ == "__main__":
    unittest.main()
