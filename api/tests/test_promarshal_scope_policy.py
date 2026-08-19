import unittest

from app.promarshal.scope_policy import (
    build_scope_refusal_response,
    classify_scope_for_message,
)


class ProMarshalScopePolicyTests(unittest.TestCase):
    def test_greeting_classification(self):
        decision = classify_scope_for_message("hi there")
        self.assertEqual(decision.classification, "greeting")

    def test_in_scope_task_query(self):
        decision = classify_scope_for_message(
            "can you check if i have any task related to launch checklist"
        )
        self.assertEqual(decision.classification, "in_scope_action")

    def test_short_followup_is_not_blocked(self):
        decision = classify_scope_for_message("yes")
        self.assertEqual(decision.classification, "in_scope_action")

    def test_out_of_scope_general_question(self):
        decision = classify_scope_for_message("what is the weather in san francisco today?")
        self.assertEqual(decision.classification, "out_of_scope")

    def test_pm_definition_query_is_out_of_scope(self):
        decision = classify_scope_for_message("what is project management")
        self.assertEqual(decision.classification, "out_of_scope")
        self.assertEqual(decision.reason_code, "pm_definition_query")

    def test_promarshal_capability_query_is_in_scope_info(self):
        decision = classify_scope_for_message("what can ProMarshal help with?")
        self.assertEqual(decision.classification, "in_scope_info")
        self.assertEqual(decision.reason_code, "capability_query")

    def test_pm_operational_status_question_remains_in_scope(self):
        decision = classify_scope_for_message("what is the status of SCRUM-10?")
        self.assertEqual(decision.classification, "in_scope_action")

    def test_pm_workload_distribution_query_is_in_scope(self):
        decision = classify_scope_for_message("how is the workload distributed between the team")
        self.assertEqual(decision.classification, "in_scope_action")
        self.assertEqual(decision.reason_code, "pm_analytics_query")

    def test_pm_capacity_query_is_in_scope(self):
        decision = classify_scope_for_message("show team capacity and allocation for this sprint")
        self.assertEqual(decision.classification, "in_scope_action")

    def test_pm_burnout_risk_query_is_in_scope(self):
        decision = classify_scope_for_message("is there a burnout risk in team?")
        self.assertEqual(decision.classification, "in_scope_action")
        self.assertEqual(decision.reason_code, "pm_analytics_query")

    def test_pm_action_item_burden_query_is_in_scope(self):
        decision = classify_scope_for_message("show action item workload by owner")
        self.assertEqual(decision.classification, "in_scope_action")
        self.assertEqual(decision.reason_code, "pm_analytics_query")

    def test_non_pm_workload_query_stays_out_of_scope(self):
        decision = classify_scope_for_message("how to manage study workload")
        self.assertEqual(decision.classification, "out_of_scope")

    def test_refusal_mentions_supported_scope(self):
        text = build_scope_refusal_response().lower()
        self.assertIn("project operations", text)
        self.assertIn("tasks", text)


if __name__ == "__main__":
    unittest.main()
