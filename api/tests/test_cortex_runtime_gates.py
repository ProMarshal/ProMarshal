import unittest

from app.cortex.plans.runtime_gates import dependency_gate, predicate_gate


class CortexRuntimeGatesTests(unittest.TestCase):
    def test_dependency_gate_skips_when_dependency_skipped(self):
        decision, code, message = dependency_gate(
            node_id="n2",
            depends_on=["n1"],
            node_status_by_id={"n1": "skipped"},
        )
        self.assertEqual(decision, "skip")
        self.assertEqual(code, "dependency_skipped")
        self.assertIn("n1", str(message))

    def test_dependency_gate_fails_when_dependency_missing(self):
        decision, code, _ = dependency_gate(
            node_id="n2",
            depends_on=["n1"],
            node_status_by_id={},
        )
        self.assertEqual(decision, "fail")
        self.assertEqual(code, "invalid_step_dependency")

    def test_predicate_gate_skips_when_predicate_false(self):
        decision, code, _ = predicate_gate(
            node_id="n2",
            predicate={"type": "if_empty", "depends_on": "n1", "result_key": "items"},
            node_results={"n1": {"items": [{"external_id": "ABC-1"}]}},
            node_status_by_id={"n1": "completed"},
        )
        self.assertEqual(decision, "skip")
        self.assertEqual(code, "predicate_not_selected")

    def test_predicate_gate_skips_when_dependency_skipped(self):
        decision, code, _ = predicate_gate(
            node_id="n2",
            predicate={"type": "if_empty", "depends_on": "n1", "result_key": "items"},
            node_results={},
            node_status_by_id={"n1": "skipped"},
        )
        self.assertEqual(decision, "skip")
        self.assertEqual(code, "predicate_dependency_skipped")


if __name__ == "__main__":
    unittest.main()
