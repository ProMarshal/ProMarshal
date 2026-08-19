import unittest
from unittest.mock import patch

from app.core.session_lease import (
    acquire_session_lease,
    release_session_lease,
    renew_session_lease,
)


class _FakeRedis:
    def __init__(self):
        self.fence = 0
        self.lock = None
        self.expire_seconds = 0

    def incr(self, _key):
        self.fence += 1
        return self.fence

    def set(self, _key, value, ex=None, nx=False):
        if nx and self.lock is not None:
            return False
        self.lock = str(value)
        self.expire_seconds = int(ex or 0)
        return True

    def eval(self, script, _numkeys, _key, *argv):
        if "EXPIRE" in script:
            token = str(argv[0])
            ttl = int(argv[1])
            if self.lock == token:
                self.expire_seconds = ttl
                return 1
            return 0
        token = str(argv[0])
        if self.lock == token:
            self.lock = None
            return 1
        return 0


class SessionLeaseTests(unittest.TestCase):
    @patch("app.core.session_lease.get_redis_connection", return_value=None)
    def test_acquire_returns_false_without_redis(self, _mock_conn):
        acquired, token = acquire_session_lease("session-1")
        self.assertFalse(acquired)
        self.assertEqual(token, 0)

    @patch("app.core.session_lease.get_redis_connection")
    def test_acquire_and_release_happy_path(self, mock_conn):
        fake = _FakeRedis()
        mock_conn.return_value = fake

        acquired, token = acquire_session_lease("session-1", ttl_seconds=30)
        self.assertTrue(acquired)
        self.assertGreater(token, 0)
        self.assertEqual(fake.lock, str(token))
        self.assertEqual(fake.expire_seconds, 30)

        released = release_session_lease("session-1", token=token)
        self.assertTrue(released)
        self.assertIsNone(fake.lock)

    @patch("app.core.session_lease.get_redis_connection")
    def test_renew_only_owner_can_extend(self, mock_conn):
        fake = _FakeRedis()
        mock_conn.return_value = fake
        acquired, token = acquire_session_lease("session-1", ttl_seconds=20)
        self.assertTrue(acquired)

        self.assertTrue(renew_session_lease("session-1", token=token, ttl_seconds=45))
        self.assertEqual(fake.expire_seconds, 45)
        self.assertFalse(renew_session_lease("session-1", token=token + 1, ttl_seconds=45))


if __name__ == "__main__":
    unittest.main()
