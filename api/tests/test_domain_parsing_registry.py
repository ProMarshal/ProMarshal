import unittest

from app.domain.parsing.models import ParseOutcome
from app.domain.parsing.registry import ParseRegistry, message_for_reason_code


class DomainParsingRegistryTests(unittest.TestCase):
    def test_reason_catalog_formats_status_availability_prompt(self):
        text = message_for_reason_code(
            "clarification_required",
            context={
                "requested_status": "in_review",
                "available_statuses": ["To Do", "In Progress", "Done"],
            },
        )
        self.assertIn("in_review", text)
        self.assertIn("To Do", text)
        self.assertIn("Done", text)

    def test_registry_register_and_get(self):
        registry = ParseRegistry()

        def _adapter(*_args, **_kwargs):
            return ParseOutcome(
                status="resolved",
                reason_code="resolved",
                confidence=1.0,
                requires_clarification=False,
                normalized_payload={},
            )

        registry.register("cadence.status", _adapter)
        resolved = registry.get("cadence.status")
        self.assertIsNotNone(resolved)
        self.assertIs(resolved, _adapter)

    def test_reason_catalog_supports_backlog_ambiguous_target_variant(self):
        text = message_for_reason_code(
            "ambiguous_target",
            context={"target_kind": "backlog"},
        )
        self.assertIn("backlog tasks", text)
        self.assertIn("skip for now", text)

    def test_reason_catalog_supports_unsupported_operations_context(self):
        text = message_for_reason_code(
            "unsupported_capability",
            context={"operations": ["workitem_assign", "task_delete"]},
        )
        self.assertIn("currently available tools", text)
        self.assertIn("Unsupported operations", text)
        self.assertIn("workitem_assign", text)


if __name__ == "__main__":
    unittest.main()
