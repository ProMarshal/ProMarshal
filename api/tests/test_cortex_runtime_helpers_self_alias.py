import unittest

from app.cortex.tools.runtime_helpers import resolve_self_alias_to_actor_email


class CortexRuntimeHelpersSelfAliasTests(unittest.TestCase):
    def test_resolves_self_alias_with_trailing_punctuation(self):
        project = {
            "members": [
                {
                    "status": "active",
                    "email": "owner@example.com",
                    "integration_ids": {"slack_user_id": "U123"},
                }
            ]
        }
        resolved, used_alias = resolve_self_alias_to_actor_email(
            "me;",
            project=project,
            actor_email=None,
            slack_user_id="U123",
        )
        self.assertTrue(used_alias)
        self.assertEqual(resolved, "owner@example.com")

    def test_non_alias_hint_is_normalized_and_preserved(self):
        project = {"members": []}
        resolved, used_alias = resolve_self_alias_to_actor_email(
            "john@example.com,",
            project=project,
            actor_email=None,
            slack_user_id=None,
        )
        self.assertFalse(used_alias)
        self.assertEqual(resolved, "john@example.com")


if __name__ == "__main__":
    unittest.main()
