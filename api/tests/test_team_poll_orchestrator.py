import unittest

from app.team_poll.orchestrator import (
    classify_owner_free_text_intent,
    classify_owner_free_text_intent_with_confidence,
)


class TeamPollOrchestratorTests(unittest.TestCase):
    def test_classify_owner_free_text_intent_create(self):
        self.assertEqual(
            classify_owner_free_text_intent("ask team if everyone is ready"),
            "create",
        )

    def test_classify_owner_free_text_intent_status(self):
        self.assertEqual(
            classify_owner_free_text_intent("what is the status of the team poll"),
            "status",
        )

    def test_classify_owner_free_text_intent_with_confidence_low_when_weak(self):
        payload = classify_owner_free_text_intent_with_confidence("ask if ready")
        self.assertTrue(payload["intent"] == "none" or payload["confidence"] < 0.7)


if __name__ == "__main__":
    unittest.main()
