import unittest

from app.cortex.tools.providers.jira_tools import JiraCreateTaskInput
from app.domain.capabilities.deterministic.service import deterministic_interpretation_service
from app.domain.capabilities.jira_bundles import get_jira_deterministic_pattern_specs
from app.tasks.providers.jira_adapter import JiraAdapter


class WorkItemCreateTypeSupportTests(unittest.TestCase):
    def test_deterministic_create_extracts_provider_type_slot(self):
        specs = get_jira_deterministic_pattern_specs()
        interpretation = deterministic_interpretation_service.interpret(
            text="create story authentication flow",
            pattern_specs=specs,
        )
        create_match = next(
            (m for m in interpretation.matches if str(m.action_type or "").strip() == "create_work_item"),
            None,
        )
        self.assertIsNotNone(create_match)
        self.assertEqual(
            str((create_match.extracted_slots or {}).get("provider_type") or "").strip().lower(),
            "story",
        )
        self.assertEqual(
            str((create_match.extracted_slots or {}).get("title") or "").strip().lower(),
            "authentication flow",
        )
        self.assertEqual(
            str((create_match.extracted_slots or {}).get("parent_external_id") or "").strip(),
            "",
        )

    def test_deterministic_create_extracts_subtask_parent_external_id(self):
        specs = get_jira_deterministic_pattern_specs()
        interpretation = deterministic_interpretation_service.interpret(
            text="create subtask add retry guard under PRM-88",
            pattern_specs=specs,
        )
        create_match = next(
            (m for m in interpretation.matches if str(m.action_type or "").strip() == "create_work_item"),
            None,
        )
        self.assertIsNotNone(create_match)
        self.assertEqual(
            str((create_match.extracted_slots or {}).get("provider_type") or "").strip().lower(),
            "subtask",
        )
        self.assertEqual(
            str((create_match.extracted_slots or {}).get("title") or "").strip().lower(),
            "add retry guard",
        )
        self.assertEqual(
            str((create_match.extracted_slots or {}).get("parent_external_id") or "").strip().upper(),
            "PRM-88",
        )

    def test_create_input_hydrates_provider_type_from_work_item_type(self):
        parsed = JiraCreateTaskInput(
            title="Fix payment retry edge case",
            work_item_type="defect",
            operation_id="op_workitem_create_type_001",
        )
        self.assertEqual(parsed.provider_type, "Bug")

    def test_create_input_strips_leading_for_from_title(self):
        parsed = JiraCreateTaskInput(
            title="for demo task 4",
            provider_type="Sub-task",
            operation_id="op_workitem_create_type_002",
        )
        self.assertEqual(parsed.title, "demo task 4")

    def test_jira_issue_type_resolution_supports_aliases_and_canonical_hints(self):
        self.assertEqual(
            JiraAdapter._resolve_issue_type_name(provider_type="subtask", work_item_type=None),
            "Sub-task",
        )
        self.assertEqual(
            JiraAdapter._resolve_issue_type_name(provider_type=None, work_item_type="portfolio"),
            "Epic",
        )
        self.assertEqual(
            JiraAdapter._resolve_issue_type_name(provider_type=None, work_item_type=None),
            "Task",
        )


if __name__ == "__main__":
    unittest.main()
