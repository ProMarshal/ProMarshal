import unittest
from unittest.mock import patch

from app.cadence.action_adapters.registry import (
    CadenceActionAdapterRegistry,
    CadenceAdapterNotFoundError,
    cadence_action_adapter_registry,
)
from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec
from app.domain.capabilities.bundle_models import CapabilityBundle
from app.domain.capabilities.bundle_registry import BundleInstallError, BundleRegistry
from app.domain.capabilities.deterministic.pattern_registry import pattern_registry


class _ToolA(CortexTool):
    spec = ToolSpec(
        name="cadence_bundle_tool_a",
        description="tool a",
        provider="jira",
        capabilities=["workitem_update_status"],
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        _ = args
        return {"ok": True}


class _ToolB(CortexTool):
    spec = ToolSpec(
        name="cadence_bundle_tool_b",
        description="tool b",
        provider="jira",
        capabilities=["workitem_add_comment"],
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        _ = args
        return {"ok": True}


class _AdapterA:
    provider = "jira"
    capability_id = "workitem_update_status"

    async def execute(self, *, db, session, payload, context):
        _ = db, session, context
        return {"ok": True, "task_id": payload.get("task_id")}


class _AdapterB:
    provider = "jira"
    capability_id = "workitem_update_status"

    async def execute(self, *, db, session, payload, context):
        _ = db, session, context
        return {"ok": True, "task_id": payload.get("task_id")}


class _AdapterWorkitemComment:
    provider = "asana"
    capability_id = "workitem_add_comment"

    async def execute(self, *, db, session, payload, context):
        _ = db, session, context
        return {"ok": True, "task_id": payload.get("task_id")}


class _AdapterWorkitemRead:
    provider = "asana"
    capability_id = "workitem_read"

    async def execute(self, *, db, session, payload, context):
        _ = db, session, context
        return {"ok": True, "task_id": payload.get("task_id")}


class _AdapterNestedTask:
    provider = "linear"
    capability_id = "workitem_update_due"

    async def execute(self, *, db, session, payload, context):
        _ = db, session, payload, context
        return {"ok": True, "task": {"id": "LIN-42"}}


class _AdapterEcho:
    provider = "linear"
    capability_id = "workitem_update_start"

    async def execute(self, *, db, session, payload, context):
        _ = db, session, context
        return {"ok": True, "ack": True, "payload": payload}


class CadenceAdapterRegistryDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_resolves_registered_adapter_by_provider_and_capability(self):
        registry = CadenceActionAdapterRegistry()
        registry.register(_AdapterA())
        result = await registry.dispatch(
            provider="jira",
            capability_id="workitem_update_status",
            db=object(),
            session={"session_id": "s-1"},
            payload={"task_id": "SCRUM-1"},
            context={"source": "test"},
        )
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("task_id"), "SCRUM-1")

    async def test_dispatch_raises_not_found_when_adapter_missing(self):
        registry = CadenceActionAdapterRegistry()
        with self.assertRaises(CadenceAdapterNotFoundError):
            await registry.dispatch(
                provider="jira",
                capability_id="workitem_add_comment",
                db=object(),
                session={},
                payload={},
                context={},
            )

    async def test_dispatch_marks_recommendation_dirty_for_mutating_capability(self):
        registry = CadenceActionAdapterRegistry()
        registry.register(_AdapterA())
        with patch(
            "app.cadence.action_adapters.registry.mark_project_task_dirty",
            return_value=True,
        ) as mark_dirty_mock:
            result = await registry.dispatch(
                provider="jira",
                capability_id="workitem_update_status",
                db=object(),
                session={"session_id": "s-1", "project_id": "proj-1111-2222"},
                payload={"task_id": "SCRUM-1"},
                context={"source": "test"},
            )
        self.assertTrue(result.get("ok"))
        mark_dirty_mock.assert_called_once_with("proj-1111-2222", "SCRUM-1")

    async def test_dispatch_marks_recommendation_dirty_for_workitem_comment_capability(self):
        registry = CadenceActionAdapterRegistry()
        registry.register(_AdapterWorkitemComment())
        with patch(
            "app.cadence.action_adapters.registry.mark_project_task_dirty",
            return_value=True,
        ) as mark_dirty_mock:
            result = await registry.dispatch(
                provider="asana",
                capability_id="workitem_add_comment",
                db=object(),
                session={"session_id": "s-1", "project_id": "proj-1111-2222"},
                payload={"task_id": "ASANA-1"},
                context={"source": "test"},
            )
        self.assertTrue(result.get("ok"))
        mark_dirty_mock.assert_called_once_with("proj-1111-2222", "ASANA-1")

    async def test_dispatch_skips_dirty_mark_for_workitem_read_capability(self):
        registry = CadenceActionAdapterRegistry()
        registry.register(_AdapterWorkitemRead())
        with patch(
            "app.cadence.action_adapters.registry.mark_project_task_dirty",
            return_value=True,
        ) as mark_dirty_mock:
            result = await registry.dispatch(
                provider="asana",
                capability_id="workitem_read",
                db=object(),
                session={"session_id": "s-1", "project_id": "proj-1111-2222"},
                payload={"task_id": "ASANA-1"},
                context={"source": "test"},
            )
        self.assertTrue(result.get("ok"))
        mark_dirty_mock.assert_not_called()

    async def test_dispatch_marks_dirty_when_task_key_is_nested_in_result_task(self):
        registry = CadenceActionAdapterRegistry()
        registry.register(_AdapterNestedTask())
        with patch(
            "app.cadence.action_adapters.registry.mark_project_task_dirty",
            return_value=True,
        ) as mark_dirty_mock:
            result = await registry.dispatch(
                provider="linear",
                capability_id="workitem_update_due",
                db=object(),
                session={"session_id": "s-1", "project_id": "proj-1111-2222"},
                payload={},
                context={"source": "test"},
            )
        self.assertTrue(result.get("ok"))
        mark_dirty_mock.assert_called_once_with("proj-1111-2222", "LIN-42")

    async def test_dispatch_marks_dirty_when_task_key_is_nested_in_payload_task(self):
        registry = CadenceActionAdapterRegistry()
        registry.register(_AdapterEcho())
        with patch(
            "app.cadence.action_adapters.registry.mark_project_task_dirty",
            return_value=True,
        ) as mark_dirty_mock:
            result = await registry.dispatch(
                provider="linear",
                capability_id="workitem_update_start",
                db=object(),
                session={"session_id": "s-1", "project_id": "proj-1111-2222"},
                payload={"task": {"id": "LIN-77"}},
                context={"source": "test"},
            )
        self.assertTrue(result.get("ok"))
        mark_dirty_mock.assert_called_once_with("proj-1111-2222", "LIN-77")

    async def test_dispatch_marks_dirty_when_task_key_is_nested_in_payload_workitem(self):
        registry = CadenceActionAdapterRegistry()
        registry.register(_AdapterEcho())
        with patch(
            "app.cadence.action_adapters.registry.mark_project_task_dirty",
            return_value=True,
        ) as mark_dirty_mock:
            result = await registry.dispatch(
                provider="linear",
                capability_id="workitem_update_start",
                db=object(),
                session={"session_id": "s-1", "project_id": "proj-1111-2222"},
                payload={"workitem": {"id": "LIN-88"}},
                context={"source": "test"},
            )
        self.assertTrue(result.get("ok"))
        mark_dirty_mock.assert_called_once_with("proj-1111-2222", "LIN-88")


class CadenceAdapterBundleValidationTests(unittest.TestCase):
    def setUp(self):
        cadence_action_adapter_registry.reset()
        pattern_registry.reset()

    def tearDown(self):
        cadence_action_adapter_registry.reset()
        pattern_registry.reset()

    def test_bundle_registry_rejects_duplicate_cadence_adapter_capability(self):
        registry = BundleRegistry()
        first = CapabilityBundle(
            bundle_id="jira.cadence.adapters.v1",
            provider="jira",
            capability_id="workitem_update_status",
            cortex_tool_factories=[lambda: _ToolA()],
            cadence_adapters=[_AdapterA()],
        )
        second = CapabilityBundle(
            bundle_id="jira.cadence.adapters.v2",
            provider="jira",
            capability_id="workitem_add_comment",
            cortex_tool_factories=[lambda: _ToolB()],
            cadence_adapters=[_AdapterB()],
        )
        registry.install(first)
        with self.assertRaises(BundleInstallError) as ctx:
            registry.install(second)
        conflict_types = [item.conflict_type for item in ctx.exception.diagnostics]
        self.assertIn("duplicate_cadence_adapter", conflict_types)


if __name__ == "__main__":
    unittest.main()
