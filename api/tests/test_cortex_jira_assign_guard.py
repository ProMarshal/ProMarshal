import unittest

from app.cortex.tools.providers.jira_tools import JiraAssignTaskTool


class CortexJiraAssignGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_jira_assign_rejects_action_item_reference_prefix(self):
        tool = JiraAssignTaskTool()
        result = await tool.execute_with_context(
            {
                "external_id": "ACTION-10",
                "assignee": "sridevi@example.com",
                "operation_id": "op_test_assign_guard_1",
            },
            {
                "project_id": "proj-1234-5678",
                "user_id": "u-owner",
            },
        )

        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_code"), "invalid_target_type")


if __name__ == "__main__":
    unittest.main()

