import unittest
from unittest.mock import AsyncMock, patch

from app.cadence import session_store


class _FakeSessionRepo:
    def __init__(self) -> None:
        self.update_docs = []

    async def list_session_index(self, *, query, limit):
        return [{"session_id": "sid-expire-1", "project_id": "proj-1"}]

    async def update_session_by_id(
        self,
        *,
        entity_type,
        source,
        session_id,
        project_id=None,
        update_doc,
    ):
        self.update_docs.append(
            {
                "entity_type": entity_type,
                "source": source,
                "session_id": session_id,
                "project_id": project_id,
                "update_doc": update_doc,
            }
        )
        return True


class CadenceSessionExpiryClarificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_expire_stale_sessions_clears_clarification_payload(self):
        fake_repo = _FakeSessionRepo()
        with patch("app.cadence.session_store._repo", return_value=fake_repo), patch(
            "app.cadence.session_store.build_and_store_session_summary",
            AsyncMock(return_value=True),
        ), patch(
            "app.cadence.session_store.get_session",
            AsyncMock(return_value=None),
        ):
            expired_count = await session_store.expire_stale_sessions(object())

        self.assertEqual(expired_count, 1)
        self.assertEqual(len(fake_repo.update_docs), 1)
        update_doc = fake_repo.update_docs[0]["update_doc"]
        self.assertIn("$unset", update_doc)
        self.assertIn("clarification_payload", update_doc["$unset"])

    async def test_expire_stale_sessions_uses_source_fallback_when_index_missing(self):
        fake_repo = _FakeSessionRepo()

        async def _empty_index(*, query, limit):
            return []

        fake_repo.list_session_index = _empty_index  # type: ignore[assignment]

        with patch("app.cadence.session_store._repo", return_value=fake_repo), patch(
            "app.cadence.session_store._list_expired_source_sessions",
            AsyncMock(return_value=[{"session_id": "sid-expire-2", "project_id": "proj-1"}]),
        ), patch(
            "app.cadence.session_store.build_and_store_session_summary",
            AsyncMock(return_value=True),
        ), patch(
            "app.cadence.session_store.get_session",
            AsyncMock(return_value=None),
        ):
            expired_count = await session_store.expire_stale_sessions(object())

        self.assertEqual(expired_count, 1)
        self.assertEqual(len(fake_repo.update_docs), 1)
        self.assertEqual(fake_repo.update_docs[0]["session_id"], "sid-expire-2")


if __name__ == "__main__":
    unittest.main()
