import importlib
import unittest
from unittest.mock import patch


planner_router_module = importlib.import_module("app.planner.router")


class PlannerBootstrapQueueRuntimeTests(unittest.TestCase):
    def test_enqueue_uses_dramatiq_when_lock_available(self):
        with patch.object(
            planner_router_module,
            "acquire_planner_bootstrap_job_lock",
            return_value=True,
        ), patch.object(
            planner_router_module,
            "dramatiq_enqueue",
            return_value={"accepted": True, "job_id": "dramatiq-msg-3"},
        ) as dramatiq_mock, patch.object(
            planner_router_module,
            "_set_planner_bootstrap_status",
            return_value={"status": "queued"},
        ):
            result = planner_router_module._enqueue_planner_bootstrap_job("proj-1234-5678")

        self.assertTrue(result["accepted"])
        self.assertEqual(result["reason"], "queued")
        self.assertEqual(result["job_id"], "dramatiq-msg-3")
        dramatiq_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
