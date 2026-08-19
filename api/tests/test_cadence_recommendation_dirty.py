import unittest
from unittest.mock import AsyncMock, patch

from app.cadence.interaction_handler import update_jira_task


class CadenceRecommendationDirtyTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_jira_task_marks_recommendation_dirty_on_success(self):
        session = {
            "session_id": "sess-1",
            "project_id": "proj-1111-2222",
            "user_id": "user-1",
            "task_provider": "jira",
            "tasks": [
                {
                    "external_id": "ABC-123",
                    "updated_status": "in_progress",
                    "comment": "Working on it",
                }
            ],
        }

        with patch(
            "app.cadence.interaction_handler._dispatch_cadence_task_mutation",
            new=AsyncMock(
                return_value={
                    "ok": True,
                    "status_updated": True,
                    "comment_added": True,
                    "brain_sync_succeeded": True,
                }
            ),
        ), patch(
            "app.cadence.interaction_handler.mark_project_task_dirty",
            return_value=True,
        ) as mark_dirty_mock, patch(
            "app.cadence.interaction_handler.mark_task_updated_in_jira",
            new=AsyncMock(return_value=True),
        ):
            await update_jira_task(db=AsyncMock(), session=session, task_index=0)

        mark_dirty_mock.assert_called_once_with("proj-1111-2222", "ABC-123")


if __name__ == "__main__":
    unittest.main()
