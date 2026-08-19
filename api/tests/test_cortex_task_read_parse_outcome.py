import unittest

from app.cortex.domain.tasks.query_intent import parse_task_read_status_outcome


class CortexTaskReadParseOutcomeTests(unittest.TestCase):
    def test_in_review_alias_maps_to_in_progress(self):
        outcome = parse_task_read_status_outcome("in-review")
        self.assertEqual(outcome.status, "resolved")
        self.assertEqual(outcome.normalized_payload.get("status"), "in_progress")

    def test_unknown_status_is_invalid(self):
        outcome = parse_task_read_status_outcome("ship_it_now")
        self.assertEqual(outcome.status, "invalid")
        self.assertTrue(outcome.requires_clarification)


if __name__ == "__main__":
    unittest.main()

