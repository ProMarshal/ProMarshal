import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.projects.router import list_projects


class ProjectsRouterAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_projects_rejects_mismatched_query_user_id(self):
        with self.assertRaises(HTTPException) as context:
            await list_projects(
                user_id="other-user",
                skip=0,
                limit=100,
                current_user={"user_id": "actor-user"},
            )
        self.assertEqual(context.exception.status_code, 403)
        self.assertEqual(
            context.exception.detail,
            "Actor identity mismatch between token and query param",
        )

    async def test_list_projects_uses_actor_identity(self):
        project_docs = [
            {
                "_id": "p1",
                "project_id": "proj-1",
                "project_name": "Test",
                "user_id": "actor-user",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        ]
        list_all_mock = AsyncMock(return_value=project_docs)
        with patch("app.projects.router.ProjectService.list_all", new=list_all_mock):
            result = await list_projects(
                user_id=None,
                skip=5,
                limit=10,
                current_user={"user_id": "actor-user"},
            )

        list_all_mock.assert_awaited_once_with("actor-user", 5, 10)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].project_id, "proj-1")


if __name__ == "__main__":
    unittest.main()
