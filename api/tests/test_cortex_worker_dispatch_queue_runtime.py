import importlib
import unittest
from unittest.mock import MagicMock, patch


worker_module = importlib.import_module("app.cortex.worker")


class CortexWorkerDispatchQueueRuntimeTests(unittest.TestCase):
    def test_dispatch_enqueue_uses_dramatiq_when_enabled(self):
        redis_conn = MagicMock()

        with patch.object(
            worker_module,
            "is_dramatiq_enabled_for_queue",
            return_value=True,
        ), patch.object(
            worker_module,
            "has_active_run",
            return_value=False,
        ), patch.object(
            worker_module,
            "has_pending_dispatch",
            return_value=False,
        ), patch.object(
            worker_module,
            "try_claim_dispatch",
            return_value=True,
        ), patch.object(
            worker_module,
            "dramatiq_enqueue",
            return_value={"accepted": True, "job_id": "dramatiq-msg-3"},
        ) as dramatiq_mock:
            result = worker_module._enqueue_dispatch_job_if_needed(
                redis_conn=redis_conn,
                session_key="proj-1234-5678:user-1:channel",
            )

        self.assertEqual(result, "dramatiq-msg-3")
        dramatiq_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
