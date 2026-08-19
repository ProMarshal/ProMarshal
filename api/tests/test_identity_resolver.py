import unittest

from app.core.identity_resolver import (
    build_identity_followup_message,
    resolve_project_member_identity,
)


class IdentityResolverTests(unittest.TestCase):
    def test_resolves_by_promarshal_user_id(self):
        project = {
            "members": [
                {"user_id": "u-123", "name": "Prabhu", "status": "active"},
            ]
        }
        result = resolve_project_member_identity(project, "u-123")
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.promarshal_user_id, "u-123")

    def test_resolves_by_name(self):
        project = {
            "members": [
                {"user_id": "u-123", "name": "Prabhu Kumar", "status": "active"},
            ]
        }
        result = resolve_project_member_identity(project, "prabhu")
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.promarshal_user_id, "u-123")

    def test_resolves_by_name_when_hint_has_trailing_punctuation(self):
        project = {
            "members": [
                {"user_id": "u-333", "name": "Sridevi Prabhu", "status": "active"},
            ]
        }
        result = resolve_project_member_identity(project, "sridevi.")
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.promarshal_user_id, "u-333")

    def test_resolves_by_provider_id(self):
        project = {
            "members": [
                {
                    "user_id": "u-456",
                    "name": "Alex",
                    "status": "active",
                    "integration_ids": {"jira_account_id": "jira-acc-99"},
                },
            ]
        }
        result = resolve_project_member_identity(project, "jira-acc-99")
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.promarshal_user_id, "u-456")
        self.assertEqual(result.provider_ids.get("jira_account_id"), "jira-acc-99")

    def test_returns_ambiguous_for_multiple_name_matches(self):
        project = {
            "members": [
                {"user_id": "u-111", "name": "Prabhu S", "status": "active"},
                {"user_id": "u-222", "name": "Prabhu K", "status": "active"},
            ]
        }
        result = resolve_project_member_identity(project, "prabhu")
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(len(result.matched_members), 2)

    def test_resolves_actor_when_hint_contains_assign_to_me_phrase(self):
        project = {
            "members": [
                {"user_id": "u-123", "name": "Prabhu Kumar", "status": "active"},
            ]
        }
        result = resolve_project_member_identity(
            project,
            "create action item to send meeting invite tomorrow at 12pm and assign to me",
            actor_user_id="u-123",
            actor_aliases_enabled=True,
        )
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.promarshal_user_id, "u-123")

    def test_followup_message_includes_suggestions_for_not_found_and_ambiguous(self):
        project = {
            "members": [
                {"user_id": "u-111", "name": "Prabhu S", "status": "active"},
                {"user_id": "u-222", "name": "Prabhu K", "status": "active"},
                {"user_id": "u-333", "name": "Sridevi", "status": "active"},
            ]
        }

        not_found_result = resolve_project_member_identity(project, "zzz")
        not_found_message = build_identity_followup_message(
            not_found_result,
            subject="owner",
            project=project,
        )
        self.assertIn("Couldn't resolve owner", not_found_message)
        self.assertIn("Would you like to assign to", not_found_message)
        self.assertIn("u-111", not_found_message)

        ambiguous_result = resolve_project_member_identity(project, "prabhu")
        ambiguous_message = build_identity_followup_message(
            ambiguous_result,
            subject="owner",
            project=project,
        )
        self.assertIn("Owner `prabhu` is ambiguous", ambiguous_message)
        self.assertIn("Would you like to assign to", ambiguous_message)
        self.assertIn("u-111", ambiguous_message)
        self.assertIn("u-222", ambiguous_message)


if __name__ == "__main__":
    unittest.main()
