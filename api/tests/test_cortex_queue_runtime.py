import importlib
import unittest
from unittest.mock import MagicMock, patch


cortex_queue_module = importlib.import_module("app.cortex.queue")


class CortexQueueRuntimeTests(unittest.TestCase):
    def test_enqueue_uses_dramatiq_when_enabled(self):
        with patch.object(
            cortex_queue_module,
            "get_redis_connection",
            return_value=MagicMock(),
        ), patch.object(
            cortex_queue_module,
            "session_key_from_ingress",
            return_value="proj-1234-5678:user-1:channel",
        ), patch.object(
            cortex_queue_module,
            "build_queue_item",
            return_value={"ingress": {"text": "hello"}},
        ), patch.object(
            cortex_queue_module,
            "has_active_run",
            return_value=False,
        ), patch.object(
            cortex_queue_module,
            "has_pending_dispatch",
            return_value=False,
        ), patch.object(
            cortex_queue_module,
            "try_claim_dispatch",
            return_value=True,
        ), patch.object(
            cortex_queue_module,
            "dramatiq_enqueue",
            return_value={"accepted": True, "job_id": "dramatiq-msg-1"},
        ) as dramatiq_mock:
            result = cortex_queue_module.enqueue_cortex_run(
                ingress={"project_id": "proj-1234-5678", "user_id": "user-1"},
                raw_event={"type": "message"},
            )

        self.assertTrue(result.queued)
        self.assertEqual(result.reason, "queued_new_dispatch")
        self.assertEqual(result.job_id, "dramatiq-msg-1")
        dramatiq_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
