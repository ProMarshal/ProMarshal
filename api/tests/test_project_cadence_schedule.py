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


class ProjectCadenceScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_cadence_schedule_maps_skip_weekends(self):
        fake_db = _FakeDB({"_id": "507f1f77bcf86cd799439011", "project_id": "proj-1234-5678"})
        with patch("app.projects.service.get_database", return_value=fake_db), patch(
            "app.projects.service.get_or_create_cadence_schedule_for_project",
            new=AsyncMock(
                return_value={
                    "enabled": True,
                    "schedule_spec": {
                        "mode": "daily",
                        "time": "09:15",
                        "skip_weekends": True,
                        "ignore_followup_for_owner": True,
                    },
                }
            ),
        ):
            result = await ProjectService.get_cadence_schedule("507f1f77bcf86cd799439011")

        self.assertEqual(result["enabled"], True)
        self.assertEqual(result["time"], "09:15")
        self.assertTrue(result["skip_weekends"])
        self.assertTrue(result["ignore_followup_for_owner"])

    async def test_update_cadence_schedule_passes_skip_weekends(self):
        fake_db = _FakeDB({"project_id": "proj-1234-5678"})
        with patch("app.projects.service.get_database", return_value=fake_db), patch(
            "app.projects.service.upsert_cadence_schedule_for_project",
            new=AsyncMock(
                return_value={
                    "enabled": False,
                    "schedule_spec": {
                        "mode": "daily",
                        "time": "10:00",
                        "skip_weekends": True,
                        "ignore_followup_for_owner": True,
                    },
                }
            ),
        ) as upsert_mock:
            result = await ProjectService.update_cadence_schedule(
                "507f1f77bcf86cd799439011",
                enabled=False,
                time="10:00",
                skip_weekends=True,
                ignore_followup_for_owner=True,
            )

        self.assertEqual(result["enabled"], False)
        self.assertEqual(result["time"], "10:00")
        self.assertTrue(result["skip_weekends"])
        self.assertTrue(result["ignore_followup_for_owner"])
        kwargs = upsert_mock.await_args.kwargs
        self.assertEqual(kwargs["project_id"], "proj-1234-5678")
        self.assertEqual(kwargs["time_hhmm"], "10:00")
        self.assertTrue(kwargs["skip_weekends"])
        self.assertTrue(kwargs["ignore_followup_for_owner"])


if __name__ == "__main__":
    unittest.main()
