import unittest

from app.domain.parsing.models import ParseOutcome
from app.domain.parsing.service import (
    ACTION_ALLOW_WRITE,
    ACTION_CLARIFY,
    ACTION_REJECT,
    build_clarification_payload,
    enforce_mutation_gate,
    parse_with_runtime_adapter,
    register_runtime_adapter,
)


class DomainParsingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_parse_with_runtime_adapter_missing_adapter_returns_unsupported(self):
        outcome = await parse_with_runtime_adapter("missing.adapter")
        self.assertEqual(outcome.status, "unsupported")
        self.assertEqual(outcome.reason_code, "unsupported_capability")

    async def test_parse_with_runtime_adapter_supports_async_adapter(self):
        async def _adapter(**_kwargs):
            return ParseOutcome(
                status="resolved",
                reason_code="resolved",
                confidence=1.0,
                requires_clarification=False,
                normalized_payload={"ok": True},
            )

        register_runtime_adapter("test.async_adapter", _adapter)
        outcome = await parse_with_runtime_adapter("test.async_adapter")
        self.assertEqual(outcome.status, "resolved")
        self.assertEqual(outcome.normalized_payload.get("ok"), True)

    async def test_parse_with_runtime_adapter_bootstraps_cortex_adapter(self):
        outcome = await parse_with_runtime_adapter(
            "cortex.write_args",
            tool_name="jira_update_status",
            args={"external_id": "PROJ-1", "status": "in review"},
            parser_context={"path": "service_bootstrap_test"},
        )
        self.assertEqual(outcome.status, "resolved")
        payload = outcome.normalized_payload if isinstance(outcome.normalized_payload, dict) else {}
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        self.assertEqual(str(args.get("status") or ""), "in_progress")

    async def test_parse_with_runtime_adapter_invalid_return_maps_to_invalid(self):
        def _invalid_adapter(**_kwargs):
            return {"invalid": "shape"}

        register_runtime_adapter("test.invalid_adapter", _invalid_adapter)
        outcome = await parse_with_runtime_adapter("test.invalid_adapter")
        self.assertEqual(outcome.status, "invalid")
        self.assertTrue(outcome.requires_clarification)

    def test_enforce_mutation_gate_actions(self):
        resolved = ParseOutcome(
            status="resolved",
            reason_code="resolved",
            confidence=1.0,
            requires_clarification=False,
            normalized_payload={},
        )
        ambiguous = ParseOutcome(
            status="ambiguous",
            reason_code="ambiguous_target",
            confidence=0.1,
            requires_clarification=True,
            normalized_payload={},
        )
        unsupported = ParseOutcome(
            status="unsupported",
            reason_code="unsupported_capability",
            confidence=0.0,
            requires_clarification=False,
            normalized_payload={},
        )

        self.assertEqual(enforce_mutation_gate(resolved).action, ACTION_ALLOW_WRITE)
        self.assertEqual(enforce_mutation_gate(ambiguous).action, ACTION_CLARIFY)
        self.assertEqual(enforce_mutation_gate(unsupported).action, ACTION_REJECT)

    def test_build_clarification_payload_standard_shape(self):
        payload = build_clarification_payload(
            reason_code="clarification_required",
            original_user_text="ship it now",
            parser_understanding={"status": "ship_it_now"},
            clarification_kind="status",
            retry_count=1,
            max_retries=2,
            candidate_statuses=["To Do", "In Progress"],
            extra={"task_index": 0},
        )
        self.assertEqual(payload.get("reason_code"), "clarification_required")
        self.assertEqual(payload.get("clarification_kind"), "status")
        self.assertEqual(payload.get("retry_count"), 1)
        self.assertEqual(payload.get("max_retries"), 2)
        self.assertEqual(payload.get("task_index"), 0)
        self.assertIn("candidate_statuses", payload)


if __name__ == "__main__":
    unittest.main()
