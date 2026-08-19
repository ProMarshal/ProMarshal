import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.sessions.cleanup import run_unified_session_cleanup


class _FakeSessionIndex:
    def __init__(self) -> None:
        self.last_delete_query = None

    async def distinct(self, field, query):
        return []

    async def delete_many(self, query):
        self.last_delete_query = query
        return SimpleNamespace(deleted_count=0)


class _FakeProjects:
    async def distinct(self, field):
        return []


class _FakeDb:
    def __init__(self) -> None:
        self.session_index = _FakeSessionIndex()
        self.projects = _FakeProjects()

    def __getitem__(self, key):
        if key == "session_index":
            return self.session_index
        raise KeyError(key)


class SessionCleanupCadenceIndexPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_cadence_index_cleanup_targets_terminal_only(self):
        db = _FakeDb()
        with patch("app.sessions.cleanup.get_brain_collection") as mock_brain:
            mock_brain.return_value.delete_many = AsyncMock(return_value=SimpleNamespace(deleted_count=0))
            await run_unified_session_cleanup(db, now=datetime(2026, 3, 12, 10, 0, 0))

        self.assertIsNotNone(db.session_index.last_delete_query)
        index_query = db.session_index.last_delete_query
        cadence_query = index_query["$or"][0]
        self.assertIn("$and", cadence_query)
        terminal_gate = cadence_query["$and"][0]["$or"]
        time_gate = cadence_query["$and"][1]
        self.assertIn({"terminal": True}, terminal_gate)
        self.assertIn({"status": "completed"}, terminal_gate)
        self.assertEqual(list(time_gate.keys()), ["updated_at"])


if __name__ == "__main__":
    unittest.main()
