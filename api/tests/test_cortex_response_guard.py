import unittest

from app.cortex.response_guard import CortexResponseGuard


class CortexResponseGuardTests(unittest.TestCase):
    def test_appends_guard_when_commitment_text_and_non_idempotent_write_failed(self):
        text = "I created the action item and assigned it."
        raw_response = {
            "tool_error_count": 1,
            "tool_errors": [
                {
                    "tool_name": "jira_create_work_item",
                    "error_code": "upstream_unavailable",
                    "error_class": "upstream",
                    "user_message": "Jira unavailable.",
                }
            ],
        }
        tool_descriptors = [
            {"name": "jira_create_work_item", "idempotency": "non_idempotent"},
            {"name": "jira_search_work_items", "idempotency": "read_only"},
        ]

        guarded = CortexResponseGuard.apply(
            response_text=text,
            raw_response=raw_response,
            tool_descriptors=tool_descriptors,
        )

        self.assertIn(text, guarded)
        self.assertIn(CortexResponseGuard.COMMITMENT_GUARD_CLAUSE, guarded)

    def test_does_not_append_guard_when_no_commitment_language(self):
        text = "I could not complete that update due to a provider error."
        raw_response = {
            "tool_error_count": 1,
            "tool_errors": [{"tool_name": "jira_create_work_item"}],
        }
        tool_descriptors = [{"name": "jira_create_work_item", "idempotency": "non_idempotent"}]

        guarded = CortexResponseGuard.apply(
            response_text=text,
            raw_response=raw_response,
            tool_descriptors=tool_descriptors,
        )
        self.assertEqual(guarded, text)

    def test_does_not_append_guard_for_read_only_tool_failures(self):
        text = "I created a report for you."
        raw_response = {
            "tool_error_count": 1,
            "tool_errors": [{"tool_name": "jira_search_work_items"}],
        }
        tool_descriptors = [{"name": "jira_search_work_items", "idempotency": "read_only"}]

        guarded = CortexResponseGuard.apply(
            response_text=text,
            raw_response=raw_response,
            tool_descriptors=tool_descriptors,
        )
        self.assertEqual(guarded, text)

    def test_guard_clause_is_not_duplicated(self):
        base = (
            "I created the task.\n\n"
            + CortexResponseGuard.COMMITMENT_GUARD_CLAUSE
        )
        raw_response = {
            "tool_error_count": 1,
            "tool_errors": [{"tool_name": "jira_create_work_item"}],
        }
        tool_descriptors = [{"name": "jira_create_work_item", "idempotency": "non_idempotent"}]

        guarded = CortexResponseGuard.apply(
            response_text=base,
            raw_response=raw_response,
            tool_descriptors=tool_descriptors,
        )
        self.assertEqual(guarded, base)


if __name__ == "__main__":
    unittest.main()

