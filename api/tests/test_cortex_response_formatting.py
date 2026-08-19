import unittest

from app.cortex.response import _normalize_slack_text_format


class CortexResponseFormattingTests(unittest.TestCase):
    def test_preserves_bold_id_type_for_bullet_rows(self):
        raw = (
            "Here are the matching work items (2):\n"
            "- *PRM-86 (Task)*: Slack reconnect bug\n"
            "  Status: In Progress | Assignee: Prabhu raj | Due: Not set\n\n"
            "- *PRM-1 (Epic)*: Foundation\n"
            "  Status: Todo | Assignee: Prabhu raj | Due: 2025-12-01"
        )
        formatted = _normalize_slack_text_format(raw)
        self.assertIn("- *PRM-86 (Task)*: Slack reconnect bug", formatted)
        self.assertIn("- *PRM-1 (Epic)*: Foundation", formatted)

    def test_does_not_promote_indented_status_line_to_heading(self):
        raw = (
            "Here are the matching work items (1):\n"
            "- *PRM-2 (Epic)*: MVP\n"
            "  Status: In Progress | Assignee: Prabhu raj | Due: Not set"
        )
        formatted = _normalize_slack_text_format(raw)
        self.assertIn("  Status: In Progress | Assignee: Prabhu raj | Due: Not set", formatted)
        self.assertNotIn("*Status: In Progress | Assignee: Prabhu raj | Due: Not set*", formatted)


if __name__ == "__main__":
    unittest.main()
