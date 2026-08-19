import unittest

from app.agent_runtime.executor import AgentExecutor


class CortexExecutorReadCompactionTests(unittest.TestCase):
    def test_compact_rows_preserve_status_display_and_status_raw(self):
        executor = AgentExecutor()
        rows = [
            {
                "external_id": "PRM-1",
                "title": "Improve API latency",
                "provider_type": "Bug",
                "status": "in_progress",
                "status_raw": "QA TEST",
                "status_display": "QA TEST",
                "assignee_name": "Prabhu raj",
                "due_date": "",
            }
        ]
        compact = executor._compact_work_item_rows(rows)
        self.assertEqual(len(compact), 1)
        self.assertEqual(compact[0].get("status"), "in_progress")
        self.assertEqual(compact[0].get("status_raw"), "QA TEST")
        self.assertEqual(compact[0].get("status_display"), "QA TEST")

    def test_compact_rows_include_extended_read_fields(self):
        executor = AgentExecutor()
        rows = [
            {
                "external_id": "PIT-12",
                "title": "work item creation failing due to missing issue type.",
                "provider_type": "Bug",
                "status": "todo",
                "status_raw": "Backlog",
                "status_display": "Backlog",
                "assignee_name": "Prabhu raj",
                "due_date": "",
                "description": "Detailed description text",
                "url": "https://example.atlassian.net/browse/PIT-12",
                "created_by": "Sridevi",
                "created_at": "2026-04-17T00:00:00Z",
                "updated_at": "2026-04-17T00:05:00Z",
            }
        ]
        compact = executor._compact_work_item_rows(rows)
        self.assertEqual(len(compact), 1)
        item = compact[0]
        self.assertEqual(item.get("link"), "https://example.atlassian.net/browse/PIT-12")
        self.assertEqual(item.get("description"), "Detailed description text")
        self.assertEqual(item.get("created_by"), "Sridevi")
        self.assertEqual(item.get("created_at"), "2026-04-17T00:00:00Z")
        self.assertEqual(item.get("updated_at"), "2026-04-17T00:05:00Z")


if __name__ == "__main__":
    unittest.main()

