import unittest

from app.cortex.plans.branch_evaluator import evaluate_branch_predicate


class CortexBranchEvaluatorTests(unittest.TestCase):
    def test_if_empty_true(self):
        selected, reason = evaluate_branch_predicate(
            predicate={
                "type": "if_empty",
                "depends_on": "n1",
                "result_key": "items",
            },
            node_results={"n1": {"items": []}},
        )
        self.assertTrue(selected)
        self.assertIsNone(reason)

    def test_if_non_empty_false(self):
        selected, reason = evaluate_branch_predicate(
            predicate={
                "type": "if_non_empty",
                "depends_on": "n1",
                "result_key": "items",
            },
            node_results={"n1": {"items": []}},
        )
        self.assertFalse(selected)
        self.assertIsNone(reason)

    def test_unknown_type_returns_reason(self):
        selected, reason = evaluate_branch_predicate(
            predicate={
                "type": "if_custom",
                "depends_on": "n1",
            },
            node_results={"n1": {"ok": True}},
        )
        self.assertFalse(selected)
        self.assertIsNotNone(reason)
        self.assertIn("predicate_unknown_type", str(reason))


if __name__ == "__main__":
    unittest.main()

