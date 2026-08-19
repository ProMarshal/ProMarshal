import unittest

from app.cortex.orchestrator import CortexOrchestrator


class CortexOrchestratorScopeEntryTests(unittest.TestCase):
    def test_should_try_implicit_for_conceptual_question(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        should_try = orchestrator._should_try_implicit_tool_proposal(
            user_message="what is project management",
            connected_integrations=["jira"],
            member_candidates=[],
        )
        self.assertTrue(should_try)

    def test_should_not_try_implicit_without_integrations(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        should_try = orchestrator._should_try_implicit_tool_proposal(
            user_message="what is project management",
            connected_integrations=[],
            member_candidates=[],
        )
        self.assertFalse(should_try)

    def test_should_try_implicit_for_write_intent(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        should_try = orchestrator._should_try_implicit_tool_proposal(
            user_message="create a task for release checklist",
            connected_integrations=["jira"],
            member_candidates=[],
        )
        self.assertTrue(should_try)

    def test_capability_query_detection(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        self.assertTrue(orchestrator._looks_like_capability_help_query("what can ProMarshal do"))
        self.assertFalse(orchestrator._looks_like_capability_help_query("what is project management"))


if __name__ == "__main__":
    unittest.main()
