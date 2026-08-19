import unittest

from app.cadence.state_machine import StateMachine


class CadenceStateMachineClarificationTests(unittest.TestCase):
    def test_clarification_states_are_registered(self):
        self.assertIn("awaiting_status_clarification", StateMachine.VALID_STATES)
        self.assertIn("awaiting_backlog_clarification", StateMachine.VALID_STATES)

    def test_status_clarification_transitions(self):
        self.assertTrue(
            StateMachine.can_transition("awaiting_status", "awaiting_status_clarification")
        )
        self.assertTrue(
            StateMachine.can_transition("awaiting_status_clarification", "awaiting_status")
        )

    def test_backlog_clarification_transitions(self):
        self.assertTrue(
            StateMachine.can_transition("awaiting_backlog_decision", "awaiting_backlog_clarification")
        )
        self.assertTrue(
            StateMachine.can_transition("awaiting_backlog_clarification", "awaiting_backlog_pick")
        )


if __name__ == "__main__":
    unittest.main()

