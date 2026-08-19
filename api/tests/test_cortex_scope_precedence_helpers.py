import unittest

from app.cortex.orchestrator import CortexOrchestrator
from app.domain.scope.models import (
    SCOPE_GATE_GREETING,
    SCOPE_GATE_PROJECT_RELATED,
    SCOPE_GATE_UNCERTAIN,
    ScopeGateDecision,
)
from app.domain.scope import registry


class CortexScopePrecedenceHelperTests(unittest.TestCase):
    def test_strong_match_for_work_item_signal(self):
        decision = ScopeGateDecision(
            classification=SCOPE_GATE_PROJECT_RELATED,
            reason_code=registry.REASON_PROJECT_SIGNAL_DETECTED,
            signals=[registry.SIGNAL_WORK_ITEM_KEY],
        )
        self.assertTrue(CortexOrchestrator._is_strong_project_related_scope_gate_decision(decision))
        self.assertEqual(CortexOrchestrator._scope_stage1_strength(decision), "strong_project_related")

    def test_strong_match_for_pm_analytics_reason(self):
        decision = ScopeGateDecision(
            classification=SCOPE_GATE_PROJECT_RELATED,
            reason_code=registry.REASON_PM_ANALYTICS_QUERY,
            signals=[registry.SIGNAL_ANALYTICS_QUERY],
        )
        self.assertTrue(CortexOrchestrator._is_strong_project_related_scope_gate_decision(decision))
        self.assertEqual(CortexOrchestrator._scope_stage1_strength(decision), "strong_project_related")

    def test_weak_project_related_when_only_capability_signal(self):
        decision = ScopeGateDecision(
            classification=SCOPE_GATE_PROJECT_RELATED,
            reason_code=registry.REASON_PROJECT_SIGNAL_DETECTED,
            signals=[registry.SIGNAL_CAPABILITY_QUERY],
        )
        self.assertFalse(CortexOrchestrator._is_strong_project_related_scope_gate_decision(decision))
        self.assertEqual(CortexOrchestrator._scope_stage1_strength(decision), "weak_project_related")

    def test_greeting_strength_classification(self):
        decision = ScopeGateDecision(
            classification=SCOPE_GATE_GREETING,
            reason_code=registry.REASON_GREETING_ONLY,
            signals=[registry.SIGNAL_GREETING_ONLY],
        )
        self.assertEqual(CortexOrchestrator._scope_stage1_strength(decision), "greeting")

    def test_uncertain_strength_classification(self):
        decision = ScopeGateDecision(
            classification=SCOPE_GATE_UNCERTAIN,
            reason_code=registry.REASON_INSUFFICIENT_PROJECT_SIGNALS,
            signals=[],
        )
        self.assertEqual(CortexOrchestrator._scope_stage1_strength(decision), "uncertain")


if __name__ == "__main__":
    unittest.main()

