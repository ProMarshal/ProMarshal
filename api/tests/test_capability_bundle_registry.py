import unittest
import re

from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec
from app.cortex.tools.registry import CortexToolRegistry
from app.domain.capabilities.bundle_models import CapabilityBundle
from app.domain.capabilities.bundle_registry import BundleInstallError, BundleRegistry
from app.domain.capabilities.deterministic.models import ActionPatternSpec
from app.domain.capabilities.deterministic.pattern_registry import pattern_registry
from app.cadence.action_adapters.registry import cadence_action_adapter_registry

try:
    from app.domain.capabilities.startup import install_builtin_bundles
except Exception as exc:  # pragma: no cover - local env may not have all deps.
    install_builtin_bundles = None
    _STARTUP_IMPORT_ERROR = exc
else:
    _STARTUP_IMPORT_ERROR = None


class _ToolOne(CortexTool):
    spec = ToolSpec(
        name="bundle_tool_one",
        description="bundle tool one",
        provider="jira",
        capabilities=["workitem_create"],
        requires_confirmation=False,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        _ = args
        return {"ok": True}


class _ToolTwo(CortexTool):
    spec = ToolSpec(
        name="bundle_tool_two",
        description="bundle tool two",
        provider="jira",
        capabilities=["workitem_assign"],
        requires_confirmation=False,
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        _ = args
        return {"ok": True}


class _ToolSameNameProviderA(CortexTool):
    spec = ToolSpec(
        name="duplicate_global_name",
        description="provider a",
        provider="jira",
        capabilities=["workitem_create"],
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        _ = args
        return {"ok": True}


class _ToolSameNameProviderB(CortexTool):
    spec = ToolSpec(
        name="duplicate_global_name",
        description="provider b",
        provider="linear",
        capabilities=["workitem_create"],
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        _ = args
        return {"ok": True}


def _bundle(
    *,
    bundle_id: str,
    provider: str,
    capability_id: str,
    tool_classes: list[type[CortexTool]],
) -> CapabilityBundle:
    return CapabilityBundle(
        bundle_id=bundle_id,
        provider=provider,
        capability_id=capability_id,
        cortex_tool_factories=[lambda cls=cls: cls() for cls in tool_classes],
    )


class BundleRegistryConflictTests(unittest.TestCase):
    def setUp(self):
        cadence_action_adapter_registry.reset()
        pattern_registry.reset()

    def tearDown(self):
        cadence_action_adapter_registry.reset()
        pattern_registry.reset()

    def test_allows_same_capability_id_across_different_providers(self):
        registry = BundleRegistry()
        tools = CortexToolRegistry()
        registry.install(
            _bundle(
                bundle_id="jira.workitem_create",
                provider="jira",
                capability_id="workitem_create",
                tool_classes=[_ToolOne],
            ),
            tool_registry=tools,
        )
        registry.install(
            _bundle(
                bundle_id="linear.workitem_create",
                provider="linear",
                capability_id="workitem_create",
                tool_classes=[_ToolTwo],
            ),
            tool_registry=tools,
        )
        self.assertEqual(sorted(registry.list_bundle_ids()), ["jira.workitem_create", "linear.workitem_create"])

    def test_rejects_duplicate_provider_capability_pair(self):
        registry = BundleRegistry()
        registry.install(
            _bundle(
                bundle_id="jira.workitem_create.primary",
                provider="jira",
                capability_id="workitem_create",
                tool_classes=[_ToolOne],
            )
        )
        with self.assertRaises(BundleInstallError) as ctx:
            registry.install(
                _bundle(
                    bundle_id="jira.workitem_create.duplicate",
                    provider="jira",
                    capability_id="workitem_create",
                    tool_classes=[_ToolTwo],
                )
            )
        conflict_types = [item.conflict_type for item in ctx.exception.diagnostics]
        self.assertIn("duplicate_provider_capability", conflict_types)

    def test_rejects_duplicate_tool_name_globally(self):
        registry = BundleRegistry()
        registry.install(
            _bundle(
                bundle_id="jira.duplicate_name",
                provider="jira",
                capability_id="workitem_create",
                tool_classes=[_ToolSameNameProviderA],
            )
        )
        with self.assertRaises(BundleInstallError) as ctx:
            registry.install(
                _bundle(
                    bundle_id="linear.duplicate_name",
                    provider="linear",
                    capability_id="workitem_create",
                    tool_classes=[_ToolSameNameProviderB],
                )
            )
        conflict_types = [item.conflict_type for item in ctx.exception.diagnostics]
        self.assertIn("duplicate_tool_name", conflict_types)

    def test_rejects_duplicate_planner_action_type_for_same_provider_and_capability(self):
        registry = BundleRegistry()
        first = CapabilityBundle(
            bundle_id="jira.create.v1",
            provider="jira",
            capability_id="workitem_create",
            cortex_tool_factories=[lambda: _ToolOne()],
            planner_patterns=[
                ActionPatternSpec(
                    action_type="create_work_item",
                    capability_id="workitem_create",
                    tool_name="bundle_tool_one",
                    regex=re.compile(r"create task (?P<title>.+)", re.IGNORECASE),
                    field_extractors={"title": "title"},
                    required_fields=["title"],
                )
            ],
        )
        second = CapabilityBundle(
            bundle_id="jira.create.v2",
            provider="jira",
            capability_id="task_create_alt",
            cortex_tool_factories=[lambda: _ToolTwo()],
            planner_patterns=[
                ActionPatternSpec(
                    action_type="create_work_item",
                    capability_id="workitem_create",
                    tool_name="bundle_tool_two",
                    regex=re.compile(r"add task (?P<title>.+)", re.IGNORECASE),
                    field_extractors={"title": "title"},
                    required_fields=["title"],
                )
            ],
        )
        registry.install(first)
        with self.assertRaises(BundleInstallError) as ctx:
            registry.install(second)
        conflict_types = [item.conflict_type for item in ctx.exception.diagnostics]
        self.assertIn("duplicate_planner_action_type", conflict_types)


@unittest.skipIf(
    install_builtin_bundles is None,
    f"startup import unavailable in this environment: {_STARTUP_IMPORT_ERROR}",
)
class BuiltinBundleInstallTests(unittest.TestCase):
    def setUp(self):
        cadence_action_adapter_registry.reset()

    def test_install_builtin_bundles_populates_launch_toolset(self):
        registry = BundleRegistry()
        tool_registry = CortexToolRegistry()
        try:
            install_builtin_bundles(tool_registry=tool_registry, registry=registry)
        except ModuleNotFoundError as exc:  # pragma: no cover - local dependency gaps.
            self.skipTest(f"built-in bundle dependencies unavailable: {exc}")
        names = set(tool_registry.list_names())
        self.assertIn("jira_create_work_item", names)
        self.assertIn("jira_search_work_items", names)
        self.assertIn("brain_search_entities", names)
        self.assertIn("brain_get_entity", names)
        self.assertIn("action_item_create", names)
        self.assertIn("action_item_list", names)
        self.assertIn("team_poll_create", names)
        self.assertIn("team_poll_status", names)
        self.assertIn("team_poll_close", names)
        self.assertIsNotNone(
            cadence_action_adapter_registry.get(provider="jira", capability_id="workitem_update_status")
        )


if __name__ == "__main__":
    unittest.main()
