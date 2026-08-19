import unittest

from app.projects.recommendations.normalized_task_model import NormalizedTask


class NormalizedTaskModelTests(unittest.TestCase):
    def test_from_health_item_normalizes_fields(self):
        item = {
            "task_key": "PROJ-42",
            "title": "Fix pipeline",
            "status": "In Progress",
            "priority": "High",
            "health_status": "AT_RISK",
            "health_reason": "No updates",
            "health_action": "Post update",
            "assignee_name": "Alex",
            "assignee_email": "alex@example.com",
            "due_date": "2026-03-09T00:00:00",
            "start_date": "2026-03-01T00:00:00",
            "url": "https://example.local/task/PROJ-42",
        }

        normalized = NormalizedTask.from_health_item(item)

        self.assertEqual(normalized.task_key, "PROJ-42")
        self.assertEqual(normalized.status, "in_progress")
        self.assertEqual(normalized.status_category, "in_progress")
        self.assertEqual(normalized.priority, "high")
        self.assertEqual(normalized.health_status, "at_risk")
        self.assertEqual(normalized.assignee_name, "Alex")
        self.assertEqual(normalized.assignee_email, "alex@example.com")
        self.assertEqual(normalized.reopen_count, 0)
        self.assertIsNone(normalized.assignee_load_score)
        self.assertIsNone(normalized.sprint_state)
        self.assertIsNone(normalized.sprint_end_at)

    def test_from_health_item_defaults(self):
        normalized = NormalizedTask.from_health_item({})
        self.assertEqual(normalized.task_key, "")
        self.assertEqual(normalized.title, "")
        self.assertEqual(normalized.status, "")
        self.assertEqual(normalized.status_category, "unknown")
        self.assertEqual(normalized.priority, "")
        self.assertEqual(normalized.health_status, "")
        self.assertEqual(normalized.health_reason, "")
        self.assertEqual(normalized.health_action, "")
        self.assertEqual(normalized.reopen_count, 0)
        self.assertIsNone(normalized.assignee_load_score)
        self.assertIsNone(normalized.sprint_state)
        self.assertIsNone(normalized.sprint_end_at)

    def test_from_health_item_supports_sprint_nested_fields(self):
        item = {
            "task_key": "PROJ-77",
            "sprint": {
                "name": "Sprint 42",
                "state": "active",
                "end_at": "2026-03-11T10:00:00",
            },
        }
        normalized = NormalizedTask.from_health_item(item)
        self.assertEqual(normalized.sprint_name, "Sprint 42")
        self.assertEqual(normalized.sprint_state, "active")
        self.assertEqual(normalized.sprint_end_at, "2026-03-11T10:00:00")

    def test_from_health_item_uses_status_category_map_override(self):
        item = {
            "task_key": "PROJ-88",
            "status": "Ready For Dev",
        }
        normalized = NormalizedTask.from_health_item(
            item,
            status_category_map={
                "ready for dev": "not_started",
            },
        )
        self.assertEqual(normalized.status, "ready_for_dev")
        self.assertEqual(normalized.status_category, "not_started")


if __name__ == "__main__":
    unittest.main()
