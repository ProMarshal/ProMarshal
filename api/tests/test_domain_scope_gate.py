import unittest

from app.domain.scope.gate import classify_project_scope_gate
from app.domain.scope.models import (
    SCOPE_GATE_OUT_OF_SCOPE,
    SCOPE_GATE_PROJECT_RELATED,
)
from app.domain.scope import registry


class DomainScopeGateTests(unittest.TestCase):
    def test_pm_analytics_query_is_project_related(self):
        decision = classify_project_scope_gate("is there burnout risk in team")
        self.assertEqual(decision.classification, SCOPE_GATE_PROJECT_RELATED)
        self.assertEqual(decision.reason_code, registry.REASON_PM_ANALYTICS_QUERY)

    def test_non_project_workload_query_is_out_of_scope(self):
        decision = classify_project_scope_gate("how to manage study workload")
        self.assertEqual(decision.classification, SCOPE_GATE_OUT_OF_SCOPE)
        self.assertEqual(decision.reason_code, registry.REASON_GENERAL_WORKLOAD_QUERY)

    def test_conceptual_pm_definition_is_out_of_scope(self):
        decision = classify_project_scope_gate("what is project management")
        self.assertEqual(decision.classification, SCOPE_GATE_OUT_OF_SCOPE)
        self.assertEqual(decision.reason_code, registry.REASON_PM_DEFINITION_QUERY)

    def test_work_item_key_is_project_related(self):
        decision = classify_project_scope_gate("what is status of PRM-71")
        self.assertEqual(decision.classification, SCOPE_GATE_PROJECT_RELATED)
        self.assertEqual(decision.reason_code, registry.REASON_PROJECT_SIGNAL_DETECTED)
        self.assertIn(registry.SIGNAL_WORK_ITEM_KEY, decision.signals)

    def test_broadcast_operation_request_is_project_related(self):
        decision = classify_project_scope_gate("please check with everyone and collect status updates")
        self.assertEqual(decision.classification, SCOPE_GATE_PROJECT_RELATED)
        self.assertEqual(decision.reason_code, registry.REASON_PROJECT_BROADCAST_OPERATION)
        self.assertIn(registry.SIGNAL_BROADCAST_OPERATION, decision.signals)

    def test_broadcast_request_without_project_semantics_is_not_forced_in_scope(self):
        decision = classify_project_scope_gate("ask everyone which movie we should watch tonight")
        self.assertEqual(decision.classification, SCOPE_GATE_OUT_OF_SCOPE)

    def test_bug_noun_is_project_related_entity_signal(self):
        decision = classify_project_scope_gate("list all bugs")
        self.assertEqual(decision.classification, SCOPE_GATE_PROJECT_RELATED)
        self.assertIn(registry.SIGNAL_PROJECT_ENTITY_HINT, decision.signals)
        self.assertIn(registry.SIGNAL_PROJECT_ACTION_HINT, decision.signals)

    def test_incident_noun_is_project_related_entity_signal(self):
        decision = classify_project_scope_gate("show incidents")
        self.assertEqual(decision.classification, SCOPE_GATE_PROJECT_RELATED)
        self.assertIn(registry.SIGNAL_PROJECT_ENTITY_HINT, decision.signals)
        self.assertIn(registry.SIGNAL_PROJECT_ACTION_HINT, decision.signals)


if __name__ == "__main__":
    unittest.main()
