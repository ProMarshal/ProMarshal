import unittest
from unittest.mock import patch

from app.core.queue_metrics import get_queue_depth


class _FakeRedisOk:
    def __init__(self, value=3):
        self._value = value
        self.keys = []

    def hlen(self, key):
        self.keys.append(key)
        return self._value


class _FakeRedisError:
    def hlen(self, key):
        raise RuntimeError("redis failure")


class QueueMetricsTests(unittest.TestCase):
    @patch("app.core.queue_metrics.get_redis_connection", return_value=None)
    def test_returns_unknown_when_redis_missing(self, _mock_conn):
        self.assertEqual(get_queue_depth("cadence_dm"), -1)

    @patch("app.core.queue_metrics.get_redis_connection")
    def test_returns_depth_from_dramatiq_msgs_hash(self, mock_conn):
        fake = _FakeRedisOk(value=7)
        mock_conn.return_value = fake
        depth = get_queue_depth("cadence_dm")
        self.assertEqual(depth, 7)
        self.assertEqual(fake.keys, ["{dramatiq}:cadence_dm.msgs"])

    @patch("app.core.queue_metrics.get_redis_connection", return_value=_FakeRedisError())
    def test_returns_unknown_on_redis_error(self, _mock_conn):
        self.assertEqual(get_queue_depth("cadence_dm"), -1)


if __name__ == "__main__":
    unittest.main()
