import unittest
from unittest.mock import AsyncMock, patch

from app.cortex.orchestrator import OrchestratorResult
from app.cortex.queue import CortexQueueEnqueueResult
from app.cortex.slack_events import SlackEventEnvelope, handoff_slack_event_to_cortex


class CortexSlackHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_handles_error_as_accepted_when_already_delivered(self):
        envelope = SlackEventEnvelope(
            team_id="T1",
            channel_id="D1",
            user_id="U1",
            text="create task",
            event_ts="1710000.1",
            raw_event={"type": "message"},
        )

        fake_orchestrator = AsyncMock()
        fake_orchestrator.handle_turn.return_value = OrchestratorResult(
            ok=False,
            response_text=None,
            error="assignee_not_mapped",
            metadata={"delivered": True},
        )

        with patch(
            "app.cortex.slack_events.enqueue_cortex_run",
            return_value=CortexQueueEnqueueResult(queued=False, reason="cortex_queue_unavailable"),
        ), patch("app.cortex.slack_events.CortexOrchestrator", return_value=fake_orchestrator):
            result = await handoff_slack_event_to_cortex(envelope, project_id="proj-1")

        self.assertTrue(result.accepted)
        self.assertEqual(result.reason, "assignee_not_mapped")
        self.assertIsNone(result.response_text)
        self.assertFalse(result.used_tool_call)
        self.assertEqual(result.tool_calls_count, 0)

    async def test_handles_error_as_accepted_when_response_text_available(self):
        envelope = SlackEventEnvelope(
            team_id="T1",
            channel_id="D1",
            user_id="U1",
            text="create task",
            event_ts="1710000.2",
            raw_event={"type": "message"},
        )

        fake_orchestrator = AsyncMock()
        fake_orchestrator.handle_turn.return_value = OrchestratorResult(
            ok=False,
            response_text="Please provide assignee email.",
            error="assignee_not_mapped",
            metadata={"delivered": False},
        )

        with patch(
            "app.cortex.slack_events.enqueue_cortex_run",
            return_value=CortexQueueEnqueueResult(queued=False, reason="cortex_queue_unavailable"),
        ), patch("app.cortex.slack_events.CortexOrchestrator", return_value=fake_orchestrator):
            result = await handoff_slack_event_to_cortex(envelope, project_id="proj-1")

        self.assertTrue(result.accepted)
        self.assertEqual(result.reason, "assignee_not_mapped")
        self.assertEqual(result.response_text, "Please provide assignee email.")
        self.assertFalse(result.used_tool_call)
        self.assertEqual(result.tool_calls_count, 0)

    async def test_handles_error_as_accepted_when_nested_delivery_flag_present(self):
        envelope = SlackEventEnvelope(
            team_id="T1",
            channel_id="D1",
            user_id="U1",
            text="create task",
            event_ts="1710000.25",
            raw_event={"type": "message"},
        )

        fake_orchestrator = AsyncMock()
        fake_orchestrator.handle_turn.return_value = OrchestratorResult(
            ok=False,
            response_text=None,
            error="insufficient_argument_quality",
            metadata={"delivery": {"ok": True}},
        )

        with patch(
            "app.cortex.slack_events.enqueue_cortex_run",
            return_value=CortexQueueEnqueueResult(queued=False, reason="cortex_queue_unavailable"),
        ), patch("app.cortex.slack_events.CortexOrchestrator", return_value=fake_orchestrator):
            result = await handoff_slack_event_to_cortex(envelope, project_id="proj-1")

        self.assertTrue(result.accepted)
        self.assertEqual(result.reason, "insufficient_argument_quality")
        self.assertIsNone(result.response_text)
        self.assertFalse(result.used_tool_call)
        self.assertEqual(result.tool_calls_count, 0)

    async def test_keeps_unaccepted_when_error_has_no_delivery_or_response(self):
        envelope = SlackEventEnvelope(
            team_id="T1",
            channel_id="D1",
            user_id="U1",
            text="create task",
            event_ts="1710000.3",
            raw_event={"type": "message"},
        )

        fake_orchestrator = AsyncMock()
        fake_orchestrator.handle_turn.return_value = OrchestratorResult(
            ok=False,
            response_text=None,
            error="executor_failed",
            metadata={"delivered": False},
        )

        with patch(
            "app.cortex.slack_events.enqueue_cortex_run",
            return_value=CortexQueueEnqueueResult(queued=False, reason="cortex_queue_unavailable"),
        ), patch("app.cortex.slack_events.CortexOrchestrator", return_value=fake_orchestrator):
            result = await handoff_slack_event_to_cortex(envelope, project_id="proj-1")

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "executor_failed")
        self.assertFalse(result.used_tool_call)
        self.assertEqual(result.tool_calls_count, 0)

    async def test_extracts_tool_call_metadata_from_orchestrator_result(self):
        envelope = SlackEventEnvelope(
            team_id="T1",
            channel_id="D1",
            user_id="U1",
            text="list all work items",
            event_ts="1710000.4",
            raw_event={"type": "message"},
        )

        fake_orchestrator = AsyncMock()
        fake_orchestrator.handle_turn.return_value = OrchestratorResult(
            ok=False,
            response_text="Here is what I found.",
            error="partial_failure",
            metadata={"delivered": False, "executor": {"tool_calls_count": 2}},
        )

        with patch(
            "app.cortex.slack_events.enqueue_cortex_run",
            return_value=CortexQueueEnqueueResult(queued=False, reason="cortex_queue_unavailable"),
        ), patch("app.cortex.slack_events.CortexOrchestrator", return_value=fake_orchestrator):
            result = await handoff_slack_event_to_cortex(envelope, project_id="proj-1")

        self.assertTrue(result.accepted)
        self.assertEqual(result.tool_calls_count, 2)
        self.assertTrue(result.used_tool_call)


if __name__ == "__main__":
    unittest.main()
