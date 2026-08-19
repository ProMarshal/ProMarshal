import unittest

from app.cortex.orchestrator import CortexOrchestrator
from app.cortex.plans.models import (
    ActionPlan,
    ActionPlanExecutionResult,
    ActionStep,
    PlanExecutionStatus,
    PlanSource,
)


def _pending_plan() -> ActionPlan:
    return ActionPlan(
        plan_id="plan-1",
        source=PlanSource.DETERMINISTIC,
        confidence=0.9,
        rationale="test",
        steps=[
            ActionStep(
                step_id="step_1",
                action_type="create_work_item",
                tool_name="jira_create_work_item",
                args={"title": "cadence summary"},
                summary="Create task",
            )
        ],
    )


class CortexOrchestratorPendingPromptTests(unittest.TestCase):
    def test_partial_response_includes_continue_cancel_prompt(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        result = ActionPlanExecutionResult(
            ok=True,
            status=PlanExecutionStatus.PARTIAL,
            completed_action_texts=["Create task"],
            pending_action_texts=["Assign task"],
            pending_plan=_pending_plan(),
        )

        base = orchestrator._format_action_plan_response(execution_result=result)
        text, added = orchestrator._ensure_pending_plan_followup_prompt(
            response_text=base,
            execution_result=result,
        )

        self.assertTrue(added)
        self.assertIn("`continue`", text)
        self.assertIn("`cancel`", text)

    def test_failed_response_with_pending_includes_continue_cancel_prompt(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        result = ActionPlanExecutionResult(
            ok=False,
            status=PlanExecutionStatus.FAILED,
            completed_action_texts=["Create task"],
            pending_action_texts=["Add comment"],
            pending_plan=_pending_plan(),
            error_code="tool_step_failed",
            error_message="Could not add comment",
        )

        base = orchestrator._format_action_plan_response(execution_result=result)
        text, added = orchestrator._ensure_pending_plan_followup_prompt(
            response_text=base,
            execution_result=result,
        )

        self.assertTrue(added)
        self.assertIn("`continue`", text)
        self.assertIn("`cancel`", text)

    def test_completed_response_does_not_include_pending_prompt(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        result = ActionPlanExecutionResult(
            ok=True,
            status=PlanExecutionStatus.COMPLETED,
            completed_action_texts=["Create task"],
            pending_action_texts=[],
            pending_plan=None,
        )

        base = orchestrator._format_action_plan_response(execution_result=result)
        text, added = orchestrator._ensure_pending_plan_followup_prompt(
            response_text=base,
            execution_result=result,
        )

        self.assertFalse(added)
        self.assertNotIn("`continue`", text)
        self.assertNotIn("`cancel`", text)

    def test_pending_prompt_not_duplicated_when_already_present(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        result = ActionPlanExecutionResult(
            ok=True,
            status=PlanExecutionStatus.PARTIAL,
            completed_action_texts=["Create task"],
            pending_action_texts=["Assign task"],
            pending_plan=_pending_plan(),
        )

        base = "Reply `continue` to resume or `cancel` to discard."
        text, added = orchestrator._ensure_pending_plan_followup_prompt(
            response_text=base,
            execution_result=result,
        )

        self.assertFalse(added)
        self.assertEqual(text, base)

    def test_join_action_texts_three_items_has_no_zero_more_suffix(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        text = orchestrator._join_action_texts(["a", "b", "c"])
        self.assertEqual(text, "a; b; c")
        self.assertNotIn("0 more", text)

    def test_join_action_texts_four_items_returns_full_list(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        text = orchestrator._join_action_texts(["a", "b", "c", "d"])
        self.assertEqual(text, "a; b; c; d")
        self.assertNotIn("and 1 more", text)


if __name__ == "__main__":
    unittest.main()
