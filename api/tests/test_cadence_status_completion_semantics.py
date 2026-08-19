import unittest
from unittest.mock import AsyncMock, patch

from app.cadence import session_store
from app.cadence.formatters import format_session_summary
from app.cadence.summary import _status_category_for_summary


class CadenceStatusCompletionSemanticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_session_summary_treats_project_done_category_as_completed(self):
        session = {
            "session_id": "sid-1",
            "project_id": "proj-1",
            "user_id": "user-1",
            "tasks": [
                {
                    "external_id": "PIT-4",
                    "title": "Bug fix",
                    "status": "in_progress",
                    "updated_status": "QA TEST",
                    "comment": "",
                }
            ],
            "overall_update_input": "",
            "overall_update_input_skipped": True,
            "created_at": None,
            "completed_at": None,
        }
        captured_summary = {}

        async def _capture_summary(_db, _session_id, summary):
            captured_summary.update(summary)
            return True

        fake_db = AsyncMock()
        fake_db.projects.find_one = AsyncMock(
            return_value={"recommendation_status_category_map": {"QA TEST": "done"}}
        )

        with (
            patch("app.cadence.session_store.get_session", AsyncMock(return_value=session)),
            patch("app.cadence.session_store._persist_session_summary", AsyncMock(side_effect=_capture_summary)),
        ):
            ok = await session_store.build_and_store_session_summary(
                fake_db,
                "sid-1",
                reason="agenda_complete",
                member_name="Prabhu",
            )

        self.assertTrue(ok)
        self.assertEqual(len(captured_summary.get("tasks_completed") or []), 1)
        self.assertEqual((captured_summary.get("tasks_completed") or [])[0].get("key"), "PIT-4")

    def test_summary_helper_maps_custom_status_to_done_category(self):
        category = _status_category_for_summary(
            status_value="QA TEST",
            status_category_map={"QA TEST": "done"},
        )
        self.assertEqual(category, "done")

    def test_session_formatter_marks_done_equivalent_statuses_as_done(self):
        payload = format_session_summary(
            "Summary",
            [
                {
                    "title": "Task A",
                    "status": "in_progress",
                    "updated_status": "resolved",
                    "comment": "",
                }
            ],
        )
        text_blocks = [
            str(((block or {}).get("text") or {}).get("text") or "")
            for block in (payload.get("blocks") or [])
            if isinstance(block, dict)
        ]
        combined = "\n".join(text_blocks)
        self.assertIn("marked done", combined)


if __name__ == "__main__":
    unittest.main()
