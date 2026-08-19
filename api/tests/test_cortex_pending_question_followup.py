import unittest

from app.cortex.orchestrator import CortexOrchestrator


class CortexPendingQuestionFollowupTests(unittest.TestCase):
    def test_inject_target_refs_from_calls_carries_external_id_for_grounding(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        execution_context = {
            "runtime_target_refs": [],
            "session_snapshot_target_refs": [],
        }
        orchestrator._inject_target_refs_from_calls(
            execution_context=execution_context,
            calls=[
                {
                    "tool_name": "jira_add_comment",
                    "args": {"external_id": "PRM-59", "comment": "please attach logs"},
                }
            ],
        )
        self.assertIn("PRM-59", execution_context.get("runtime_target_refs") or [])
        self.assertIn("PRM-59", execution_context.get("session_snapshot_target_refs") or [])

    def test_missing_fields_superseded_for_distinct_new_intent(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        should_supersede = orchestrator._should_supersede_pending_missing_fields(
            pending_question={
                "type": "proposal_missing_fields",
                "missing_fields": ["jira_add_comment.comment"],
                "proposed_tool_calls": [
                    {"tool_name": "jira_add_comment", "args": {"external_id": "PRM-59"}}
                ],
            },
            user_message="list all unassigned tasks",
        )
        self.assertTrue(should_supersede)

    def test_missing_fields_followup_comment_value_not_superseded(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        should_supersede = orchestrator._should_supersede_pending_missing_fields(
            pending_question={
                "type": "proposal_missing_fields",
                "missing_fields": ["jira_add_comment.comment"],
                "proposed_tool_calls": [
                    {"tool_name": "jira_add_comment", "args": {"external_id": "PRM-59"}}
                ],
            },
            user_message="ask sridevi to attach error logs",
        )
        self.assertFalse(should_supersede)


if __name__ == "__main__":
    unittest.main()
