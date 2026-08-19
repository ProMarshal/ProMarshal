import unittest

from app.cortex.parsing_adapter import parse_cortex_write_args


class CortexParsingAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_alias_normalizes_for_jira_update_status(self):
        outcome = await parse_cortex_write_args(
            tool_name="jira_update_status",
            args={"external_id": "SCRUM-1", "status": "in review"},
            parser_context={},
        )
        self.assertEqual(outcome.status, "resolved")
        normalized_args = (outcome.normalized_payload or {}).get("args") or {}
        self.assertEqual(normalized_args.get("status"), "in_progress")

    async def test_custom_status_passthrough_for_jira_update_status(self):
        outcome = await parse_cortex_write_args(
            tool_name="jira_update_status",
            args={"external_id": "SCRUM-1", "status": "ship_it_now"},
            parser_context={},
        )
        self.assertEqual(outcome.status, "resolved")
        self.assertFalse(outcome.requires_clarification)
        normalized_args = (outcome.normalized_payload or {}).get("args") or {}
        self.assertEqual(normalized_args.get("status"), "ship_it_now")

    async def test_non_status_write_tool_passthrough(self):
        outcome = await parse_cortex_write_args(
            tool_name="jira_add_comment",
            args={"external_id": "SCRUM-1", "comment": "Looks good"},
            parser_context={},
        )
        self.assertEqual(outcome.status, "resolved")
        normalized_args = (outcome.normalized_payload or {}).get("args") or {}
        self.assertEqual(normalized_args.get("comment"), "Looks good")


if __name__ == "__main__":
    unittest.main()
