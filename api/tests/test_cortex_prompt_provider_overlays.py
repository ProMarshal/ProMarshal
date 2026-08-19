import unittest

from app.cortex.prompt_builder import CortexPromptBuilder, PromptBuildInput


class CortexPromptProviderOverlayTests(unittest.TestCase):
    def _request(self, *, project_context):
        return PromptBuildInput(
            project_context=project_context,
            session_context={},
            user_message="List my tasks",
            role="member",
            visible_tools=[
                {
                    "name": "jira_search_work_items",
                    "description": "Search Jira tasks",
                    "integration_requirements": ["jira"],
                    "idempotency": "read_only",
                },
                {
                    "name": "linear_search_tasks",
                    "description": "Search Linear tasks",
                    "integration_requirements": ["linear"],
                    "idempotency": "read_only",
                },
            ],
            metadata={},
        )

    def _tools_section(self, payload):
        sections = payload.get("sections") if isinstance(payload.get("sections"), list) else []
        for section in sections:
            if str(section.get("id") or "").strip() == "tools":
                return str(section.get("content") or "")
        return ""

    def test_materialized_active_provider_injects_only_matching_overlay(self):
        builder = CortexPromptBuilder()
        payload = builder.build(
            self._request(
                project_context={
                    "project_id": "proj-1",
                    "project_name": "Alpha",
                    "timezone": "UTC",
                    "integrations": {
                        "jira": {"status": "connected"},
                        "linear": {"status": "connected"},
                    },
                    "active_provider": "jira",
                    "members": [],
                }
            )
        )
        tools_section = self._tools_section(payload)
        self.assertIn("Provider overlay: Jira", tools_section)
        self.assertNotIn("Provider overlay: Linear", tools_section)

    def test_ambiguous_provider_state_does_not_inject_provider_overlay(self):
        builder = CortexPromptBuilder()
        payload = builder.build(
            self._request(
                project_context={
                    "project_id": "proj-2",
                    "project_name": "Beta",
                    "timezone": "UTC",
                    "integrations": {
                        "jira": {"status": "connected"},
                        "linear": {"status": "connected"},
                    },
                    "active_provider": None,
                    "members": [],
                }
            )
        )
        tools_section = self._tools_section(payload)
        self.assertNotIn("Provider overlay: Jira", tools_section)
        self.assertNotIn("Provider overlay: Linear", tools_section)
        self.assertNotIn("Tool policy: Jira family", tools_section)

    def test_overlay_does_not_leak_between_project_builds(self):
        builder = CortexPromptBuilder()
        jira_payload = builder.build(
            self._request(
                project_context={
                    "project_id": "proj-1",
                    "project_name": "Alpha",
                    "timezone": "UTC",
                    "integrations": {"jira": {"status": "connected"}},
                    "active_provider": "jira",
                    "members": [],
                }
            )
        )
        ambiguous_payload = builder.build(
            self._request(
                project_context={
                    "project_id": "proj-2",
                    "project_name": "Beta",
                    "timezone": "UTC",
                    "integrations": {
                        "jira": {"status": "connected"},
                        "linear": {"status": "connected"},
                    },
                    "active_provider": None,
                    "members": [],
                }
            )
        )
        self.assertIn("Provider overlay: Jira", self._tools_section(jira_payload))
        self.assertNotIn("Provider overlay: Jira", self._tools_section(ambiguous_payload))


if __name__ == "__main__":
    unittest.main()

