import unittest
from bson import ObjectId
from unittest.mock import AsyncMock, patch

from app.projects.service import ProjectService


class ProjectTimezoneScheduleRealignTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_realigns_project_daily_schedules_when_timezone_changes(self):
        project_oid = str(ObjectId())
        existing = {
            "_id": ObjectId(project_oid),
            "project_id": "proj-1234-5678",
            "timezone": "Asia/Kolkata",
        }
        updated = {
            "_id": ObjectId(project_oid),
            "project_id": "proj-1234-5678",
            "timezone": "America/New_York",
        }

        db = type("FakeDB", (), {})()
        db.projects = type("FakeProjects", (), {})()
        db.projects.find_one = AsyncMock(side_effect=[existing, updated])
        db.projects.update_one = AsyncMock(return_value=None)

        with patch("app.projects.service.get_database", return_value=db), patch(
            "app.projects.service.realign_project_daily_schedules_for_timezone",
            new=AsyncMock(return_value={"project_daily_schedules_realigned": 2}),
        ) as mock_realign:
            result = await ProjectService.update(
                project_oid,
                {"timezone": "America/New_York"},
            )

        self.assertIsNotNone(result)
        db.projects.update_one.assert_awaited_once()
        mock_realign.assert_awaited_once_with(db, project_id="proj-1234-5678")

    async def test_update_skips_realign_when_timezone_unchanged(self):
        project_oid = str(ObjectId())
        existing = {
            "_id": ObjectId(project_oid),
            "project_id": "proj-1234-5678",
            "timezone": "Asia/Kolkata",
        }
        updated = {
            "_id": ObjectId(project_oid),
            "project_id": "proj-1234-5678",
            "timezone": "Asia/Kolkata",
        }

        db = type("FakeDB", (), {})()
        db.projects = type("FakeProjects", (), {})()
        db.projects.find_one = AsyncMock(side_effect=[existing, updated])
        db.projects.update_one = AsyncMock(return_value=None)

        with patch("app.projects.service.get_database", return_value=db), patch(
            "app.projects.service.realign_project_daily_schedules_for_timezone",
            new=AsyncMock(),
        ) as mock_realign:
            result = await ProjectService.update(
                project_oid,
                {"timezone": "Asia/Kolkata"},
            )

        self.assertIsNotNone(result)
        db.projects.update_one.assert_awaited_once()
        mock_realign.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

