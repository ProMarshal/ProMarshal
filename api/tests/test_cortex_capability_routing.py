import unittest
from unittest.mock import AsyncMock

from pydantic import ValidationError

from app.agent_runtime.executor import ExecutorResult
from app.cortex.orchestrator import CortexOrchestrator
from app.cortex.plans.models import ActionPlan, ActionStep, PlanSource
from app.cortex.proposals.models import ProposedToolCall, ToolProposal
from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec
from app.cortex.tools.registry import CortexToolRegistry


class _JiraSearchTool(CortexTool):
    spec = ToolSpec(
        name="jira_search_cap_tool",
        description="jira search capability tool",
        provider="jira",
        capabilities=["workitem_search"],
        integration_requirements=["jira"],
        idempotency=IdempotencyClass.READ_ONLY.value,
    )

    async def execute(self, args):
        _ = args
        return {"ok": True}


class _BrainSearchTool(CortexTool):
    spec = ToolSpec(
        name="brain_search_cap_tool",
        description="brain search capability tool",
        provider="brain",
        capabilities=["workitem_search"],
        idempotency=IdempotencyClass.READ_ONLY.value,
    )

    async def execute(self, args):
        _ = args
        return {"ok": True}


class CortexCapabilityRoutingTests(unittest.TestCase):
    def _build_orchestrator(self) -> CortexOrchestrator:
        registry = CortexToolRegistry()
        registry.register(_JiraSearchTool())
        registry.register(_BrainSearchTool())
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        orchestrator.tool_registry = registry
        return orchestrator

    def test_proposed_tool_call_accepts_capability_without_tool_name(self):
        call = ProposedToolCall(capability="workitem_search", args={"keyword": "release"})
        self.assertIsNone(call.tool_name)
        self.assertEqual(call.capability, "workitem_search")

    def test_proposed_tool_call_requires_capability(self):
        with self.assertRaises(ValidationError):
            ProposedToolCall(tool_name="jira_search_cap_tool", args={"keyword": "release"})

    def test_registry_capability_lookup_filters_by_provider(self):
        orchestrator = self._build_orchestrator()
        matches = orchestrator.tool_registry.list_by_provider_and_capability(
            provider="jira",
            capability="workitem_search",
            role="member",
            connected_integrations=["jira"],
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].normalized_spec().name, "jira_search_cap_tool")

    def test_resolves_capability_to_connected_jira_provider(self):
        orchestrator = self._build_orchestrator()
        proposal = ToolProposal(
            is_actionable=True,
            proposed_tool_calls=[ProposedToolCall(capability="workitem_search", args={"keyword": "release"})],
        )
        resolved, _resolved_meta, unresolved = orchestrator._resolve_proposal_calls_to_tools(
            proposal=proposal,
            role="member",
            connected_integrations=["jira"],
            project_context={},
        )
        self.assertEqual(len(unresolved), 0)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].tool_name, "jira_search_cap_tool")
        self.assertEqual(resolved[0].provider, "jira")

    def test_honors_provider_hint_when_supported(self):
        orchestrator = self._build_orchestrator()
        proposal = ToolProposal(
            is_actionable=True,
            proposed_tool_calls=[
                ProposedToolCall(capability="workitem_search", provider="brain", args={"keyword": "release"})
            ],
        )
        resolved, _resolved_meta, unresolved = orchestrator._resolve_proposal_calls_to_tools(
            proposal=proposal,
            role="member",
            connected_integrations=["jira"],
            project_context={},
        )
        self.assertEqual(len(unresolved), 0)
        self.assertEqual(resolved[0].tool_name, "brain_search_cap_tool")
        self.assertEqual(resolved[0].provider, "brain")

    def test_returns_unresolved_for_unsupported_capability(self):
        orchestrator = self._build_orchestrator()
        proposal = ToolProposal(
            is_actionable=True,
            proposed_tool_calls=[ProposedToolCall(capability="workitem_assign", args={"external_id": "SCRUM-1"})],
        )
        resolved, _resolved_meta, unresolved = orchestrator._resolve_proposal_calls_to_tools(
            proposal=proposal,
            role="member",
            connected_integrations=["jira"],
            project_context={},
        )
        self.assertEqual(resolved, [])
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0].get("reason_code"), "unsupported_capability")

    def test_required_tool_call_missing_returns_deterministic_message(self):
        orchestrator = self._build_orchestrator()
        message = orchestrator._build_failed_turn_user_message(
            execute_result=ExecutorResult(
                ok=False,
                error="required_tool_call_missing",
                raw_response={"error_code": "required_tool_call_missing"},
            ),
            committed_action_texts=[],
            pending_action_texts=[],
        )
        self.assertEqual(message, "I cannot do that operation with the currently available tools.")

    def test_action_plan_step_resolves_from_capability(self):
        orchestrator = self._build_orchestrator()
        plan = ActionPlan(
            plan_id="plan-1",
            source=PlanSource.DETERMINISTIC,
            confidence=0.8,
            rationale="test",
            steps=[
                ActionStep(
                    step_id="s1",
                    action_type="search_task",
                    tool_name="",
                    capability="workitem_search",
                    provider="jira",
                    args={"keyword": "release"},
                    summary="Search tasks",
                )
            ],
        )
        resolved_plan, unresolved = orchestrator._resolve_action_plan_step_tools(
            plan=plan,
            role="member",
            connected_integrations=["jira"],
            project_context={},
        )
        self.assertEqual(unresolved, [])
        self.assertEqual(resolved_plan.steps[0].tool_name, "jira_search_cap_tool")
        self.assertEqual(resolved_plan.steps[0].capability, "workitem_search")
        self.assertEqual(resolved_plan.steps[0].provider, "jira")

    def test_action_plan_step_unresolved_for_unsupported_capability(self):
        orchestrator = self._build_orchestrator()
        plan = ActionPlan(
            plan_id="plan-1",
            source=PlanSource.DETERMINISTIC,
            confidence=0.8,
            rationale="test",
            steps=[
                ActionStep(
                    step_id="s1",
                    action_type="assign_work_item",
                    tool_name="",
                    capability="workitem_assign",
                    args={"external_id": "SCRUM-1"},
                    summary="Assign task",
                )
            ],
        )
        _resolved_plan, unresolved = orchestrator._resolve_action_plan_step_tools(
            plan=plan,
            role="member",
            connected_integrations=["jira"],
            project_context={},
        )
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0].get("reason_code"), "unsupported_capability")

    def test_action_plan_step_requires_capability(self):
        orchestrator = self._build_orchestrator()
        plan = ActionPlan(
            plan_id="plan-1",
            source=PlanSource.DETERMINISTIC,
            confidence=0.8,
            rationale="test",
            steps=[
                ActionStep(
                    step_id="s1",
                    action_type="search_task",
                    tool_name="jira_search_cap_tool",
                    capability=None,
                    args={"keyword": "release"},
                    summary="Search tasks",
                )
            ],
        )
        _resolved_plan, unresolved = orchestrator._resolve_action_plan_step_tools(
            plan=plan,
            role="member",
            connected_integrations=["jira"],
            project_context={},
        )
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0].get("reason_code"), "missing_capability")

    def test_normalizes_unresolved_payload_schema(self):
        orchestrator = self._build_orchestrator()
        normalized = orchestrator._normalize_capability_unresolved_entries(
            unresolved=[
                {
                    "capability": "workitem_assign",
                    "provider": "jira",
                    "tool_name": "",
                    "reason_code": "unsupported_capability",
                }
            ],
            path="proposal",
        )
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].get("path"), "proposal")
        self.assertEqual(normalized[0].get("capability"), "workitem_assign")
        self.assertEqual(normalized[0].get("provider_hint"), "jira")
        self.assertIsNone(normalized[0].get("tool_name"))
        self.assertEqual(normalized[0].get("reason_code"), "unsupported_capability")

    def test_normalizes_action_plan_step_id_in_unresolved_payload(self):
        orchestrator = self._build_orchestrator()
        normalized = orchestrator._normalize_capability_unresolved_entries(
            unresolved=[
                {
                    "step_id": "s2",
                    "capability": "workitem_assign",
                    "provider_hint": "jira",
                    "tool_name": "",
                    "reason_code": "unsupported_capability",
                }
            ],
            path="action_plan",
        )
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].get("path"), "action_plan")
        self.assertEqual(normalized[0].get("step_id"), "s2")


class CortexParseTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_capability_unresolved_emits_normalized_parse_outcome(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        orchestrator._append_event = AsyncMock()

        unresolved = [
            {
                "path": "proposal",
                "capability": "workitem_assign",
                "reason_code": "missing_capability",
            },
            {
                "path": "proposal",
                "capability": "workitem_assign",
                "reason_code": "unsupported_capability",
            },
        ]
        await orchestrator._emit_capability_unresolved_event(
            session_key="sess-1",
            run_id="run-1",
            unresolved=unresolved,
            resolved=[],
        )

        self.assertEqual(orchestrator._append_event.await_count, 2)
        parse_call = orchestrator._append_event.await_args_list[-1]
        self.assertEqual(parse_call.kwargs.get("event_type"), "parse_outcome")
        payload = parse_call.kwargs.get("payload") or {}
        self.assertEqual(payload.get("event"), "capability_unresolved")
        self.assertEqual(payload.get("parse_status"), "unsupported")
        self.assertTrue(payload.get("requires_clarification"))
        self.assertEqual((payload.get("reason_code_counts") or {}).get("missing_capability"), 1)
        self.assertEqual((payload.get("reason_code_counts") or {}).get("unsupported_capability"), 1)

    def test_reason_code_to_parse_status_priority(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        status = orchestrator._derive_parse_status_for_reason_codes(
            ["unsupported_capability", "ambiguous_target"]
        )
        self.assertEqual(status, "ambiguous")

    def test_reason_code_to_parse_status_low_confidence(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        status = orchestrator._derive_parse_status_for_reason_codes(["low_confidence"])
        self.assertEqual(status, "low_confidence")

    def test_unsupported_operation_response_uses_reason_catalog_context(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        text = orchestrator._build_unsupported_operation_response(
            [
                {"capability": "workitem_assign", "tool_name": "", "reason_code": "unsupported_capability"},
                {"capability": "task_delete", "tool_name": "", "reason_code": "unsupported_capability"},
            ]
        )
        self.assertIn("currently available tools", text)
        self.assertIn("Unsupported operations", text)
        self.assertIn("workitem_assign", text)


if __name__ == "__main__":
    unittest.main()
