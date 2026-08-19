import ast
import inspect
import unittest

from app.cadence import deterministic_interpreter
from app.cadence.deterministic_interpreter import interpret_cadence_deterministic
from app.domain.capabilities.deterministic.pattern_registry import pattern_registry
from app.domain.capabilities.jira_bundles import get_jira_deterministic_pattern_specs

try:
    from app.cortex.plans.planner import ActionPlanBuilder
except ModuleNotFoundError as exc:  # pragma: no cover - environment dependency gap.
    ActionPlanBuilder = None
    _PLANNER_IMPORT_ERROR = exc
else:
    _PLANNER_IMPORT_ERROR = None


class CadenceDeterministicParityTests(unittest.TestCase):
    def setUp(self):
        # Ensure both Cadence and Cortex deterministic paths use identical registry input.
        pattern_registry.reset()
        pattern_registry.register_many(
            get_jira_deterministic_pattern_specs(),
            provider="jira",
            bundle_id="test.jira.patterns",
        )

    def tearDown(self):
        pattern_registry.reset()

    @unittest.skipIf(ActionPlanBuilder is None, f"planner import unavailable: {_PLANNER_IMPORT_ERROR}")
    def test_cadence_shared_interpretation_matches_cortex_for_same_fixture(self):
        text = (
            "create task launch checklist and assign to alex@example.com "
            "and move it to in progress and add comment started today"
        )
        capability_ids = {
            "workitem_create",
            "workitem_assign",
            "workitem_update_status",
            "workitem_add_comment",
        }
        cadence_result = interpret_cadence_deterministic(
            text=text,
            provider="jira",
            capability_ids=sorted(capability_ids),
        )
        builder = ActionPlanBuilder()
        cortex_result = builder._interpret_deterministic(
            text=text,
            visible_capabilities=capability_ids,
        )

        self.assertEqual(cadence_result.normalized_text, cortex_result.normalized_text)
        self.assertAlmostEqual(cadence_result.confidence, cortex_result.confidence, places=6)
        self.assertEqual(len(cadence_result.matches), len(cortex_result.matches))
        cadence_keys = [(m.action_type, m.capability_id, m.tool_name) for m in cadence_result.matches]
        cortex_keys = [(m.action_type, m.capability_id, m.tool_name) for m in cortex_result.matches]
        self.assertEqual(cadence_keys, cortex_keys)

    @unittest.skipIf(ActionPlanBuilder is None, f"planner import unavailable: {_PLANNER_IMPORT_ERROR}")
    def test_create_subtask_parent_slot_is_shared_between_cadence_and_cortex(self):
        text = "create subtask align migration docs under PRM-42"
        capability_ids = {"workitem_create"}
        cadence_result = interpret_cadence_deterministic(
            text=text,
            provider="jira",
            capability_ids=sorted(capability_ids),
        )
        builder = ActionPlanBuilder()
        cortex_result = builder._interpret_deterministic(
            text=text,
            visible_capabilities=capability_ids,
        )

        self.assertEqual(len(cadence_result.matches), 1)
        self.assertEqual(len(cortex_result.matches), 1)
        cadence_slots = cadence_result.matches[0].extracted_slots or {}
        cortex_slots = cortex_result.matches[0].extracted_slots or {}
        self.assertEqual(str(cadence_slots.get("provider_type") or "").lower(), "subtask")
        self.assertEqual(str(cadence_slots.get("parent_external_id") or "").upper(), "PRM-42")
        self.assertEqual(cadence_slots, cortex_slots)

    def test_deterministic_bridge_module_has_no_cortex_imports(self):
        source = inspect.getsource(deterministic_interpreter)
        tree = ast.parse(source)
        imported_modules = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.append(alias.name)
            if isinstance(node, ast.ImportFrom):
                imported_modules.append(node.module or "")
        self.assertFalse(any(name.startswith("app.cortex") for name in imported_modules))


if __name__ == "__main__":
    unittest.main()
