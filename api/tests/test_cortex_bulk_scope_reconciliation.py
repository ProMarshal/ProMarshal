import unittest
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.cortex.orchestrator import CortexOrchestrator
from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec
from app.cortex.tools.registry import CortexToolRegistry


class _StatusWriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1, max_length=64)
    status: str = Field(min_length=1, max_length=64)
    operation_id: Optional[str] = Field(default=None, min_length=3, max_length=128)


class _CommentWriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1, max_length=64)
    comment: str = Field(min_length=1, max_length=2000)
    operation_id: Optional[str] = Field(default=None, min_length=3, max_length=128)


class _ReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: Optional[str] = Field(default=None, max_length=200)


class _JiraStatusTool(CortexTool):
    input_model = _StatusWriteInput
    spec = ToolSpec(
        name="jira_update_status",
        description="update status",
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        return {"ok": True, "args": dict(args or {})}


class _JiraCommentTool(CortexTool):
    input_model = _CommentWriteInput
    spec = ToolSpec(
        name="jira_add_comment",
        description="add comment",
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        return {"ok": True, "args": dict(args or {})}


class _JiraSearchTool(CortexTool):
    input_model = _ReadInput
    spec = ToolSpec(
        name="jira_search_work_items",
        description="search tasks",
        idempotency=IdempotencyClass.READ_ONLY.value,
    )

    async def execute(self, args):
        return {"ok": True, "args": dict(args or {})}


class CortexBulkScopeReconciliationTests(unittest.TestCase):
    def _build_orchestrator(self) -> CortexOrchestrator:
        registry = CortexToolRegistry()
        registry.register(_JiraStatusTool())
        registry.register(_JiraCommentTool())
        registry.register(_JiraSearchTool())

        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        orchestrator.tool_registry = registry
        return orchestrator

    def _base_write_calls(self):
        return [
            {
                "tool_name": "jira_update_status",
                "args": {"external_id": "SCRUM-27", "status": "done"},
            },
            {
                "tool_name": "jira_update_status",
                "args": {"external_id": "SCRUM-26", "status": "done"},
            },
            {
                "tool_name": "jira_add_comment",
                "args": {
                    "external_id": "SCRUM-27",
                    "comment": "Implemented and tested successfully.",
                },
            },
            {
                "tool_name": "jira_add_comment",
                "args": {
                    "external_id": "SCRUM-26",
                    "comment": "Implemented and tested successfully.",
                },
            },
        ]

    def test_compute_scope_mismatch_detects_missing_targets(self):
        orchestrator = self._build_orchestrator()
        mismatch = orchestrator._compute_scope_mismatch(
            calls=self._base_write_calls(),
            candidate_targets=["SCRUM-27", "SCRUM-26", "SCRUM-23"],
        )

        self.assertIsNotNone(mismatch)
        self.assertEqual(sorted(mismatch.get("selected_targets") or []), ["SCRUM-26", "SCRUM-27"])
        self.assertEqual(sorted(mismatch.get("missing_targets") or []), ["SCRUM-23"])

    def test_compute_scope_mismatch_returns_none_when_fully_covered(self):
        orchestrator = self._build_orchestrator()
        mismatch = orchestrator._compute_scope_mismatch(
            calls=self._base_write_calls(),
            candidate_targets=["SCRUM-27", "SCRUM-26"],
        )
        self.assertIsNone(mismatch)

    def test_expand_calls_with_targets_applies_templates_for_missing_target(self):
        orchestrator = self._build_orchestrator()
        calls = self._base_write_calls()
        templates = orchestrator._build_write_target_templates(calls)

        expanded = orchestrator._expand_calls_with_targets(
            calls=calls,
            templates=templates,
            targets=["SCRUM-23", "SCRUM-26"],
        )

        self.assertEqual(len(expanded), 6)
        status_targets = sorted(
            str(item.get("args", {}).get("external_id") or "")
            for item in expanded
            if str(item.get("tool_name") or "") == "jira_update_status"
        )
        comment_targets = sorted(
            str(item.get("args", {}).get("external_id") or "")
            for item in expanded
            if str(item.get("tool_name") or "") == "jira_add_comment"
        )
        self.assertEqual(status_targets, ["SCRUM-23", "SCRUM-26", "SCRUM-27"])
        self.assertEqual(comment_targets, ["SCRUM-23", "SCRUM-26", "SCRUM-27"])

    def test_prepare_scope_confirmation_include_all_expands_missing_target(self):
        orchestrator = self._build_orchestrator()
        calls = self._base_write_calls()
        templates = orchestrator._build_write_target_templates(calls)
        pending_question = {
            "type": "proposal_scope_confirmation",
            "proposed_tool_calls": calls,
            "write_target_templates": templates,
            "missing_targets": ["SCRUM-23"],
        }

        prepared, guidance = orchestrator._prepare_tool_calls_from_pending_question(
            pending_question=pending_question,
            user_message="include all",
        )

        self.assertIsNone(guidance)
        self.assertIsNotNone(prepared)
        self.assertEqual(len(prepared or []), 6)

    def test_prepare_scope_confirmation_selected_only_keeps_subset(self):
        orchestrator = self._build_orchestrator()
        calls = self._base_write_calls()
        pending_question = {
            "type": "proposal_scope_confirmation",
            "proposed_tool_calls": calls,
            "write_target_templates": [],
            "missing_targets": ["SCRUM-23"],
        }

        prepared, guidance = orchestrator._prepare_tool_calls_from_pending_question(
            pending_question=pending_question,
            user_message="only selected",
        )

        self.assertIsNone(guidance)
        self.assertEqual(prepared, calls)

    def test_parse_scope_confirmation_selected_only_not_misread_as_yes(self):
        orchestrator = self._build_orchestrator()
        decision = orchestrator._parse_scope_confirmation_decision("only selected")
        self.assertEqual(decision, "selected_only")

    def test_all_matches_scope_request_detects_both_short_form(self):
        orchestrator = self._build_orchestrator()
        self.assertTrue(
            orchestrator._is_all_matches_scope_request(
                {"latest_user_message": "assign both to me and move to in progress"}
            )
        )

    def test_all_matches_scope_request_detects_all_of_them(self):
        orchestrator = self._build_orchestrator()
        self.assertTrue(
            orchestrator._is_all_matches_scope_request(
                {"latest_user_message": "assign all of them to me"}
            )
        )

    def test_all_matches_scope_request_detects_explicit_task_quantifier(self):
        orchestrator = self._build_orchestrator()
        self.assertTrue(
            orchestrator._is_all_matches_scope_request(
                {"latest_user_message": "assign to me all the tasks"}
            )
        )

    def test_prepare_scope_confirmation_include_all_needs_explicit_ids_when_unexpandable(self):
        orchestrator = self._build_orchestrator()
        calls = self._base_write_calls()
        pending_question = {
            "type": "proposal_scope_confirmation",
            "proposed_tool_calls": calls,
            "write_target_templates": [],
            "missing_targets": ["SCRUM-23"],
        }

        prepared, guidance = orchestrator._prepare_tool_calls_from_pending_question(
            pending_question=pending_question,
            user_message="include all",
        )

        self.assertIsNone(prepared)
        self.assertIn("explicit target identifiers", str(guidance or "").lower())


if __name__ == "__main__":
    unittest.main()
