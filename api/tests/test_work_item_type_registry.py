import unittest

from app.domain.work_items.types import (
    classify_work_item_type,
    resolve_work_item_type_from_item,
)


class WorkItemTypeRegistryTests(unittest.TestCase):
    def test_classify_work_item_type_covers_portfolio_and_service(self):
        self.assertEqual(classify_work_item_type("Epic"), "portfolio")
        self.assertEqual(classify_work_item_type("Milestone"), "portfolio")
        self.assertEqual(classify_work_item_type("Service Request"), "service")

    def test_classify_work_item_type_defaults_to_custom(self):
        self.assertEqual(classify_work_item_type(""), "custom")
        self.assertEqual(classify_work_item_type("VendorSpecificType"), "custom")

    def test_resolve_work_item_type_prefers_existing_canonical_field(self):
        row = {"work_item_type": "defect", "provider_type": "Story", "issue_type": "Task"}
        self.assertEqual(resolve_work_item_type_from_item(row), "defect")

    def test_resolve_work_item_type_falls_back_provider_then_issue(self):
        by_provider = {"provider_type": "Bug"}
        by_issue = {"issue_type": "Sub-task"}
        self.assertEqual(resolve_work_item_type_from_item(by_provider), "defect")
        self.assertEqual(resolve_work_item_type_from_item(by_issue), "subtask")


if __name__ == "__main__":
    unittest.main()
