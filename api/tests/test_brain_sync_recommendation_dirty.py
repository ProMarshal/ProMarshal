import unittest
from unittest.mock import AsyncMock, patch

from app.tasks.brain_sync import BrainSync


class BrainSyncRecommendationDirtyTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_task_marks_recommendation_dirty(self):
        fake_collection = AsyncMock()
        jira_task = {
            "external_id": "ABC-123",
            "integration_type": "jira",
            "entity_type": "work_item",
            "title": "Test task",
        }

        with patch(
            "app.tasks.brain_sync.get_brain_collection",
            return_value=fake_collection,
        ), patch(
            "app.tasks.brain_sync.mark_project_task_dirty",
            return_value=True,
        ) as mark_dirty_mock, patch(
            "app.tasks.brain_sync.invalidate_pm_board_summary_cache",
            return_value=None,
        ):
            await BrainSync.sync_task_to_brain("proj-1111-2222", jira_task)

        mark_dirty_mock.assert_called_once_with("proj-1111-2222", "ABC-123")
        fake_collection.update_one.assert_awaited_once()

    async def test_sync_task_canonicalizes_legacy_task_entity_type(self):
        fake_collection = AsyncMock()
        jira_task = {
            "external_id": "ABC-124",
            "integration_type": "jira",
            "entity_type": "task",
            "issue_type": "Story",
            "provider_type": None,
            "work_item_type": None,
            "title": "Legacy shaped task",
        }

        with patch(
            "app.tasks.brain_sync.get_brain_collection",
            return_value=fake_collection,
        ), patch(
            "app.tasks.brain_sync.mark_project_task_dirty",
            return_value=True,
        ), patch(
            "app.tasks.brain_sync.invalidate_pm_board_summary_cache",
            return_value=None,
        ):
            await BrainSync.sync_task_to_brain("proj-1111-2222", jira_task)

        update_call = fake_collection.update_one.await_args
        set_payload = update_call.args[1]["$set"]
        self.assertEqual(set_payload.get("entity_type"), "work_item")
        self.assertEqual(set_payload.get("provider_type"), "Story")
        self.assertEqual(set_payload.get("work_item_type"), "standard")


if __name__ == "__main__":
    unittest.main()
