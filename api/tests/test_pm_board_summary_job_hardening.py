import unittest
from unittest.mock import AsyncMock, MagicMock, patch

project_router_module = __import__("app.projects.router", fromlist=["_process_pm_board_summary_job_async"])
_process_pm_board_summary_job_async = project_router_module._process_pm_board_summary_job_async


class PMBoardSummaryJobHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_owner_passed_to_mongo_ready(self):
        """ensure_mongo_ready receives Dramatiq runtime owner token."""
        with (
            patch("app.projects.router.ensure_mongo_ready", new_callable=AsyncMock) as mock_ensure_mongo_ready,
            patch("app.projects.router.ProjectService.get_by_id", new_callable=AsyncMock, return_value=None),
            patch("app.projects.router._set_pm_board_job_status"),
            patch("app.projects.router._release_pm_board_job_lock"),
        ):
            await _process_pm_board_summary_job_async(
                project_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                trigger="test",
                custom_project_id="proj-1234-5678",
            )

        mock_ensure_mongo_ready.assert_awaited_once_with(runtime_owner="dramatiq_event_loop_thread")

    async def test_failed_status_written_on_early_lookup_failure(self):
        """When project lookup returns None, status=failed is written using the seeded custom_project_id."""
        mock_set_status = MagicMock()
        with (
            patch("app.projects.router.ensure_mongo_ready", new_callable=AsyncMock),
            patch("app.projects.router.ProjectService.get_by_id", new_callable=AsyncMock, return_value=None),
            patch("app.projects.router._set_pm_board_job_status", mock_set_status),
            patch("app.projects.router._release_pm_board_job_lock"),
        ):
            await _process_pm_board_summary_job_async(
                project_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                trigger="test",
                custom_project_id="proj-1234-5678",
            )

        failed_calls = [
            call for call in mock_set_status.call_args_list
            if call.kwargs.get("status_value") == "failed"
        ]
        self.assertEqual(len(failed_calls), 1)
        self.assertEqual(failed_calls[0].kwargs.get("custom_project_id"), "proj-1234-5678")

    async def test_lock_released_on_early_lookup_failure(self):
        """When project lookup returns None, the job lock is always released using the seeded custom_project_id."""
        mock_release = MagicMock()
        with (
            patch("app.projects.router.ensure_mongo_ready", new_callable=AsyncMock),
            patch("app.projects.router.ProjectService.get_by_id", new_callable=AsyncMock, return_value=None),
            patch("app.projects.router._set_pm_board_job_status"),
            patch("app.projects.router._release_pm_board_job_lock", mock_release),
        ):
            await _process_pm_board_summary_job_async(
                project_id="aaaaaaaaaaaaaaaaaaaaaaaa",
                trigger="test",
                custom_project_id="proj-1234-5678",
            )

        mock_release.assert_called_once_with("proj-1234-5678")
