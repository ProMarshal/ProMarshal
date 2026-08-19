import importlib
import unittest
from datetime import datetime
from unittest.mock import patch


extraction_module = importlib.import_module("app.action_items.extraction")


class ActionItemExtractionQueueContractTests(unittest.TestCase):
    def test_cadence_enqueue_uses_dramatiq_actor(self):
        with patch.object(
            extraction_module,
            "dramatiq_enqueue",
            return_value={"accepted": True, "job_id": "dramatiq-msg-cadence"},
        ) as dramatiq_mock:
            result = extraction_module.enqueue_from_cadence_session_summary(
                project_id="proj-1234-5678",
                session_id="sid-1",
                actor_user_id="u-1",
                summary={"key_input": "Send status update"},
                reason="completed",
            )

        self.assertEqual(result, {"queued": True, "job_id": "dramatiq-msg-cadence"})
        dramatiq_mock.assert_called_once()

    def test_comment_enqueue_uses_dramatiq_actor(self):
        with patch.object(
            extraction_module,
            "dramatiq_enqueue",
            return_value={"accepted": True, "job_id": "dramatiq-msg-comment"},
        ) as dramatiq_mock:
            capture = {}

            def _capture_enqueue(*, queue_name, actor_name, kwargs=None):
                capture["queue_name"] = queue_name
                capture["actor_name"] = actor_name
                capture["kwargs"] = kwargs or {}
                return {"accepted": True, "job_id": "dramatiq-msg-comment"}

            dramatiq_mock.side_effect = _capture_enqueue
            result = extraction_module.enqueue_from_task_comment_mutation(
                project_id="proj-1234-5678",
                task_key="ABC-1",
                raw_comment_text="Please email ops",
                posted_comment_text="Please email ops",
                actor_user_id="u-2",
                mutation_timestamp="2026-03-14T12:01:02Z",
            )

        self.assertEqual(result, {"queued": True, "job_id": "dramatiq-msg-comment"})
        self.assertEqual(capture["queue_name"], "action_item_extraction")
        self.assertEqual(capture["actor_name"], "run_action_item_extraction")
        payload = capture["kwargs"]["payload"]
        self.assertEqual(payload["source_type"], "task_comment_mutation")
        self.assertEqual(payload["mutation_timestamp"], "2026-03-14T12:01:02Z")
        self.assertIn("comment_event", payload)
        self.assertEqual(payload["comment_event"]["provider"], "jira")
        self.assertEqual(payload["comment_event"]["source"], "task_service_mutate_task")
        self.assertEqual(payload["comment_event"]["task_key"], "ABC-1")
        self.assertEqual(payload["comment_event"]["comment_text"], "Please email ops")

    def test_enqueue_returns_queue_unavailable_when_dramatiq_rejects(self):
        with patch.object(
            extraction_module,
            "dramatiq_enqueue",
            return_value={"accepted": False, "reason": "broker_unavailable"},
        ):
            result = extraction_module.enqueue_from_cadence_session_summary(
                project_id="proj-1234-5678",
                session_id="sid-1",
                actor_user_id="u-1",
                summary={"key_input": "Send status update"},
                reason="completed",
            )

        self.assertEqual(result, {"queued": False, "reason": "queue_unavailable"})

    def test_cadence_enqueue_serializes_datetime_fields(self):
        capture = {}

        def _capture_enqueue(*, queue_name, actor_name, kwargs=None):
            capture["queue_name"] = queue_name
            capture["actor_name"] = actor_name
            capture["kwargs"] = kwargs or {}
            return {"accepted": True, "job_id": "dramatiq-msg-cadence"}

        with patch.object(
            extraction_module,
            "dramatiq_enqueue",
            side_effect=_capture_enqueue,
        ):
            result = extraction_module.enqueue_from_cadence_session_summary(
                project_id="proj-1234-5678",
                session_id="sid-2",
                actor_user_id="u-1",
                summary={
                    "generated_at": datetime(2026, 4, 14, 12, 0, 0),
                    "key_input": "follow up with design",
                },
                reason="completed",
            )

        self.assertEqual(result, {"queued": True, "job_id": "dramatiq-msg-cadence"})
        payload = capture["kwargs"]["payload"]
        self.assertIsInstance(payload["summary"]["generated_at"], str)
        self.assertIn("2026-04-14", payload["summary"]["generated_at"])


if __name__ == "__main__":
    unittest.main()
