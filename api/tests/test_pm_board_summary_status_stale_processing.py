import unittest
from unittest.mock import AsyncMock, patch

project_router_module = __import__("app.projects.router", fromlist=["get_pm_board_summary_status"])
get_pm_board_summary_status = project_router_module.get_pm_board_summary_status


class PMBoardSummaryStatusStaleProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_processing_without_lock_transitions_to_failed(self):
        with (
            patch(
                "app.projects.router.ProjectService.get_by_id",
                new_callable=AsyncMock,
                return_value={"_id": "69bee8596fe6571c0813bf6a", "project_id": "proj-5693-2556"},
            ),
            patch("app.projects.router.get_cached_pm_board_summary", return_value=None),
            patch(
                "app.projects.router._read_pm_board_job_status",
                return_value={"status": "processing", "trigger": "retry"},
            ),
            patch("app.projects.router._has_pm_board_job_lock", return_value=False),
            patch("app.projects.router._set_pm_board_job_status") as set_status_mock,
        ):
            payload = await get_pm_board_summary_status("69bee8596fe6571c0813bf6a")

        self.assertEqual(str(payload.get("status")), "failed")
        set_status_mock.assert_called_once()
        self.assertEqual(set_status_mock.call_args.kwargs.get("status_value"), "failed")
        self.assertEqual(
            str(set_status_mock.call_args.kwargs.get("error") or ""),
            "stale_processing_without_lock",
        )


if __name__ == "__main__":
    unittest.main()

