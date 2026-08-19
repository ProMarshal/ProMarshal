import unittest
from datetime import datetime, timezone

from app.cadence.summary import _project_local_date_from_utc


class CadenceSummaryTimezoneTests(unittest.TestCase):
    def test_project_local_date_uses_timezone_for_early_morning_ist(self):
        utc_ts = datetime(2026, 3, 22, 19, 30, 0, tzinfo=timezone.utc)  # 2026-03-23 01:00 IST
        self.assertEqual(
            _project_local_date_from_utc(utc_ts, "Asia/Kolkata"),
            "2026-03-23",
        )

    def test_project_local_date_keeps_utc_when_timezone_is_utc(self):
        utc_ts = datetime(2026, 3, 22, 19, 30, 0, tzinfo=timezone.utc)
        self.assertEqual(
            _project_local_date_from_utc(utc_ts, "UTC"),
            "2026-03-22",
        )


if __name__ == "__main__":
    unittest.main()

