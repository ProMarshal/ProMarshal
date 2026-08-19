import unittest

from app.action_items.owner_intent import parse_owner_intent


class ActionItemOwnerIntentTests(unittest.TestCase):
    def test_assign_with_action_item_reference_extracts_only_owner(self):
        parsed = parse_owner_intent("assign action-12 to sridevi")
        self.assertTrue(parsed.intent_detected)
        self.assertEqual(parsed.owner_hint, "sridevi")

    def test_assign_with_story_reference_extracts_owner(self):
        parsed = parse_owner_intent("assign story-77 to anna")
        self.assertTrue(parsed.intent_detected)
        self.assertEqual(parsed.owner_hint, "anna")

    def test_assign_with_epic_reference_extracts_owner(self):
        parsed = parse_owner_intent("assign epic-8 to rahul")
        self.assertTrue(parsed.intent_detected)
        self.assertEqual(parsed.owner_hint, "rahul")

    def test_assign_without_target_extracts_owner(self):
        parsed = parse_owner_intent("assign to me")
        self.assertTrue(parsed.intent_detected)
        self.assertEqual(parsed.owner_hint, "me")


if __name__ == "__main__":
    unittest.main()

