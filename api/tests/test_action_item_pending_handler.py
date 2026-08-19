import unittest
from unittest.mock import AsyncMock, patch

from app.action_items.pending_handler import handle_pending_action_item_clarification
from app.core.config import settings


class ActionItemPendingHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_unhandled_when_no_pending_record(self):
        project = {"project_id": "proj-1234-5678", "members": []}
        with patch(
            "app.action_items.pending_handler.find_pending_interaction",
            new=AsyncMock(return_value=None),
        ):
            result = await handle_pending_action_item_clarification(
                project=project,
                actor_user_id="u-1",
                text="assign to prabhu",
            )
        self.assertFalse(result.get("handled"))

    async def test_resolves_owner_and_clears_followup(self):
        project = {
            "project_id": "proj-1234-5678",
            "members": [{"user_id": "u-2", "status": "active", "name": "Prabhu"}],
        }
        pending = {
            "interaction_id": "pi-1",
            "context_payload": {"action_item_id": "ai-1", "display_key": "ACTION-1"},
        }
        collection = AsyncMock()
        collection.find_one = AsyncMock(
            return_value={"entity_type": "action_item", "action_item_id": "ai-1", "status": "open"}
        )
        collection.update_one = AsyncMock(return_value=AsyncMock(modified_count=1))

        with patch(
            "app.action_items.pending_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.action_items.pending_handler.get_brain_collection",
            return_value=collection,
        ), patch(
            "app.action_items.pending_handler.resolve_project_member_identity",
        ) as resolve_identity, patch(
            "app.action_items.pending_handler.ActionItemService.update",
            new=AsyncMock(return_value={"display_key": "ACTION-1"}),
        ) as update_mock, patch(
            "app.action_items.pending_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ) as resolve_pending:
            resolve_identity.return_value.status = "resolved"
            resolve_identity.return_value.promarshal_user_id = "u-2"
            result = await handle_pending_action_item_clarification(
                project=project,
                actor_user_id="u-1",
                text="assign to prabhu",
            )

        self.assertTrue(result.get("handled"))
        self.assertIn("Assigned ACTION-1 to u-2", result.get("response_text", ""))
        update_mock.assert_awaited_once()
        resolve_pending.assert_awaited_once()

    async def test_pending_clarification_resolves_assign_to_me_with_actor_context(self):
        project = {
            "project_id": "proj-1234-5678",
            "members": [{"user_id": "u-actor", "status": "active", "name": "Prabhu"}],
        }
        pending = {
            "interaction_id": "pi-2",
            "context_payload": {"action_item_id": "ai-2", "display_key": "ACTION-2"},
        }
        collection = AsyncMock()
        collection.find_one = AsyncMock(
            return_value={"entity_type": "action_item", "action_item_id": "ai-2", "status": "open"}
        )
        collection.update_one = AsyncMock(return_value=AsyncMock(modified_count=1))

        with patch(
            "app.action_items.pending_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.action_items.pending_handler.get_brain_collection",
            return_value=collection,
        ), patch(
            "app.action_items.pending_handler.ActionItemService.update",
            new=AsyncMock(return_value={"display_key": "ACTION-2"}),
        ) as update_mock, patch(
            "app.action_items.pending_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ) as resolve_pending:
            result = await handle_pending_action_item_clarification(
                project=project,
                actor_user_id="u-actor",
                text="assign to me",
            )

        self.assertTrue(result.get("handled"))
        self.assertIn("Assigned ACTION-2 to u-actor", result.get("response_text", ""))
        update_mock.assert_awaited_once()
        resolve_pending.assert_awaited_once()

    async def test_pending_clarification_resolves_assign_with_target_reference(self):
        project = {
            "project_id": "proj-1234-5678",
            "members": [{"user_id": "u-2", "status": "active", "name": "Sridevi"}],
        }
        pending = {
            "interaction_id": "pi-22",
            "context_payload": {"action_item_id": "ai-22", "display_key": "ACTION-22"},
        }
        collection = AsyncMock()
        collection.find_one = AsyncMock(
            return_value={"entity_type": "action_item", "action_item_id": "ai-22", "status": "open"}
        )
        collection.update_one = AsyncMock(return_value=AsyncMock(modified_count=1))

        with patch(
            "app.action_items.pending_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.action_items.pending_handler.get_brain_collection",
            return_value=collection,
        ), patch(
            "app.action_items.pending_handler.ActionItemService.update",
            new=AsyncMock(return_value={"display_key": "ACTION-22"}),
        ) as update_mock, patch(
            "app.action_items.pending_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ) as resolve_pending:
            result = await handle_pending_action_item_clarification(
                project=project,
                actor_user_id="u-1",
                text="assign action-22 to sridevi",
            )

        self.assertTrue(result.get("handled"))
        self.assertIn("Assigned ACTION-22 to u-2", result.get("response_text", ""))
        update_mock.assert_awaited_once()
        resolve_pending.assert_awaited_once()

    async def test_pending_clarification_ignores_new_action_item_create_intent(self):
        project = {
            "project_id": "proj-1234-5678",
            "members": [{"user_id": "u-actor", "status": "active", "name": "Prabhu"}],
        }
        pending = {
            "interaction_id": "pi-3",
            "context_payload": {"action_item_id": "ai-3", "display_key": "ACTION-3"},
        }
        collection = AsyncMock()
        collection.find_one = AsyncMock(
            return_value={"entity_type": "action_item", "action_item_id": "ai-3", "status": "open"}
        )
        collection.update_one = AsyncMock(return_value=AsyncMock(modified_count=1))

        with patch(
            "app.action_items.pending_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.action_items.pending_handler.get_brain_collection",
            return_value=collection,
        ), patch(
            "app.action_items.pending_handler.ActionItemService.update",
            new=AsyncMock(return_value={"display_key": "ACTION-3"}),
        ) as update_mock, patch(
            "app.action_items.pending_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ) as resolve_pending:
            result = await handle_pending_action_item_clarification(
                project=project,
                actor_user_id="u-actor",
                text="create action item to send meeting invite for customer tomorrow at 12pm and assign to me",
            )

        self.assertFalse(result.get("handled"))
        update_mock.assert_not_awaited()
        resolve_pending.assert_not_awaited()

    async def test_inconclusive_reply_llm_unavailable_fails_open(self):
        project = {
            "project_id": "proj-1234-5678",
            "members": [{"user_id": "u-actor", "status": "active", "name": "Prabhu"}],
        }
        pending = {
            "interaction_id": "pi-31",
            "context_payload": {"action_item_id": "ai-31", "display_key": "ACTION-31"},
        }
        collection = AsyncMock()
        collection.find_one = AsyncMock(
            return_value={"entity_type": "action_item", "action_item_id": "ai-31", "status": "open"}
        )

        with patch(
            "app.action_items.pending_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.action_items.pending_handler.get_brain_collection",
            return_value=collection,
        ), patch(
            "app.action_items.pending_handler._llm_classify_inconclusive_reply",
            new=AsyncMock(return_value=None),
        ):
            result = await handle_pending_action_item_clarification(
                project=project,
                actor_user_id="u-actor",
                text="sridevi",
            )

        self.assertFalse(result.get("handled"))

    async def test_inconclusive_reply_llm_low_confidence_reminds(self):
        project = {
            "project_id": "proj-1234-5678",
            "members": [{"user_id": "u-actor", "status": "active", "name": "Prabhu"}],
        }
        pending = {
            "interaction_id": "pi-32",
            "context_payload": {"action_item_id": "ai-32", "display_key": "ACTION-32"},
        }
        collection = AsyncMock()
        collection.find_one = AsyncMock(
            return_value={"entity_type": "action_item", "action_item_id": "ai-32", "status": "open"}
        )

        with patch(
            "app.action_items.pending_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.action_items.pending_handler.get_brain_collection",
            return_value=collection,
        ), patch(
            "app.action_items.pending_handler._llm_classify_inconclusive_reply",
            new=AsyncMock(
                return_value={
                    "is_owner_clarification_reply": True,
                    "likely_new_intent": False,
                    "confidence": 0.25,
                    "reason": "low_conf",
                }
            ),
        ):
            result = await handle_pending_action_item_clarification(
                project=project,
                actor_user_id="u-actor",
                text="sridevi",
            )

        self.assertTrue(result.get("handled"))
        self.assertIn("I still need the owner", result.get("response_text", ""))

    async def test_inconclusive_reply_llm_marks_new_intent_passes_through(self):
        project = {
            "project_id": "proj-1234-5678",
            "members": [{"user_id": "u-actor", "status": "active", "name": "Prabhu"}],
        }
        pending = {
            "interaction_id": "pi-33",
            "context_payload": {"action_item_id": "ai-33", "display_key": "ACTION-33"},
        }
        collection = AsyncMock()
        collection.find_one = AsyncMock(
            return_value={"entity_type": "action_item", "action_item_id": "ai-33", "status": "open"}
        )

        with patch(
            "app.action_items.pending_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.action_items.pending_handler.get_brain_collection",
            return_value=collection,
        ), patch(
            "app.action_items.pending_handler._llm_classify_inconclusive_reply",
            new=AsyncMock(
                return_value={
                    "is_owner_clarification_reply": True,
                    "likely_new_intent": True,
                    "confidence": 0.95,
                    "reason": "new_intent",
                }
            ),
        ):
            result = await handle_pending_action_item_clarification(
                project=project,
                actor_user_id="u-actor",
                text="sridevi",
            )

        self.assertFalse(result.get("handled"))

    async def test_inconclusive_reply_llm_high_confidence_resolves_owner(self):
        project = {
            "project_id": "proj-1234-5678",
            "members": [{"user_id": "u-2", "status": "active", "name": "Sridevi"}],
        }
        pending = {
            "interaction_id": "pi-34",
            "context_payload": {"action_item_id": "ai-34", "display_key": "ACTION-34"},
        }
        collection = AsyncMock()
        collection.find_one = AsyncMock(
            return_value={"entity_type": "action_item", "action_item_id": "ai-34", "status": "open"}
        )
        collection.update_one = AsyncMock(return_value=AsyncMock(modified_count=1))

        with patch(
            "app.action_items.pending_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.action_items.pending_handler.get_brain_collection",
            return_value=collection,
        ), patch(
            "app.action_items.pending_handler._llm_classify_inconclusive_reply",
            new=AsyncMock(
                return_value={
                    "is_owner_clarification_reply": True,
                    "likely_new_intent": False,
                    "confidence": 0.91,
                    "reason": "clarification",
                }
            ),
        ), patch(
            "app.action_items.pending_handler.ActionItemService.update",
            new=AsyncMock(return_value={"display_key": "ACTION-34"}),
        ) as update_mock, patch(
            "app.action_items.pending_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ) as resolve_pending:
            result = await handle_pending_action_item_clarification(
                project=project,
                actor_user_id="u-actor",
                text="sridevi",
            )

        self.assertTrue(result.get("handled"))
        self.assertIn("Assigned ACTION-34 to u-2", result.get("response_text", ""))
        update_mock.assert_awaited_once()
        resolve_pending.assert_awaited_once()

    async def test_inconclusive_reply_with_llm_fallback_disabled_is_deterministic_only(self):
        project = {
            "project_id": "proj-1234-5678",
            "members": [{"user_id": "u-2", "status": "active", "name": "Sridevi"}],
        }
        pending = {
            "interaction_id": "pi-35",
            "context_payload": {"action_item_id": "ai-35", "display_key": "ACTION-35"},
        }
        collection = AsyncMock()
        collection.find_one = AsyncMock(
            return_value={"entity_type": "action_item", "action_item_id": "ai-35", "status": "open"}
        )

        with patch(
            "app.action_items.pending_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.action_items.pending_handler.get_brain_collection",
            return_value=collection,
        ), patch.object(settings, "action_item_pending_relevance_llm_fallback_enabled", False):
            result = await handle_pending_action_item_clarification(
                project=project,
                actor_user_id="u-actor",
                text="sridevi",
            )

        self.assertFalse(result.get("handled"))


if __name__ == "__main__":
    unittest.main()
