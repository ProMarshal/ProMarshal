import unittest

from app.cortex.orchestrator import CortexOrchestrator
from app.cortex.proposals.models import ProposedToolCall, ToolProposal
from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec
from app.cortex.tools.registry import CortexToolRegistry


class _ReadTool(CortexTool):
    spec = ToolSpec(
        name="confirm_policy_read_tool",
        description="read tool",
        idempotency=IdempotencyClass.READ_ONLY.value,
        requires_confirmation=False,
    )

    async def execute(self, args):
        return {"ok": True}


class _WriteTool(CortexTool):
    spec = ToolSpec(
        name="confirm_policy_write_tool",
        description="write tool",
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
        requires_confirmation=True,
    )

    async def execute(self, args):
        return {"ok": True}


class _SafeExplicitIdWriteTool(CortexTool):
    spec = ToolSpec(
        name="jira_update_status",
        description="update task status",
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
        requires_confirmation=False,
    )

    async def execute(self, args):
        return {"ok": True}


class CortexOrchestratorConfirmationPolicyTests(unittest.TestCase):
    def _build_orchestrator(self) -> CortexOrchestrator:
        registry = CortexToolRegistry()
        registry.register(_ReadTool())
        registry.register(_WriteTool())
        registry.register(_SafeExplicitIdWriteTool())
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        orchestrator.tool_registry = registry
        return orchestrator

    def test_read_only_proposal_does_not_require_confirmation_by_default(self):
        orchestrator = self._build_orchestrator()
        proposal = ToolProposal(
            is_actionable=True,
            needs_confirmation=True,
            proposed_tool_calls=[
                ProposedToolCall(
                    tool_name="confirm_policy_read_tool",
                    capability="task_read",
                    args={"keyword": "launch checklist"},
                    confidence=0.9,
                )
            ],
        )
        visible = orchestrator.tool_registry.visible_tool_descriptors(
            role=None,
            connected_integrations=[],
        )
        self.assertFalse(
            orchestrator._proposal_needs_confirmation(
                proposal=proposal,
                visible_tool_descriptors=visible,
            )
        )

    def test_write_proposal_requires_confirmation(self):
        orchestrator = self._build_orchestrator()
        proposal = ToolProposal(
            is_actionable=True,
            needs_confirmation=False,
            proposed_tool_calls=[
                ProposedToolCall(
                    tool_name="confirm_policy_write_tool",
                    capability="workitem_create",
                    args={"title": "launch checklist task"},
                    confidence=0.9,
                )
            ],
        )
        visible = orchestrator.tool_registry.visible_tool_descriptors(
            role=None,
            connected_integrations=[],
        )
        self.assertTrue(
            orchestrator._proposal_needs_confirmation(
                proposal=proposal,
                visible_tool_descriptors=visible,
            )
        )

    def test_unknown_tool_defaults_to_confirmation(self):
        orchestrator = self._build_orchestrator()
        proposal = ToolProposal(
            is_actionable=True,
            needs_confirmation=False,
            proposed_tool_calls=[
                ProposedToolCall(
                    tool_name="unknown_future_tool",
                    capability="unknown_capability",
                    args={"input": "value"},
                    confidence=0.6,
                )
            ],
        )
        visible = orchestrator.tool_registry.visible_tool_descriptors(
            role=None,
            connected_integrations=[],
        )
        self.assertTrue(
            orchestrator._proposal_needs_confirmation(
                proposal=proposal,
                visible_tool_descriptors=visible,
            )
        )

    def test_empty_tool_calls_do_not_require_confirmation(self):
        orchestrator = self._build_orchestrator()
        proposal = ToolProposal(
            is_actionable=False,
            needs_confirmation=True,
            proposed_tool_calls=[],
        )
        visible = orchestrator.tool_registry.visible_tool_descriptors(
            role=None,
            connected_integrations=[],
        )
        self.assertFalse(
            orchestrator._proposal_needs_confirmation(
                proposal=proposal,
                visible_tool_descriptors=visible,
            )
        )

    def test_safe_explicit_id_write_can_skip_confirmation(self):
        orchestrator = self._build_orchestrator()
        proposal = ToolProposal(
            is_actionable=True,
            needs_confirmation=False,
            proposed_tool_calls=[
                ProposedToolCall(
                    tool_name="jira_update_status",
                    capability="workitem_update_status",
                    args={"external_id": "PROJ-12", "status": "done"},
                    confidence=0.9,
                )
            ],
        )
        visible = orchestrator.tool_registry.visible_tool_descriptors(
            role=None,
            connected_integrations=[],
        )
        self.assertFalse(
            orchestrator._proposal_needs_confirmation(
                proposal=proposal,
                visible_tool_descriptors=visible,
            )
        )

    def test_safe_write_without_external_id_still_requires_confirmation(self):
        orchestrator = self._build_orchestrator()
        proposal = ToolProposal(
            is_actionable=True,
            needs_confirmation=False,
            proposed_tool_calls=[
                ProposedToolCall(
                    tool_name="jira_update_status",
                    capability="workitem_update_status",
                    args={"status": "done"},
                    confidence=0.9,
                )
            ],
        )
        visible = orchestrator.tool_registry.visible_tool_descriptors(
            role=None,
            connected_integrations=[],
        )
        self.assertTrue(
            orchestrator._proposal_needs_confirmation(
                proposal=proposal,
                visible_tool_descriptors=visible,
            )
        )

    def test_write_target_grounding_requires_user_visible_identifier(self):
        orchestrator = self._build_orchestrator()
        self.assertFalse(
            orchestrator._is_write_target_grounded_in_user_message(
                tool_name="jira_update_status",
                args={"external_id": "PRM-7", "status": "done"},
                user_message="assign both to prabhu",
            )
        )

    def test_write_target_grounding_passes_when_identifier_is_in_user_text(self):
        orchestrator = self._build_orchestrator()
        self.assertTrue(
            orchestrator._is_write_target_grounded_in_user_message(
                tool_name="jira_update_status",
                args={"external_id": "PRM-7", "status": "done"},
                user_message="move PRM-7 to done",
            )
        )


if __name__ == "__main__":
    unittest.main()
