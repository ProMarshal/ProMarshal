import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.action_items.router import (
    close_action_item,
    create_action_item,
    delete_action_item,
    update_action_item,
)
from app.action_items.schemas import (
    ActionItemCloseRequest,
    ActionItemCreateRequest,
    ActionItemUpdateRequest,
)


class ActionItemsRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_route_uses_exclude_unset_for_patch_payload(self):
        updated_doc = {
            "entity_type": "action_item",
            "action_item_id": "ai-1",
            "display_key": "ACTION-1",
            "project_id": "proj-1234-5678",
            "title": "Send invite",
            "status": "open",
            "owner_user_id": "u-1",
            "created_by_user_id": "u-1",
            "source": "manual",
            "related_to": None,
            "related_type": "other",
            "related_id": None,
            "due_type": "by_date",
            "due_date": datetime.now(timezone.utc),
            "target_datetime": None,
            "note_color": None,
            "needs_followup": False,
            "needs_followup_set_at": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "closed_at": None,
            "closed_by_user_id": None,
        }
        update_mock = AsyncMock(return_value=updated_doc)
        with patch("app.action_items.router.ActionItemService.update", new=update_mock):
            payload = ActionItemUpdateRequest(
                status="open",
            )
            await update_action_item(
                project_id="proj-1234-5678",
                action_item_id="ai-1",
                payload=payload,
                x_user_id="u-1",
                current_user={"user_id": "u-1"},
            )

        update_mock.assert_awaited_once()
        call_args = update_mock.await_args.args
        sent_payload = call_args[2]
        self.assertEqual(sent_payload.get("status"), "open")
        self.assertEqual(sent_payload.get("user_id"), "u-1")
        self.assertNotIn("due_date", sent_payload)
        self.assertNotIn("due_type", sent_payload)
        self.assertNotIn("target_datetime", sent_payload)

    async def test_create_route_accepts_token_only_payload(self):
        created_doc = {
            "entity_type": "action_item",
            "action_item_id": "ai-2",
            "display_key": "ACTION-2",
            "project_id": "proj-1234-5678",
            "title": "Send reminder",
            "status": "open",
            "owner_user_id": "u-2",
            "created_by_user_id": "u-1",
            "source": "manual",
            "related_to": None,
            "related_type": "other",
            "related_id": None,
            "due_type": "no_due_date",
            "due_date": None,
            "target_datetime": None,
            "note_color": None,
            "needs_followup": False,
            "needs_followup_set_at": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "closed_at": None,
            "closed_by_user_id": None,
        }
        create_mock = AsyncMock(return_value=created_doc)
        with patch("app.action_items.router.ActionItemService.create", new=create_mock):
            payload = ActionItemCreateRequest(
                title="Send reminder",
                owner_user_id="u-2",
            )
            await create_action_item(
                project_id="proj-1234-5678",
                payload=payload,
                x_user_id=None,
                current_user={"user_id": "u-1"},
            )

        create_mock.assert_awaited_once()
        call_args = create_mock.await_args.args
        sent_payload = call_args[1]
        self.assertEqual(sent_payload.get("user_id"), "u-1")
        self.assertEqual(sent_payload.get("owner_user_id"), "u-2")

    async def test_close_route_accepts_token_only_payload(self):
        updated_doc = {
            "entity_type": "action_item",
            "action_item_id": "ai-3",
            "display_key": "ACTION-3",
            "project_id": "proj-1234-5678",
            "title": "Close me",
            "status": "done",
            "owner_user_id": "u-1",
            "created_by_user_id": "u-1",
            "source": "manual",
            "related_to": None,
            "related_type": "other",
            "related_id": None,
            "due_type": "no_due_date",
            "due_date": None,
            "target_datetime": None,
            "note_color": None,
            "needs_followup": False,
            "needs_followup_set_at": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "closed_at": datetime.now(timezone.utc),
            "closed_by_user_id": "u-1",
        }
        close_mock = AsyncMock(return_value=updated_doc)
        with patch("app.action_items.router.ActionItemService.close", new=close_mock):
            payload = ActionItemCloseRequest(status="done")
            await close_action_item(
                project_id="proj-1234-5678",
                action_item_id="ai-3",
                payload=payload,
                x_user_id=None,
                current_user={"user_id": "u-1"},
            )

        close_mock.assert_awaited_once()
        call_args = close_mock.await_args.args
        sent_payload = call_args[2]
        self.assertEqual(sent_payload.get("user_id"), "u-1")
        self.assertEqual(sent_payload.get("status"), "done")

    async def test_delete_route_calls_service_with_resolved_actor(self):
        delete_mock = AsyncMock(return_value=None)
        with patch("app.action_items.router.ActionItemService.delete", new=delete_mock):
            await delete_action_item(
                project_id="proj-1234-5678",
                action_item_id="ai-1",
                user_id="u-1",
                x_user_id="u-1",
                current_user={"user_id": "u-1"},
            )

        delete_mock.assert_awaited_once_with(
            "proj-1234-5678",
            "ai-1",
            {"user_id": "u-1"},
        )


if __name__ == "__main__":
    unittest.main()
