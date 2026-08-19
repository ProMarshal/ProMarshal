import unittest

from app.cortex.routing.capability_resolver import CapabilityResolver
from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec
from app.cortex.tools.registry import CortexToolRegistry
from app.domain.capabilities.materialization import (
    build_capability_context,
    materialize_visible_tool_descriptors,
)


class _JiraCreateTool(CortexTool):
    spec = ToolSpec(
        name="jira_create_tool_materialization",
        description="jira create",
        provider="jira",
        capabilities=["workitem_create"],
        integration_requirements=["jira"],
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        _ = args
        return {"ok": True}


class _LinearCreateTool(CortexTool):
    spec = ToolSpec(
        name="linear_create_tool_materialization",
        description="linear create",
        provider="linear",
        capabilities=["workitem_create"],
        integration_requirements=["linear"],
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        _ = args
        return {"ok": True}


class _BrainReadTool(CortexTool):
    spec = ToolSpec(
        name="brain_read_materialization",
        description="brain read",
        provider="brain",
        capabilities=["workitem_search"],
        idempotency=IdempotencyClass.READ_ONLY.value,
    )

    async def execute(self, args):
        _ = args
        return {"ok": True}


class CapabilityMaterializationTests(unittest.TestCase):
    def test_build_capability_context_detects_provider_ambiguity(self):
        context = build_capability_context(
            project_context={
                "project_id": "proj-1",
                "integrations": {
                    "jira": {"status": "connected"},
                    "linear": {"status": "connected"},
                },
                "active_provider": None,
            }
        )
        self.assertTrue(context.provider_ambiguous)
        self.assertEqual(list(context.connected_pm_providers), ["jira", "linear"])
        self.assertEqual(list(context.materialized_pm_providers), [])

    def test_materialization_filters_pm_tools_but_keeps_brain_when_ambiguous(self):
        context = build_capability_context(
            project_context={
                "project_id": "proj-1",
                "integrations": {
                    "jira": {"status": "connected"},
                    "linear": {"status": "connected"},
                },
                "active_provider": None,
            }
        )
        tools = [
            _JiraCreateTool.spec.to_prompt_descriptor(),
            _LinearCreateTool.spec.to_prompt_descriptor(),
            _BrainReadTool.spec.to_prompt_descriptor(),
        ]
        materialized = materialize_visible_tool_descriptors(
            visible_tools=tools,
            capability_context=context,
        )
        materialized_names = sorted(str(item.get("name") or "") for item in materialized)
        self.assertEqual(materialized_names, ["brain_read_materialization"])


class CapabilityResolverActiveProviderTests(unittest.TestCase):
    def setUp(self):
        registry = CortexToolRegistry()
        registry.register(_JiraCreateTool())
        registry.register(_LinearCreateTool())
        self.resolver = CapabilityResolver(tool_registry=registry)

    def test_resolver_returns_ambiguous_provider_when_multi_connected_and_unset(self):
        resolution = self.resolver.resolve(
            capability="workitem_create",
            role="member",
            connected_integrations=["jira", "linear"],
            project_context={
                "integrations": {
                    "jira": {"status": "connected"},
                    "linear": {"status": "connected"},
                },
                "active_provider": None,
            },
        )
        self.assertFalse(resolution.ok)
        self.assertEqual(resolution.reason_code, "ambiguous_provider")
        self.assertEqual(resolution.candidate_providers, ["jira", "linear"])

    def test_resolver_honors_active_provider_when_connected(self):
        resolution = self.resolver.resolve(
            capability="workitem_create",
            role="member",
            connected_integrations=["jira", "linear"],
            project_context={
                "integrations": {
                    "jira": {"status": "connected"},
                    "linear": {"status": "connected"},
                },
                "active_provider": "jira",
            },
        )
        self.assertTrue(resolution.ok)
        self.assertEqual(resolution.provider, "jira")
        self.assertEqual(resolution.tool_name, "jira_create_tool_materialization")


if __name__ == "__main__":
    unittest.main()
