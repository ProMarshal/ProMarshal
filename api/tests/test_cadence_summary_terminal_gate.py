import unittest
from unittest.mock import AsyncMock, patch

from app.cadence.summary import try_generate_daily_summary


class CadenceSummaryTerminalGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_when_any_source_session_not_terminal(self):
        session = {"session_id": "sid-1", "project_id": "proj-1", "correlation_id": "corr-1"}
        source_sessions = [
            {"session_id": "sid-1", "status": "completed"},
            {"session_id": "sid-2", "status": "awaiting_status"},
        ]

        with (
            patch("app.cadence.summary.get_session", AsyncMock(return_value=session)),
            patch("app.cadence.summary.get_brain_collection") as mock_collection,
            patch("app.cadence.summary.SessionRepository") as mock_repo_cls,
            patch("app.cadence.summary._list_source_sessions_for_correlation", AsyncMock(return_value=source_sessions)),
            patch("app.cadence.summary.generate_daily_summary", AsyncMock(return_value=True)),
        ):
            mock_collection.return_value.find_one = AsyncMock(return_value=None)
            repo = mock_repo_cls.return_value
            repo.list_session_index = AsyncMock(return_value=[])
            result = await try_generate_daily_summary(db=object(), session_id="sid-1")

        self.assertFalse(result)

    async def test_generates_when_source_sessions_terminal_even_with_index_gap(self):
        session = {"session_id": "sid-1", "project_id": "proj-1", "correlation_id": "corr-1"}
        source_sessions = [
            {"session_id": "sid-1", "status": "completed"},
            {"session_id": "sid-2", "status": "completed"},
        ]
        # index rows are incomplete due to drift.
        index_rows = [{"session_id": "sid-1", "correlation_expected_count": 2, "terminal": True}]

        db = object()
        with (
            patch("app.cadence.summary.get_session", AsyncMock(return_value=session)),
            patch("app.cadence.summary.get_brain_collection") as mock_collection,
            patch("app.cadence.summary.SessionRepository") as mock_repo_cls,
            patch("app.cadence.summary._list_source_sessions_for_correlation", AsyncMock(return_value=source_sessions)),
            patch("app.cadence.summary.generate_daily_summary", AsyncMock(return_value=True)) as gen,
        ):
            mock_collection.return_value.find_one = AsyncMock(return_value=None)
            repo = mock_repo_cls.return_value
            repo.list_session_index = AsyncMock(return_value=index_rows)
            result = await try_generate_daily_summary(db=db, session_id="sid-1")

        self.assertTrue(result)
        gen.assert_awaited_once_with(db, "corr-1", "proj-1")

    async def test_returns_false_when_no_index_and_no_source_sessions(self):
        session = {"session_id": "sid-1", "project_id": "proj-1", "correlation_id": "corr-1"}

        with (
            patch("app.cadence.summary.get_session", AsyncMock(return_value=session)),
            patch("app.cadence.summary.get_brain_collection") as mock_collection,
            patch("app.cadence.summary.SessionRepository") as mock_repo_cls,
            patch("app.cadence.summary._list_source_sessions_for_correlation", AsyncMock(return_value=[])),
            patch("app.cadence.summary.generate_daily_summary", AsyncMock(return_value=True)),
        ):
            mock_collection.return_value.find_one = AsyncMock(return_value=None)
            repo = mock_repo_cls.return_value
            repo.list_session_index = AsyncMock(return_value=[])
            result = await try_generate_daily_summary(db=object(), session_id="sid-1")

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
