import unittest
from unittest.mock import AsyncMock, patch

from app.projects.service import ProjectService


class _FakeProjectsCollection:
    def __init__(self, find_one_result):
        self._find_one_result = find_one_result

    async def find_one(self, *args, **kwargs):
        return self._find_one_result


class _FakeDB:
    def __init__(self, find_one_result):
        self.projects = _FakeProjectsCollection(find_one_result)


class ProjectActionItemDigestScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_action_item_digest_schedule_maps_response(self):
        fake_db = _FakeDB({"_id": "507f1f77bcf86cd799439011", "project_id": "proj-1234-5678"})
        with patch("app.projects.service.get_database", return_value=fake_db), patch(
            "app.projects.service.get_or_create_action_item_digest_schedule_for_project",
            new=AsyncMock(
                return_value={
                    "enabled": True,
                    "schedule_spec": {
                        "mode": "daily",
                        "time": "12:30",
                        "skip_weekends": True,
                        "ignore_followup_for_owner": True,
                    },
                }
            ),
        ):
            result = await ProjectService.get_action_item_digest_schedule("507f1f77bcf86cd799439011")

        self.assertEqual(result["enabled"], True)
        self.assertEqual(result["time"], "12:30")
        self.assertTrue(result["skip_weekends"])
        self.assertTrue(result["ignore_followup_for_owner"])

    async def test_update_action_item_digest_schedule_uses_custom_project_id(self):
        fake_db = _FakeDB({"project_id": "proj-1234-5678"})
        with patch("app.projects.service.get_database", return_value=fake_db), patch(
            "app.projects.service.upsert_action_item_digest_schedule_for_project",
            new=AsyncMock(
                return_value={
                    "enabled": False,
                    "schedule_spec": {
                        "mode": "daily",
                        "time": "11:45",
                        "skip_weekends": True,
                        "ignore_followup_for_owner": True,
                    },
                }
            ),
        ) as upsert_mock:
            result = await ProjectService.update_action_item_digest_schedule(
                "507f1f77bcf86cd799439011",
                enabled=False,
                time="11:45",
                skip_weekends=True,
                ignore_followup_for_owner=True,
            )

        self.assertEqual(result["enabled"], False)
        self.assertEqual(result["time"], "11:45")
        self.assertTrue(result["skip_weekends"])
        self.assertTrue(result["ignore_followup_for_owner"])
        upsert_mock.assert_awaited_once()
        kwargs = upsert_mock.await_args.kwargs
        self.assertEqual(kwargs["project_id"], "proj-1234-5678")
        self.assertEqual(kwargs["time_hhmm"], "11:45")
        self.assertTrue(kwargs["skip_weekends"])
        self.assertTrue(kwargs["ignore_followup_for_owner"])


if __name__ == "__main__":
    unittest.main()
