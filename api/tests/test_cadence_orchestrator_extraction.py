import unittest
from unittest.mock import AsyncMock, patch

from app.cadence.orchestrator import (
    _build_status_parse_outcome,
    _build_status_clarification_text,
    _build_backlog_clarification_text,
    _classify_backlog_intent,
    _handle_backlog_clarification_state,
    _handle_status_clarification_state,
    _heuristic_extract,
    _provider_status_unavailable,
    _send_dm_text_best_effort,
    extract_status_and_comment,
    handle_cadence_dm,
)
from app.domain.parsing.models import PARSE_STATUS_AMBIGUOUS, ParseOutcome


class CadenceOrchestratorExtractionTests(unittest.TestCase):
    def test_on_hold_maps_to_no_change_with_comment(self):
        result = _heuristic_extract("this is on hold for now")
        self.assertEqual(result["status"], "no change")
        self.assertIn("on hold", result["comment"].lower())

    def test_paused_maps_to_no_change_with_comment(self):
        result = _heuristic_extract("paused until dependency is resolved")
        self.assertEqual(result["status"], "no change")
        self.assertIn("paused", result["comment"].lower())

    def test_done_maps_to_done(self):
        result = _heuristic_extract("its done, can be closed")
        self.assertEqual(result["status"], "done")

    def test_mixed_signal_status_is_ambiguous(self):
        outcome = _build_status_parse_outcome(
            text="code done, now in final testing",
            candidate_status="done",
            comment="in final testing",
            source="heuristic",
        )
        self.assertEqual(outcome.status, "ambiguous")
        self.assertEqual(outcome.reason_code, "ambiguous_mixed_signals")
        self.assertTrue(outcome.requires_clarification)

    def test_explicit_done_is_resolved(self):
        outcome = _build_status_parse_outcome(
            text="done and shipped",
            candidate_status="done",
            comment="",
            source="heuristic",
        )
        self.assertEqual(outcome.status, "resolved")
        self.assertEqual(outcome.normalized_payload.get("status"), "done")

    def test_terminal_text_with_in_progress_candidate_is_ambiguous(self):
        outcome = _build_status_parse_outcome(
            text="done",
            candidate_status="in progress",
            comment="",
            source="llm",
        )
        self.assertEqual(outcome.status, "ambiguous")
        self.assertEqual(outcome.reason_code, "ambiguous_mixed_signals")


class CadenceBacklogIntentParseTests(unittest.IsolatedAsyncioTestCase):
    async def test_backlog_mixed_show_and_later_is_ambiguous(self):
        outcome = await _classify_backlog_intent("show later items now")
        self.assertEqual(outcome.status, "ambiguous")
        self.assertEqual(outcome.reason_code, "ambiguous_target")
        self.assertTrue(outcome.requires_clarification)

    async def test_backlog_llm_show_path_is_resolved(self):
        with patch(
            "app.cadence.orchestrator._classify_backlog_intent_with_llm",
            AsyncMock(return_value="show_backlog"),
        ):
            outcome = await _classify_backlog_intent("can we revisit these now")
        self.assertEqual(outcome.status, "resolved")
        self.assertEqual((outcome.normalized_payload or {}).get("intent"), "show_backlog")

    async def test_backlog_llm_unclear_path_is_ambiguous(self):
        with patch(
            "app.cadence.orchestrator._classify_backlog_intent_with_llm",
            AsyncMock(return_value="unclear"),
        ):
            outcome = await _classify_backlog_intent("undecided currently")
        self.assertEqual(outcome.status, "ambiguous")
        self.assertTrue(outcome.requires_clarification)

    async def test_backlog_parser_accepts_runtime_parser_context_kwargs(self):
        outcome = await _classify_backlog_intent(
            "show",
            parser_context={"session_id": "sid-ctx", "state": "awaiting_backlog_decision"},
        )
        self.assertEqual(outcome.status, "resolved")
        self.assertEqual((outcome.normalized_payload or {}).get("intent"), "show_backlog")


class CadenceLLMParseOutcomeGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_candidate_still_blocks_when_mixed_signals(self):
        with patch(
            "app.cadence.llm_service._call_llm",
            AsyncMock(return_value='{"status":"done","comment":"in final testing"}'),
        ):
            outcome = await extract_status_and_comment("code done, now in final testing")
        self.assertEqual(outcome.status, "ambiguous")
        self.assertEqual(outcome.reason_code, "ambiguous_mixed_signals")
        self.assertTrue(outcome.requires_clarification)

    async def test_status_parser_accepts_runtime_parser_context_kwargs(self):
        outcome = await extract_status_and_comment(
            "it is still in progress",
            parser_context={"session_id": "sid-ctx", "state": "awaiting_status_clarification"},
        )
        self.assertIn(outcome.status, {"resolved", "ambiguous"})


class CadenceStatusProviderAvailabilityTests(unittest.TestCase):
    def test_provider_status_unavailable_detects_missing_target_transition(self):
        self.assertTrue(
            _provider_status_unavailable(
                requested_status="in_review",
                available_statuses=["To Do", "In Progress", "Done"],
            )
        )

    def test_provider_status_unavailable_false_when_transition_exists(self):
        self.assertFalse(
            _provider_status_unavailable(
                requested_status="in_review",
                available_statuses=["To Do", "In Review", "Done"],
            )
        )

    def test_status_clarification_message_includes_available_statuses(self):
        outcome = ParseOutcome(
            status="ambiguous",
            reason_code="clarification_required",
            confidence=0.2,
            requires_clarification=True,
            normalized_payload={"status": "in_review", "comment": "", "source": "heuristic"},
        )
        text = _build_status_clarification_text(
            outcome=outcome,
            candidate_statuses=["To Do", "In Progress", "Done"],
        )
        self.assertIn("in_review", text)
        self.assertIn("To Do", text)
        self.assertIn("In Progress", text)


class CadenceClarificationLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_clarification_hits_retry_limit_with_safe_fallback(self):
        ambiguous_outcome = ParseOutcome(
            status=PARSE_STATUS_AMBIGUOUS,
            reason_code="ambiguous_mixed_signals",
            confidence=0.2,
            requires_clarification=True,
            normalized_payload={"status": "done", "comment": "testing", "source": "llm"},
        )
        session = {
            "session_id": "sid-1",
            "clarification_payload": {"retry_count": 1, "max_retries": 2, "task_index": 0},
            "tasks": [{"external_id": "SCRUM-1", "status": "in progress"}],
            "current_task_index": 0,
        }
        with patch(
            "app.cadence.orchestrator.extract_status_and_comment",
            AsyncMock(return_value=ambiguous_outcome),
        ), patch(
            "app.cadence.orchestrator.clear_clarification_state",
            AsyncMock(return_value=True),
        ) as clear_mock, patch(
            "app.cadence.orchestrator._send_dm_text_best_effort",
            AsyncMock(return_value=None),
        ) as send_mock, patch(
            "app.cadence.orchestrator._advance_after_task_update",
            AsyncMock(return_value=True),
        ) as advance_mock:
            handled = await _handle_status_clarification_state(
                object(),
                session=session,
                text="still done but in testing",
                slack_client=AsyncMock(),
                channel="D123",
            )
        self.assertTrue(handled)
        clear_mock.assert_awaited_once()
        advance_mock.assert_awaited_once()
        self.assertIn("no change", send_mock.await_args.kwargs.get("text", "").lower())

    async def test_backlog_clarification_hits_retry_limit_with_no_mutation(self):
        ambiguous_outcome = ParseOutcome(
            status=PARSE_STATUS_AMBIGUOUS,
            reason_code="ambiguous_target",
            confidence=0.2,
            requires_clarification=True,
            normalized_payload={"intent": "unclear"},
        )
        session = {
            "session_id": "sid-2",
            "clarification_payload": {
                "retry_count": 1,
                "max_retries": 2,
                "return_state": "awaiting_backlog_decision",
            },
            "backlog_tasks": [{"external_id": "SCRUM-9", "title": "Backlog item"}],
        }
        with patch(
            "app.cadence.orchestrator._classify_backlog_intent",
            AsyncMock(return_value=ambiguous_outcome),
        ), patch(
            "app.cadence.orchestrator.clear_clarification_state",
            AsyncMock(return_value=True),
        ) as clear_mock, patch(
            "app.cadence.orchestrator._send_dm_text_best_effort",
            AsyncMock(return_value=None),
        ) as send_mock, patch(
            "app.cadence.orchestrator.set_clarification_state",
            AsyncMock(return_value=True),
        ) as set_mock:
            handled = await _handle_backlog_clarification_state(
                object(),
                session=session,
                text="maybe",
                slack_client=AsyncMock(),
                channel="D123",
            )
        self.assertTrue(handled)
        clear_mock.assert_awaited_once()
        set_mock.assert_not_awaited()
        self.assertIn("no backlog change", send_mock.await_args.kwargs.get("text", "").lower())
        self.assertIn("reply `show` or `later`", send_mock.await_args.kwargs.get("text", "").lower())


class CadenceClarificationPromptFormatTests(unittest.IsolatedAsyncioTestCase):
    async def test_dm_clarification_prompt_uses_plain_text_post_message(self):
        slack_client = AsyncMock()
        text = _build_backlog_clarification_text()
        await _send_dm_text_best_effort(slack_client=slack_client, channel="D123", text=text)
        slack_client.chat_postMessage.assert_awaited_once()
        kwargs = slack_client.chat_postMessage.await_args.kwargs
        self.assertEqual(set(kwargs.keys()), {"channel", "text"})
        self.assertEqual(kwargs["channel"], "D123")
        self.assertEqual(kwargs["text"], text)


class CadenceClarificationRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_dm_routes_backlog_clarification_state_before_agent_runtime(self):
        session = {"session_id": "sid-rt1", "status": "awaiting_backlog_clarification"}
        with patch(
            "app.cadence.orchestrator._handle_backlog_clarification_state",
            AsyncMock(return_value=True),
        ) as handler:
            handled = await handle_cadence_dm(
                object(),
                session=session,
                text="show",
                slack_client=AsyncMock(),
                channel="D123",
            )
        self.assertTrue(handled)
        handler.assert_awaited_once()

    async def test_dm_routes_status_clarification_state_before_agent_runtime(self):
        session = {"session_id": "sid-rt2", "status": "awaiting_status_clarification"}
        with patch(
            "app.cadence.orchestrator._handle_status_clarification_state",
            AsyncMock(return_value=True),
        ) as handler:
            handled = await handle_cadence_dm(
                object(),
                session=session,
                text="in progress",
                slack_client=AsyncMock(),
                channel="D123",
            )
        self.assertTrue(handled)
        handler.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
