import unittest

from app.cortex.plans.models import (
    ActionPlan,
    ActionStep,
    PlanSource,
    action_plan_from_dict,
    action_plan_to_dict,
)


class CortexActionPlanModelsTests(unittest.TestCase):
    def test_roundtrip_preserves_capability_and_provider(self):
        plan = ActionPlan(
            plan_id="plan-1",
            source=PlanSource.DETERMINISTIC,
            confidence=0.9,
            rationale="test",
            steps=[
                ActionStep(
                    step_id="s1",
                    action_type="search_task",
                    tool_name="jira_search_work_items",
                    capability="workitem_search",
                    provider="jira",
                    args={"keyword": "release"},
                    summary="Search tasks",
                )
            ],
        )
        payload = action_plan_to_dict(plan)
        hydrated = action_plan_from_dict(payload)
        self.assertIsNotNone(hydrated)
        assert hydrated is not None
        self.assertEqual(hydrated.steps[0].capability, "workitem_search")
        self.assertEqual(hydrated.steps[0].provider, "jira")

    def test_deserialize_accepts_capability_only_step_target(self):
        payload = {
            "plan_id": "plan-1",
            "source": "deterministic",
            "confidence": 0.8,
            "rationale": "test",
            "steps": [
                {
                    "step_id": "s1",
                    "action_type": "search_task",
                    "tool_name": "",
                    "capability": "workitem_search",
                    "args": {"keyword": "release"},
                    "summary": "Search tasks",
                }
            ],
        }
        hydrated = action_plan_from_dict(payload)
        self.assertIsNotNone(hydrated)
        assert hydrated is not None
        self.assertEqual(len(hydrated.steps), 1)
        self.assertEqual(hydrated.steps[0].capability, "workitem_search")
        self.assertEqual(hydrated.steps[0].tool_name, "")


if __name__ == "__main__":
    unittest.main()
