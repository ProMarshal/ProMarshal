import unittest

from app.sessions.repository import UpdateSessionResult


class SessionRepositoryUpdateResultTests(unittest.TestCase):
    def test_truthy_when_modified(self):
        result = UpdateSessionResult(matched_count=1, modified_count=1, upserted=False)
        self.assertTrue(result)
        self.assertFalse(result.stale_rejected)

    def test_truthy_when_upserted(self):
        result = UpdateSessionResult(matched_count=0, modified_count=0, upserted=True)
        self.assertTrue(result)
        self.assertFalse(result.stale_rejected)

    def test_falsy_and_stale_rejected_when_no_match(self):
        result = UpdateSessionResult(matched_count=0, modified_count=0, upserted=False)
        self.assertFalse(result)
        self.assertTrue(result.stale_rejected)


if __name__ == "__main__":
    unittest.main()
