import unittest
from datetime import datetime

from app.projects.recommendations.provider_adapters.jira import JiraTaskAdapter


class JiraTaskAdapterTests(unittest.TestCase):
    def test_normalize_task_maps_jira_fields(self):
        adapter = JiraTaskAdapter()
        raw = {
            "external_id": "PROJ-10",
            "title": "Fix auth flow",
            "status": "In Progress",
            "priority": "High",
            "assignee_name": "Sam",
            "assignee_email": "sam@example.com",
            "due_date": datetime(2026, 3, 9, 10, 30, 0),
            "start_date": datetime(2026, 3, 8, 9, 0, 0),
            "url": "https://jira.local/browse/PROJ-10",
        }

        normalized = adapter.normalize_task(raw)

        self.assertEqual(normalized.task_key, "PROJ-10")
        self.assertEqual(normalized.status, "in_progress")
        self.assertEqual(normalized.status_category, "unknown")
        self.assertEqual(normalized.priority, "high")
        self.assertEqual(normalized.assignee_name, "Sam")
        self.assertEqual(normalized.assignee_email, "sam@example.com")
        self.assertTrue((normalized.due_date or "").startswith("2026-03-09"))

    def test_capabilities_exposed(self):
        adapter = JiraTaskAdapter()
        self.assertTrue(adapter.capabilities.has_reviews)
        self.assertTrue(adapter.capabilities.has_sprints)
        self.assertTrue(adapter.capabilities.has_dependencies)


if __name__ == "__main__":
    unittest.main()
