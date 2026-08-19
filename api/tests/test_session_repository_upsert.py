import unittest
from datetime import datetime

from app.sessions.repository import SessionRepository


class SessionRepositoryUpsertTests(unittest.TestCase):
    def test_build_project_upsert_update_doc_avoids_created_at_conflict(self):
        now = datetime.utcnow()
        created_at = datetime(2026, 1, 1)
        update_doc = SessionRepository._build_project_upsert_update_doc(
            project_doc={
                "session_id": "s1",
                "session_key": "k1",
                "source": "slack_dm",
                "created_at": created_at,
                "updated_at": now,
            },
            now=now,
        )

        self.assertIn("$set", update_doc)
        self.assertIn("$setOnInsert", update_doc)
        self.assertNotIn("created_at", update_doc["$set"])
        self.assertEqual(update_doc["$setOnInsert"].get("created_at"), created_at)


if __name__ == "__main__":
    unittest.main()
