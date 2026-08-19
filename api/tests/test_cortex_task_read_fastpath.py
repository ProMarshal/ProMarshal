import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.cortex.orchestrator import CortexOrchestrator


class CortexTaskReadFastpathTests(unittest.TestCase):
    def test_fastpath_intent_resolves_for_list_my_tasks(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        intent = orchestrator._resolve_task_read_fastpath_intent(
            user_message="list my tasks",
            connected_integrations=["jira"],
            launch_tool_descriptors=[{"name": "jira_search_work_items"}],
        )
        self.assertIsNotNone(intent)
        self.assertEqual(str((intent.args or {}).get("assignee_mode") or ""), "me")

    def test_fastpath_intent_skips_when_search_tool_unavailable(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        intent = orchestrator._resolve_task_read_fastpath_intent(
            user_message="list my tasks",
            connected_integrations=["jira"],
            launch_tool_descriptors=[{"name": "jira_analyze_work_items"}],
        )
        self.assertIsNone(intent)

    def test_fastpath_intent_skips_write_intent(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        intent = orchestrator._resolve_task_read_fastpath_intent(
            user_message="move PRM-70 to done",
            connected_integrations=["jira"],
            launch_tool_descriptors=[{"name": "jira_search_work_items"}],
        )
        self.assertIsNone(intent)

    def test_fastpath_intent_skips_analysis_distribution_query(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        intent = orchestrator._resolve_task_read_fastpath_intent(
            user_message="how many tasks are there for each member",
            connected_integrations=["jira"],
            launch_tool_descriptors=[{"name": "jira_search_work_items"}],
        )
        self.assertIsNone(intent)

    def test_fastpath_count_response(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        text = orchestrator._render_task_read_fastpath_response(
            tool_result={"ok": True, "count": 3, "items": []},
            intent_args={"status": "in_progress"},
            is_count_request=True,
        )
        self.assertIn("3", text)
        self.assertIn("matching work items", text.lower())

    def test_fastpath_open_count_response_includes_dynamic_status_split(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        text = orchestrator._render_task_read_fastpath_response(
            tool_result={
                "ok": True,
                "count": 4,
                "items": [
                    {"status": "todo", "status_display": "Backlog"},
                    {"status": "in_progress", "status_display": "QA TEST"},
                    {"status": "in_progress", "status_display": "QA TEST"},
                    {"status": "in_progress", "status_display": "In Progress"},
                ],
            },
            intent_args={"status": "pending"},
            is_count_request=True,
        )
        self.assertIn("You currently have 4 open work items.", text)
        self.assertIn("Status breakdown:", text)
        self.assertIn("- QA TEST: 2", text)
        self.assertIn("- Backlog: 1", text)
        self.assertIn("- In Progress: 1", text)

    def test_fastpath_open_count_response_partial_note_when_truncated(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        text = orchestrator._render_task_read_fastpath_response(
            tool_result={
                "ok": True,
                "count": 20,
                "items": [
                    {"status": "todo", "status_display": "Backlog"},
                    {"status": "in_progress", "status_display": "In Progress"},
                ],
            },
            intent_args={"status": "pending"},
            is_count_request=True,
        )
        self.assertIn("You currently have 20 open work items.", text)
        self.assertIn("Note: breakdown is based on currently returned items.", text)

    def test_fastpath_list_response(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        text = orchestrator._render_task_read_fastpath_response(
            tool_result={
                "ok": True,
                "count": 1,
                "items": [
                    {
                        "external_id": "PRM-1",
                        "title": "Improve API latency",
                        "provider_type": "Story",
                        "status": "in_progress",
                        "status_raw": "In Progress",
                        "status_display": "In Progress",
                        "assignee_name": "Prabhu raj",
                    }
                ],
            },
            intent_args={"status": "pending"},
            is_count_request=False,
        )
        self.assertIn("PRM-1", text)
        self.assertIn("Improve API latency", text)
        self.assertIn("*PRM-1 (Story)*: Improve API latency", text)
        self.assertIn("Status: In Progress | Assignee: Prabhu raj | Due: Not set", text)
        self.assertIn(
            "Would you like me to take any action on these work items, or run another query?",
            text,
        )

    def test_standardized_read_response_from_runtime_trace(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        execute_result = SimpleNamespace(
            raw_response={
                "read_tool_name": "jira_search_work_items",
                "read_items_count": 2,
                "read_items": [
                    {
                        "external_id": "PRM-1",
                        "provider_type": "Story",
                        "title": "Improve API latency",
                        "status": "in_progress",
                        "status_raw": "QA TEST",
                        "status_display": "QA TEST",
                        "assignee_name": "Prabhu raj",
                        "due_date": "2026-04-02",
                    },
                    {
                        "external_id": "PRM-2",
                        "provider_type": "Epic",
                        "title": "Foundation",
                        "status": "todo",
                        "status_raw": "Backlog",
                        "status_display": "Backlog",
                        "assignee_name": "",
                        "due_date": "",
                    },
                ],
            }
        )
        text = orchestrator._prefer_standardized_work_item_read_response(
            response_text="Here are your tasks.",
            execute_result=execute_result,
            user_message="list all tasks",
        )
        self.assertTrue(text.startswith("Here are your tasks."))
        self.assertIn("- *PRM-1 (Story)*: Improve API latency", text)
        self.assertIn("Status: QA TEST | Assignee: Prabhu raj | Due: 2026-04-02", text)
        self.assertIn("- *PRM-2 (Epic)*: Foundation", text)
        self.assertIn("Status: Backlog | Assignee: Unassigned | Due: Not set", text)
        self.assertIn(
            "Status: QA TEST | Assignee: Prabhu raj | Due: 2026-04-02\n\n- *PRM-2 (Epic)*: Foundation",
            text,
        )
        self.assertTrue(
            text.strip().endswith(
                "Would you like me to take any action on these work items, or run another query?"
            )
        )

    def test_standardized_read_response_not_applied_for_non_read_tool(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        execute_result = SimpleNamespace(
            raw_response={
                "read_tool_name": "jira_create_work_item",
                "read_items_count": 1,
                "read_items": [{"external_id": "PRM-1"}],
            }
        )
        text = orchestrator._prefer_standardized_work_item_read_response(
            response_text="Created PRM-1.",
            execute_result=execute_result,
            user_message="create task",
        )
        self.assertEqual(text, "Created PRM-1.")

    def test_clarification_response_is_preserved_verbatim(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        execute_result = SimpleNamespace(
            raw_response={
                "clarification_response_text": (
                    "I found possible parent work items.\n"
                    "Reply `show more` to view additional candidates, or `list all` to view all eligible parents."
                )
            }
        )
        text = orchestrator._prefer_deterministic_clarification_response(
            response_text="Model rephrased fallback",
            execute_result=execute_result,
        )
        self.assertIn("Reply `show more`", text)
        self.assertIn("`list all`", text)
        self.assertNotIn("Model rephrased fallback", text)

    def test_grounded_tool_error_message_sanitizes_internal_tool_names(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        execute_result = SimpleNamespace(
            raw_response={
                "tool_calls_count": 1,
                "tool_success_count": 0,
                "tool_error_count": 1,
                "tool_errors": [
                    {
                        "tool_name": "jira_update_parent_work_item",
                        "user_message": "I couldn't complete `jira_update_parent_work_item` right now.",
                    }
                ],
            }
        )
        text = orchestrator._prefer_grounded_tool_error_message(
            response_text="Fallback response",
            execute_result=execute_result,
        )
        self.assertEqual(text, "I couldn't complete that action right now. Please try again.")

    def test_grounded_tool_error_message_preserves_safe_user_message(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        execute_result = SimpleNamespace(
            raw_response={
                "tool_calls_count": 1,
                "tool_success_count": 0,
                "tool_error_count": 1,
                "tool_errors": [
                    {
                        "tool_name": "jira_update_parent_work_item",
                        "user_message": "I couldn't validate one of the work item keys in Jira right now.",
                    }
                ],
            }
        )
        text = orchestrator._prefer_grounded_tool_error_message(
            response_text="Fallback response",
            execute_result=execute_result,
        )
        self.assertEqual(text, "I couldn't validate one of the work item keys in Jira right now.")

    def test_grounded_tool_error_message_keeps_provider_type_clarification_copy(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        execute_result = SimpleNamespace(
            raw_response={
                "tool_calls_count": 1,
                "tool_success_count": 0,
                "tool_error_count": 1,
                "tool_errors": [
                    {
                        "tool_name": "jira_create_work_item",
                        "error_code": "provider_type_clarification_required",
                        "user_message": "I can't create `Task` in Jira project `PIT`. Reply `yes` to continue with `Bug`.",
                    }
                ],
            }
        )
        text = orchestrator._prefer_grounded_tool_error_message(
            response_text="Fallback response",
            execute_result=execute_result,
        )
        self.assertIn("Reply `yes`", text)
        self.assertIn("`Bug`", text)

    def test_standardized_read_response_uses_default_intro_when_invalid_intro_line(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        execute_result = SimpleNamespace(
            raw_response={
                "read_tool_name": "jira_search_work_items",
                "read_items_count": 1,
                "read_items": [
                    {
                        "external_id": "PRM-1",
                        "provider_type": "Task",
                        "title": "Foundation",
                        "status": "todo",
                        "status_raw": "Backlog",
                        "status_display": "Backlog",
                        "assignee_name": "Prabhu raj",
                        "due_date": "",
                    }
                ],
            }
        )
        text = orchestrator._prefer_standardized_work_item_read_response(
            response_text="- PRM-1 (Task): Foundation",
            execute_result=execute_result,
            user_message="list all tasks",
        )
        self.assertTrue(text.startswith("Here's what I found."))
        self.assertIn("- *PRM-1 (Task)*: Foundation", text)

    def test_standardized_read_response_uses_unknown_when_raw_status_missing(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        execute_result = SimpleNamespace(
            raw_response={
                "read_tool_name": "jira_search_work_items",
                "read_items_count": 1,
                "read_items": [
                    {
                        "external_id": "PRM-3",
                        "provider_type": "Task",
                        "title": "Status mapping check",
                        "status": "todo",
                        "assignee_name": "Prabhu raj",
                        "due_date": "",
                    }
                ],
            }
        )
        text = orchestrator._prefer_standardized_work_item_read_response(
            response_text="Here are your tasks.",
            execute_result=execute_result,
            user_message="list all tasks",
        )
        self.assertIn("Status: Unknown | Assignee: Prabhu raj | Due: Not set", text)

    def test_standardized_read_response_skips_field_followup_queries(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        execute_result = SimpleNamespace(
            raw_response={
                "read_tool_name": "jira_search_work_items",
                "read_items_count": 1,
                "read_items": [
                    {
                        "external_id": "PIT-12",
                        "provider_type": "Bug",
                        "title": "work item creation failing due to missing issue type.",
                        "status": "todo",
                        "status_raw": "Backlog",
                        "status_display": "Backlog",
                        "assignee_name": "Prabhu raj",
                        "due_date": "",
                        "created_by": "Sridevi",
                    }
                ],
            }
        )
        response_text = "PIT-12 was created by Sridevi."
        text = orchestrator._prefer_standardized_work_item_read_response(
            response_text=response_text,
            execute_result=execute_result,
            user_message="who created it",
        )
        self.assertEqual(text, response_text)

    def test_fastpath_truncation_hint_added_when_result_is_partial(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        response = orchestrator._append_task_read_truncation_hint_if_needed(
            response_text="Found 13 matching tasks: ...",
            tool_result={"count": 13, "items": [{} for _ in range(10)]},
        )
        self.assertIn("currently summarized", response)

    def test_fastpath_compose_uses_shared_runtime(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        orchestrator.prompt_builder = SimpleNamespace(build=lambda _request: {"instructions": "compose-system"})
        runtime_result = SimpleNamespace(ok=True, output_text="Composed tasks response", error=None, raw_response={})
        with patch(
            "app.cortex.orchestrator.shared_agent_runtime.run",
            new=AsyncMock(return_value=runtime_result),
        ) as runtime_mock:
            text, meta = asyncio.run(
                orchestrator._compose_fastpath_response_with_shared_runtime(
                    user_message="list my tasks",
                    draft_response_text="Found 2 tasks...",
                    tool_result={"ok": True, "count": 2, "items": [{"external_id": "PRM-1"}]},
                    source="slack_dm",
                    project_id="proj-1",
                    user_id="user-1",
                    session_key="proj-1:user-1:chan",
                    role="member",
                    project_context={},
                    session=None,
                    run_id="run-1",
                    actor_email_resolved=None,
                    actor_jira_account_id=None,
                    self_scope_requested=True,
                    connected_integrations=["jira"],
                    message_context_source="message_text",
                    resolved_member_candidates=[],
                    session_snapshot={},
                )
            )

        self.assertEqual(text, "Composed tasks response")
        self.assertTrue(meta.get("used"))
        self.assertEqual(meta.get("reason"), "ok")
        runtime_mock.assert_awaited_once()
        kwargs = runtime_mock.await_args.kwargs
        self.assertEqual(kwargs.get("tools"), [])
        self.assertIn("Grounded task-read outcome (JSON)", str(kwargs.get("context").user_message))

    def test_fastpath_compose_reports_fallback_when_runtime_returns_draft(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        orchestrator.prompt_builder = SimpleNamespace(build=lambda _request: {"instructions": "compose-system"})
        runtime_result = SimpleNamespace(ok=True, output_text="Found 2 tasks...", error=None, raw_response={})
        with patch(
            "app.cortex.orchestrator.shared_agent_runtime.run",
            new=AsyncMock(return_value=runtime_result),
        ):
            text, meta = asyncio.run(
                orchestrator._compose_fastpath_response_with_shared_runtime(
                    user_message="list my tasks",
                    draft_response_text="Found 2 tasks...",
                    tool_result={"ok": True, "count": 2, "items": []},
                    source="slack_dm",
                    project_id="proj-1",
                    user_id="user-1",
                    session_key="proj-1:user-1:chan",
                    role="member",
                    project_context={},
                    session=None,
                    run_id="run-1",
                    actor_email_resolved=None,
                    actor_jira_account_id=None,
                    self_scope_requested=True,
                    connected_integrations=["jira"],
                    message_context_source="message_text",
                    resolved_member_candidates=[],
                    session_snapshot={},
                )
            )
        self.assertEqual(text, "Found 2 tasks...")
        self.assertFalse(meta.get("used"))
        self.assertEqual(meta.get("reason"), "fallback_same_text")

    def test_fastpath_compose_exception_uses_draft(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        orchestrator.prompt_builder = SimpleNamespace(build=lambda _request: {"instructions": "compose-system"})
        with patch(
            "app.cortex.orchestrator.shared_agent_runtime.run",
            new=AsyncMock(side_effect=RuntimeError("compose failure")),
        ):
            text, meta = asyncio.run(
                orchestrator._compose_fastpath_response_with_shared_runtime(
                    user_message="list my tasks",
                    draft_response_text="Found 2 tasks...",
                    tool_result={"ok": True, "count": 2, "items": []},
                    source="slack_dm",
                    project_id="proj-1",
                    user_id="user-1",
                    session_key="proj-1:user-1:chan",
                    role="member",
                    project_context={},
                    session=None,
                    run_id="run-1",
                    actor_email_resolved=None,
                    actor_jira_account_id=None,
                    self_scope_requested=True,
                    connected_integrations=["jira"],
                    message_context_source="message_text",
                    resolved_member_candidates=[],
                    session_snapshot={},
                )
            )
        self.assertEqual(text, "Found 2 tasks...")
        self.assertFalse(meta.get("used"))
        self.assertEqual(meta.get("reason"), "exception")

    def test_fastpath_compose_timeout_uses_draft(self):
        orchestrator = CortexOrchestrator.__new__(CortexOrchestrator)
        orchestrator.prompt_builder = SimpleNamespace(build=lambda _request: {"instructions": "compose-system"})

        async def _timeout_wait_for(coro, timeout):
            if asyncio.iscoroutine(coro):
                coro.close()
            raise asyncio.TimeoutError()

        with patch(
            "app.cortex.orchestrator.asyncio.wait_for",
            new=AsyncMock(side_effect=_timeout_wait_for),
        ):
            text, meta = asyncio.run(
                orchestrator._compose_fastpath_response_with_shared_runtime(
                    user_message="list my tasks",
                    draft_response_text="Found 2 tasks...",
                    tool_result={"ok": True, "count": 2, "items": []},
                    source="slack_dm",
                    project_id="proj-1",
                    user_id="user-1",
                    session_key="proj-1:user-1:chan",
                    role="member",
                    project_context={},
                    session=None,
                    run_id="run-1",
                    actor_email_resolved=None,
                    actor_jira_account_id=None,
                    self_scope_requested=True,
                    connected_integrations=["jira"],
                    message_context_source="message_text",
                    resolved_member_candidates=[],
                    session_snapshot={},
                )
            )
        self.assertEqual(text, "Found 2 tasks...")
        self.assertFalse(meta.get("used"))
        self.assertEqual(meta.get("reason"), "timeout")

if __name__ == "__main__":
    unittest.main()
