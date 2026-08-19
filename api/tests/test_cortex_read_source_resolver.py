import unittest

from app.cortex.read_source.resolver import ReadSourceResolver


class CortexReadSourceResolverTests(unittest.TestCase):
    def setUp(self):
        self.resolver = ReadSourceResolver()

    def test_jira_search_latest_from_jira_prefers_live(self):
        decision = self.resolver.resolve(
            tool_name="jira_search_work_items",
            args={},
            execution_context={"latest_user_message": "show latest from jira for my tasks"},
        )
        self.assertFalse(decision.blocking)
        self.assertEqual(decision.source, "live")
        self.assertIn(decision.effective_mode, {"live_only", "live_first"})

    def test_brain_tool_live_only_is_blocking(self):
        decision = self.resolver.resolve(
            tool_name="brain_search_entities",
            args={},
            execution_context={"read_source_mode": "live_only"},
        )
        self.assertTrue(decision.blocking)
        self.assertEqual(decision.issue_code, "read_source_unresolved")

    def test_brain_first_defaults_to_brain_when_supported(self):
        decision = self.resolver.resolve(
            tool_name="jira_search_work_items",
            args={},
            execution_context={"latest_user_message": "list my tasks"},
        )
        self.assertFalse(decision.blocking)
        self.assertEqual(decision.source, "brain")


if __name__ == "__main__":
    unittest.main()

