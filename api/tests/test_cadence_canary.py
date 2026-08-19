import unittest
from unittest.mock import patch

from app.cadence.canary import should_route_to_queue


class CadenceCanaryTests(unittest.TestCase):
    @patch("app.cadence.canary.settings")
    def test_zero_percent_routes_inline(self, mock_settings):
        mock_settings.cadence_dm_queue_canary_pct = 0
        self.assertFalse(should_route_to_queue("session-1"))

    @patch("app.cadence.canary.settings")
    def test_hundred_percent_routes_queue(self, mock_settings):
        mock_settings.cadence_dm_queue_canary_pct = 100
        self.assertTrue(should_route_to_queue("session-1"))

    @patch("app.cadence.canary.settings")
    def test_mid_percent_is_deterministic_for_same_session(self, mock_settings):
        mock_settings.cadence_dm_queue_canary_pct = 50
        first = should_route_to_queue("session-abc")
        second = should_route_to_queue("session-abc")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
