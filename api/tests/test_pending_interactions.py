import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.core import pending_interactions


class PendingInteractionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_pending_interaction_inserts_pending_doc(self):
        collection = AsyncMock()
        collection.insert_one = AsyncMock(return_value=AsyncMock(inserted_id="x"))
        with patch(
            "app.core.pending_interactions.get_pending_interactions_collection",
            return_value=collection,
        ):
            doc = await pending_interactions.create_pending_interaction(
                interaction_type="action_item_clarification",
                project_id="proj-1234-5678",
                target_user_id="u-1",
                context_payload={"action_item_id": "ai-1"},
            )

        self.assertEqual(doc.get("status"), "pending")
        self.assertEqual(doc.get("type"), "action_item_clarification")
        self.assertEqual(doc.get("project_id"), "proj-1234-5678")
        self.assertEqual(doc.get("target_user_id"), "u-1")
        collection.insert_one.assert_awaited_once()

    async def test_resolve_pending_interaction_deletes_doc(self):
        collection = AsyncMock()
        collection.delete_one = AsyncMock(return_value=AsyncMock(deleted_count=1))
        with patch(
            "app.core.pending_interactions.get_pending_interactions_collection",
            return_value=collection,
        ):
            ok = await pending_interactions.resolve_pending_interaction(
                interaction_id="pi-123",
                terminal_status="resolved",
            )
        self.assertTrue(ok)
        collection.delete_one.assert_awaited_once()

    async def test_find_pending_interaction_cleans_expired_before_lookup(self):
        collection = AsyncMock()
        collection.delete_many = AsyncMock(return_value=AsyncMock(deleted_count=1))
        collection.find_one = AsyncMock(return_value=None)
        with patch(
            "app.core.pending_interactions.get_pending_interactions_collection",
            return_value=collection,
        ):
            doc = await pending_interactions.find_pending_interaction(
                project_id="proj-1234-5678",
                target_user_id="u-1",
                interaction_type="action_item_clarification",
            )

        self.assertIsNone(doc)
        collection.delete_many.assert_awaited_once()
        collection.find_one.assert_awaited_once()

    async def test_list_pending_interactions_cleans_expired_before_lookup(self):
        collection = AsyncMock()
        cursor = Mock()
        cursor.sort.return_value = cursor
        cursor.limit.return_value = cursor
        cursor.to_list = AsyncMock(return_value=[])
        collection.find = Mock(return_value=cursor)
        collection.delete_many = AsyncMock(return_value=AsyncMock(deleted_count=2))
        with patch(
            "app.core.pending_interactions.get_pending_interactions_collection",
            return_value=collection,
        ):
            docs = await pending_interactions.list_pending_interactions_by_external_identity(
                provider="slack",
                workspace_id="T1",
                external_user_id="U1",
                interaction_type="action_item_clarification",
            )
        self.assertEqual(docs, [])
        collection.delete_many.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
