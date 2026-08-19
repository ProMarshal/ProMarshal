import importlib
import re
import unittest

from app.domain.capabilities.deterministic.models import ActionPatternSpec
from app.domain.capabilities.deterministic.pattern_registry import (
    list_action_patterns,
    pattern_registry,
)


class DeterministicPatternRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        pattern_registry.reset()

    def tearDown(self) -> None:
        pattern_registry.reset()

    def test_legacy_shim_import_removed(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("app.cortex.plans.pattern_registry")

    def test_register_and_list_patterns(self):
        spec = ActionPatternSpec(
            action_type="create_work_item",
            capability_id="workitem_create",
            tool_name="jira_create_work_item",
            regex=re.compile(r"create\s+task\s+(?P<title>.+)", re.IGNORECASE),
            field_extractors={"title": "title"},
            required_fields=["title"],
        )
        pattern_registry.register(spec, provider="jira", bundle_id="jira.create")
        canonical = list_action_patterns(capability_ids=["workitem_create"])
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0].normalized_action_type(), "create_work_item")
        self.assertEqual(canonical[0].normalized_capability_id(), "workitem_create")

    def test_duplicate_provider_capability_action_rejected(self):
        first = ActionPatternSpec(
            action_type="create_work_item",
            capability_id="workitem_create",
            tool_name="jira_create_work_item",
            regex=re.compile(r"create\s+task\s+(?P<title>.+)", re.IGNORECASE),
            field_extractors={"title": "title"},
            required_fields=["title"],
        )
        second = ActionPatternSpec(
            action_type="create_work_item",
            capability_id="workitem_create",
            tool_name="jira_create_work_item",
            regex=re.compile(r"add\s+task\s+(?P<title>.+)", re.IGNORECASE),
            field_extractors={"title": "title"},
            required_fields=["title"],
        )
        pattern_registry.register(first, provider="jira", bundle_id="jira.create.v1")
        with self.assertRaises(ValueError):
            pattern_registry.register(second, provider="jira", bundle_id="jira.create.v2")

    def test_legacy_action_aliases_normalize_to_canonical_work_item_actions(self):
        create_spec = ActionPatternSpec(
            action_type="create_task",
            capability_id="workitem_create",
            tool_name="jira_create_work_item",
            regex=re.compile(r"create\s+task\s+(?P<title>.+)", re.IGNORECASE),
            field_extractors={"title": "title"},
            required_fields=["title"],
        )
        assign_spec = ActionPatternSpec(
            action_type="assign_task",
            capability_id="workitem_assign",
            tool_name="jira_assign_work_item",
            regex=re.compile(r"assign\s+(?P<task_key>[A-Z]+-\d+)\s+to\s+(?P<assignee>.+)", re.IGNORECASE),
            field_extractors={"task_key": "task_key", "assignee": "assignee"},
            required_fields=["task_key", "assignee"],
        )
        self.assertEqual(create_spec.normalized_action_type(), "create_work_item")
        self.assertEqual(assign_spec.normalized_action_type(), "assign_work_item")


if __name__ == "__main__":
    unittest.main()
