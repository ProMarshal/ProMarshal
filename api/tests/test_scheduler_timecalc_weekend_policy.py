from datetime import datetime
import unittest

from app.scheduler.timecalc import compute_next_run_at


class SchedulerTimecalcWeekendPolicyTests(unittest.TestCase):
    def test_daily_without_weekend_policy_keeps_saturday(self):
        now_utc = datetime(2026, 3, 27, 20, 0, 0)  # Saturday 01:30 Asia/Kolkata
        next_run = compute_next_run_at(
            schedule_spec={"mode": "daily", "time": "09:00", "skip_weekends": False},
            now_utc=now_utc,
            project_timezone="Asia/Kolkata",
        )
        # Saturday 09:00 IST = 03:30 UTC
        self.assertEqual(next_run, datetime(2026, 3, 28, 3, 30, 0))

    def test_daily_with_weekend_policy_skips_to_monday(self):
        now_utc = datetime(2026, 3, 27, 20, 0, 0)  # Saturday 01:30 Asia/Kolkata
        next_run = compute_next_run_at(
            schedule_spec={"mode": "daily", "time": "09:00", "skip_weekends": True},
            now_utc=now_utc,
            project_timezone="Asia/Kolkata",
        )
        # Monday 09:00 IST = Sunday 03:30 UTC (2026-03-30 IST)
        self.assertEqual(next_run, datetime(2026, 3, 29, 3, 30, 0))


if __name__ == "__main__":
    unittest.main()
