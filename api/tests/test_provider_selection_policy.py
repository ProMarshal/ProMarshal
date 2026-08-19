import unittest

from app.domain.capabilities.provider_selection_policy import (
    connected_pm_providers_from_project_context,
    is_pm_provider,
    is_provider_ambiguous,
    resolve_active_provider,
    resolve_active_provider_after_disconnect,
    resolve_materialized_pm_providers,
)


class ProviderSelectionPolicyTests(unittest.TestCase):
    def test_connected_pm_provider_extraction(self):
        providers = connected_pm_providers_from_project_context(
            {
                "integrations": {
                    "jira": {"status": "connected"},
                    "linear": {"status": "connected"},
                    "slack": {"status": "connected"},
                    "github": {"status": "disconnected"},
                }
            }
        )
        self.assertEqual(providers, ["jira", "linear"])

    def test_active_provider_must_be_connected(self):
        active = resolve_active_provider(
            project_context={"active_provider": "linear"},
            connected_pm_providers=["jira"],
        )
        self.assertIsNone(active)

    def test_materialization_and_ambiguity_rules(self):
        self.assertTrue(is_pm_provider("jira"))
        self.assertFalse(is_pm_provider("brain"))
        self.assertEqual(
            resolve_materialized_pm_providers(
                connected_pm_providers=["jira", "linear"],
                active_provider="jira",
            ),
            ("jira",),
        )
        self.assertTrue(
            is_provider_ambiguous(
                connected_pm_providers=["jira", "linear"],
                active_provider=None,
            )
        )

    def test_disconnect_policy_for_active_provider(self):
        self.assertEqual(
            resolve_active_provider_after_disconnect(
                disconnected_provider="jira",
                current_active_provider="jira",
                remaining_connected_pm_providers=["linear"],
            ),
            "linear",
        )
        self.assertIsNone(
            resolve_active_provider_after_disconnect(
                disconnected_provider="jira",
                current_active_provider="jira",
                remaining_connected_pm_providers=["linear", "asana"],
            )
        )
        self.assertEqual(
            resolve_active_provider_after_disconnect(
                disconnected_provider="jira",
                current_active_provider="linear",
                remaining_connected_pm_providers=["linear"],
            ),
            "linear",
        )


if __name__ == "__main__":
    unittest.main()
