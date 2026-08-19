import unittest

from app.cortex.tools.contracts import canonicalize_tool_args, field_classes_for_tool


class CortexToolContractsTests(unittest.TestCase):
    def test_jira_update_description_aliases_canonicalize(self):
        canonical = canonicalize_tool_args(
            tool_name="jira_update_description",
            args={
                "issue_key": "SCRUM-25",
                "issue_description": "Updated acceptance criteria",
            },
        )
        self.assertEqual(canonical.get("external_id"), "SCRUM-25")
        self.assertEqual(canonical.get("description"), "Updated acceptance criteria")

    def test_jira_update_description_field_classes(self):
        classes = field_classes_for_tool("jira_update_description")
        self.assertEqual(classes.get("external_id"), "identifier_like")
        self.assertEqual(classes.get("description"), "description_like")

    def test_action_item_update_aliases_canonicalize(self):
        canonical = canonicalize_tool_args(
            tool_name="action_item_update",
            args={
                "display_key": "ACTION-7",
                "owner": "u-123",
            },
        )
        self.assertEqual(canonical.get("action_item_ref"), "ACTION-7")
        self.assertEqual(canonical.get("owner_user_id"), "u-123")


if __name__ == "__main__":
    unittest.main()
