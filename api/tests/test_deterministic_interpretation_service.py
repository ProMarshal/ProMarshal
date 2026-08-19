import re
import unittest

from app.domain.capabilities.deterministic.models import ActionPatternSpec
from app.domain.capabilities.deterministic.service import deterministic_interpretation_service


class DeterministicInterpretationServiceTests(unittest.TestCase):
    def test_interpretation_scores_slot_coverage_and_missing_slots(self):
        specs = [
            ActionPatternSpec(
                action_type="create_work_item",
                capability_id="workitem_create",
                tool_name="jira_create_work_item",
                regex=re.compile(r"create task (?P<title>.+?)(?=(?: and |$))", re.IGNORECASE),
                field_extractors={"title": "title"},
                required_fields=["title"],
            ),
            ActionPatternSpec(
                action_type="assign_work_item",
                capability_id="workitem_assign",
                tool_name="jira_assign_work_item",
                regex=re.compile(r"assign(?: (?P<target>[A-Z]+-\d+))? to (?P<assignee>.+)$", re.IGNORECASE),
                field_extractors={"target": "target", "assignee": "assignee"},
                required_fields=["assignee"],
            ),
        ]
        interpretation = deterministic_interpretation_service.interpret(
            text="create task onboarding checklist and assign to alex@example.com",
            pattern_specs=specs,
        )
        self.assertGreaterEqual(len(interpretation.matches), 2)
        self.assertGreaterEqual(interpretation.slot_coverage, 0.5)
        self.assertGreaterEqual(interpretation.confidence, 0.4)

    def test_missing_required_slot_emits_reason_payload(self):
        specs = [
            ActionPatternSpec(
                action_type="assign_work_item",
                capability_id="workitem_assign",
                tool_name="jira_assign_work_item",
                regex=re.compile(
                    r"assign(?: (?P<target>[A-Z]+-\d+))?\s+to(?:\s+(?P<assignee>.*))?$",
                    re.IGNORECASE,
                ),
                field_extractors={"target": "target", "assignee": "assignee"},
                required_fields=["assignee"],
            ),
        ]
        interpretation = deterministic_interpretation_service.interpret(
            text="assign SCRUM-1 to",
            pattern_specs=specs,
        )
        self.assertEqual(len(interpretation.matches), 1)
        self.assertIn("assign_work_item:workitem_assign", interpretation.missing_slots)
        self.assertIn("assignee", interpretation.missing_slots["assign_work_item:workitem_assign"])


if __name__ == "__main__":
    unittest.main()
