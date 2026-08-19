import unittest

from app.cortex.proposals.models import ToolProposal


class CortexProposalScopeDefaultsTests(unittest.TestCase):
    def test_non_actionable_defaults_to_in_scope_info_when_scope_missing(self):
        proposal = ToolProposal(
            is_actionable=False,
            confidence=0.4,
            summary="general info request",
            needs_confirmation=False,
            proposed_tool_calls=[],
        )
        self.assertEqual(proposal.scope_classification, "in_scope_info")

    def test_actionable_defaults_to_in_scope_action_when_scope_missing(self):
        proposal = ToolProposal(
            is_actionable=True,
            confidence=0.8,
            summary="list tasks",
            needs_confirmation=False,
            proposed_tool_calls=[],
        )
        self.assertEqual(proposal.scope_classification, "in_scope_action")

    def test_invalid_scope_token_is_repaired(self):
        proposal = ToolProposal(
            is_actionable=False,
            confidence=0.5,
            summary="text",
            scope_classification="unsupported_scope",
            needs_confirmation=False,
            proposed_tool_calls=[],
        )
        self.assertEqual(proposal.scope_classification, "in_scope_info")


if __name__ == "__main__":
    unittest.main()
