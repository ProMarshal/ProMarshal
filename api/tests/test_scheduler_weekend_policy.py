import unittest
from unittest.mock import AsyncMock, patch

from app.scheduler import executors


class SchedulerWeekendPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_cadence_executor_skips_when_weekend_policy_enabled(self):
        fake_db = object()
        schedule = {
            "project_id": "proj-1234-5678",
            "schedule_spec": {"mode": "daily", "time": "09:00", "skip_weekends": True},
        }

        with patch.object(executors, "_is_weekend_in_project_timezone", new=AsyncMock(return_value=True)):
            result = await executors.execute_cadence_reminder(fake_db, schedule)

        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "weekend_policy")

    async def test_action_item_digest_executor_skips_when_weekend_policy_enabled(self):
        fake_db = object()
        schedule = {
            "project_id": "proj-1234-5678",
            "schedule_spec": {"mode": "daily", "time": "12:30", "skip_weekends": True},
        }

        with patch.object(executors, "_is_weekend_in_project_timezone", new=AsyncMock(return_value=True)):
            result = await executors.execute_action_item_digest(fake_db, schedule)

        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "weekend_policy")


if __name__ == "__main__":
    unittest.main()
