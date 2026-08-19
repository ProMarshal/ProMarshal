import importlib
import unittest
from unittest.mock import MagicMock, patch


projects_router_module = importlib.import_module("app.projects.router")


class _FakeRedis:
    def __init__(self, lock_claimed: bool = True):
        self._lock_claimed = lock_claimed

    def set(self, key, value, nx=True, ex=60):  # pragma: no cover - exercised by module under test
        return self._lock_claimed


class PMBoardSummaryQueueRuntimeTests(unittest.TestCase):
    def test_enqueue_uses_dramatiq_when_enabled(self):
        with patch.object(
            projects_router_module,
            "get_redis_connection",
            return_value=_FakeRedis(lock_claimed=True),
        ), patch.object(
            projects_router_module,
            "dramatiq_enqueue",
            return_value={"accepted": True, "job_id": "dramatiq-msg-2"},
        ) as dramatiq_mock, patch.object(
            projects_router_module,
            "_set_pm_board_job_status",
            return_value={"status": "queued"},
        ):
            result = projects_router_module._enqueue_pm_board_summary_job(
                project_id="project-doc-id-1",
                custom_project_id="proj-1234-5678",
                trigger="manual_refresh",
            )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["reason"], "queued")
        self.assertEqual(result["job_id"], "dramatiq-msg-2")
        dramatiq_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
