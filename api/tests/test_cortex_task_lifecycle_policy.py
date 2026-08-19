import unittest

from app.cortex.domain.tasks.query_intent import (
    DueDateMode,
    SearchStatus,
    STATUS_ALIASES,
    TaskLifecycleVisibility,
    is_task_read_self_scope_request,
    is_task_write_intent,
    parse_task_lifecycle_visibility,
    parse_task_read_intent,
)
from app.agent_runtime.executor import AgentExecutor
from app.cortex.tools.base import CortexTool, ToolSpec
from app.cortex.tools.providers.jira_tools import JiraSearchTasksInput
from app.tasks.service import TaskService


class _DummyTool(CortexTool):
    spec = ToolSpec(
        name="dummy_task_read",
        description="Dummy task read tool for lifecycle policy tests.",
        task_read_scope=True,
        lifecycle_status_arg="status",
        lifecycle_non_closed_value=SearchStatus.PENDING.value,
        lifecycle_closed_value=SearchStatus.DONE.value,
    )

    async def execute(self, args):
        return {"ok": True, "args": args}


class _DummyNonTaskReadTool(CortexTool):
    spec = ToolSpec(
        name="dummy_non_task_read",
        description="Dummy non task-read tool.",
        task_read_scope=False,
    )

    async def execute(self, args):
        return {"ok": True, "args": args}


class _DummyJiraSearchTasksTool(CortexTool):
    input_model = JiraSearchTasksInput
    spec = ToolSpec(
        name="jira_search_work_items",
        description="Dummy Jira search tool for task-read intent policy tests.",
        task_read_scope=True,
        lifecycle_status_arg="status",
        lifecycle_non_closed_value=SearchStatus.PENDING.value,
        lifecycle_closed_value=SearchStatus.DONE.value,
    )

    async def execute(self, args):
        return {"ok": True, "args": args}


class TaskLifecycleIntentTests(unittest.TestCase):
    def test_visibility_defaults_to_non_closed(self):
        visibility = parse_task_lifecycle_visibility("show tasks")
        self.assertEqual(visibility, TaskLifecycleVisibility.NON_CLOSED)

    def test_visibility_closed_when_explicitly_requested(self):
        visibility = parse_task_lifecycle_visibility("show closed tasks")
        self.assertEqual(visibility, TaskLifecycleVisibility.CLOSED_ONLY)

    def test_visibility_non_closed_for_negated_closed_phrase(self):
        visibility = parse_task_lifecycle_visibility("show not completed tasks")
        self.assertEqual(visibility, TaskLifecycleVisibility.NON_CLOSED)

    def test_read_intent_defaults_to_pending_for_generic_list(self):
        intent = parse_task_read_intent("list tasks")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.args.get("status"), SearchStatus.PENDING.value)
        self.assertIsNone(intent.args.get("provider_types"))
        self.assertFalse(intent.status_explicit)
        self.assertEqual(intent.args.get("assignee_mode"), "any")
        self.assertEqual(intent.args.get("include_unassigned"), 1)

    def test_read_intent_all_scope_includes_unassigned(self):
        intent = parse_task_read_intent("list all tasks")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.args.get("assignee_mode"), "any")
        self.assertEqual(intent.args.get("include_unassigned"), 1)
        self.assertIsNone(intent.args.get("provider_types"))

    def test_read_intent_unassigned_scope(self):
        intent = parse_task_read_intent("list unassigned tasks")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.args.get("assignee_mode"), "unassigned")
        self.assertEqual(intent.args.get("include_unassigned"), 1)
        self.assertIsNone(intent.args.get("provider_types"))

    def test_read_intent_user_stories_maps_to_story_provider_type(self):
        intent = parse_task_read_intent("list all user stories")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.args.get("provider_types"), ["Story"])
        self.assertEqual(intent.args.get("include_unassigned"), 1)

    def test_read_intent_story_singular_maps_to_story_provider_type(self):
        intent = parse_task_read_intent("list all story")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.args.get("provider_types"), ["Story"])

    def test_read_intent_tickets_stays_generic_without_type_filter(self):
        intent = parse_task_read_intent("list all tickets")
        self.assertIsNotNone(intent)
        self.assertIsNone(intent.args.get("provider_types"))

    def test_read_intent_requests_stays_generic_without_type_filter(self):
        intent = parse_task_read_intent("list requests")
        self.assertIsNotNone(intent)
        self.assertIsNone(intent.args.get("provider_types"))
        self.assertEqual(intent.args.get("include_unassigned"), 1)

    def test_read_intent_cards_stays_generic_without_type_filter(self):
        intent = parse_task_read_intent("show cards")
        self.assertIsNotNone(intent)
        self.assertIsNone(intent.args.get("provider_types"))
        self.assertEqual(intent.args.get("include_unassigned"), 1)

    def test_read_intent_incidents_maps_to_incident_provider_type(self):
        intent = parse_task_read_intent("list incidents")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.args.get("provider_types"), ["Incident"])
        self.assertEqual(intent.args.get("include_unassigned"), 1)

    def test_read_intent_features_maps_to_feature_provider_type(self):
        intent = parse_task_read_intent("show all features")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.args.get("provider_types"), ["Feature"])
        self.assertEqual(intent.args.get("include_unassigned"), 1)

    def test_read_intent_service_requests_maps_to_service_request_type(self):
        intent = parse_task_read_intent("list service requests")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.args.get("provider_types"), ["Service Request"])

    def test_read_intent_sets_done_when_closed_explicit(self):
        intent = parse_task_read_intent("show completed tasks")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.args.get("status"), SearchStatus.DONE.value)
        self.assertTrue(intent.status_explicit)

    def test_read_intent_detects_missing_due_date_mode(self):
        intent = parse_task_read_intent("how many tickets are there without due date")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.args.get("due_date_mode"), DueDateMode.MISSING.value)
        self.assertEqual(intent.args.get("status"), SearchStatus.PENDING.value)

    def test_open_alias_maps_to_pending(self):
        self.assertEqual(STATUS_ALIASES.get("open"), SearchStatus.PENDING.value)

    def test_write_intent_detects_description_update_for_standard_issue_key(self):
        self.assertTrue(
            is_task_write_intent("update description of SCRUM-25 with latest acceptance criteria")
        )

    def test_write_intent_detects_description_update_for_relaxed_issue_id(self):
        self.assertTrue(
            is_task_write_intent("change description for scrum25 to include test notes")
        )

    def test_read_intent_rejects_description_update_request(self):
        self.assertIsNone(parse_task_read_intent("edit description of scrum25"))

    def test_read_intent_self_scope_handles_embedded_nbsp(self):
        self.assertTrue(is_task_read_self_scope_request("list my\u00A0tasks"))

    def test_read_intent_self_scope_handles_qualified_phrase(self):
        self.assertTrue(is_task_read_self_scope_request("show my in progress tasks"))


class TaskLifecycleExecutorPolicyTests(unittest.TestCase):
    def setUp(self):
        # We only need this private helper; bypass runtime-heavy constructor.
        self.executor = AgentExecutor.__new__(AgentExecutor)

    def test_executor_injects_non_closed_default(self):
        result = self.executor._apply_task_read_lifecycle_defaults(
            tool=_DummyTool(),
            args={},
            execution_context={"latest_user_message": "list tasks"},
        )
        self.assertEqual(result.get("status"), SearchStatus.PENDING.value)

    def test_executor_injects_closed_on_explicit_closed_request(self):
        result = self.executor._apply_task_read_lifecycle_defaults(
            tool=_DummyTool(),
            args={},
            execution_context={"latest_user_message": "show done tasks"},
        )
        self.assertEqual(result.get("status"), SearchStatus.DONE.value)

    def test_executor_preserves_explicit_status(self):
        result = self.executor._apply_task_read_lifecycle_defaults(
            tool=_DummyTool(),
            args={"status": "in_progress"},
            execution_context={"latest_user_message": "list tasks"},
        )
        self.assertEqual(result.get("status"), "in_progress")

    def test_executor_skips_non_task_read_tool(self):
        result = self.executor._apply_task_read_lifecycle_defaults(
            tool=_DummyNonTaskReadTool(),
            args={},
            execution_context={"latest_user_message": "list tasks"},
        )
        self.assertEqual(result, {})

    def test_executor_injects_assignee_mode_me_for_self_intent_on_jira_search(self):
        result = self.executor._apply_task_read_lifecycle_defaults(
            tool=_DummyJiraSearchTasksTool(),
            args={},
            execution_context={"latest_user_message": "list my tasks"},
        )
        self.assertEqual(result.get("assignee_mode"), "me")

    def test_executor_overrides_explicit_assignee_mode_for_self_scope_on_jira_search(self):
        result = self.executor._apply_task_read_lifecycle_defaults(
            tool=_DummyJiraSearchTasksTool(),
            args={"assignee_mode": "specific", "assignee_value": "owner@example.com"},
            execution_context={"latest_user_message": "list my tasks"},
        )
        self.assertEqual(result.get("assignee_mode"), "me")
        self.assertIsNone(result.get("assignee_value"))

    def test_executor_injects_self_scope_for_task_read_tool(self):
        result = self.executor._apply_task_read_lifecycle_defaults(
            tool=_DummyTool(),
            args={},
            execution_context={"latest_user_message": "list my tasks"},
        )
        self.assertEqual(result.get("assignee_mode"), "me")

    def test_executor_overrides_stale_status_when_user_did_not_explicitly_set_status(self):
        result = self.executor._apply_task_read_intent_defaults(
            tool=_DummyJiraSearchTasksTool(),
            args={"status": "todo"},
            execution_context={"latest_user_message": "list all tasks without due date"},
        )
        self.assertEqual(result.get("status"), SearchStatus.PENDING.value)

    def test_executor_sets_closed_only_when_explicitly_requested(self):
        result = self.executor._apply_task_read_intent_defaults(
            tool=_DummyJiraSearchTasksTool(),
            args={"status": "todo"},
            execution_context={"latest_user_message": "show closed tasks"},
        )
        self.assertEqual(result.get("status"), SearchStatus.DONE.value)

    def test_executor_overrides_stale_due_date_and_scope_filters(self):
        result = self.executor._apply_task_read_intent_defaults(
            tool=_DummyJiraSearchTasksTool(),
            args={
                "due_date_mode": "present",
                "include_unassigned": 0,
                "status": "todo",
            },
            execution_context={"latest_user_message": "list all tasks without due date"},
        )
        self.assertEqual(result.get("due_date_mode"), "missing")
        self.assertEqual(result.get("include_unassigned"), 1)
        self.assertEqual(result.get("status"), SearchStatus.PENDING.value)

    def test_executor_overrides_stale_provider_types_from_intent(self):
        result = self.executor._apply_task_read_intent_defaults(
            tool=_DummyJiraSearchTasksTool(),
            args={"provider_types": ["Epic"]},
            execution_context={"latest_user_message": "list all user stories"},
        )
        self.assertEqual(result.get("provider_types"), ["Story"])


class JiraSearchInputTypeNormalizationTests(unittest.TestCase):
    def test_search_input_normalizes_provider_types(self):
        parsed = JiraSearchTasksInput.model_validate({"provider_types": ["stories", "defects", "sub tasks"]})
        self.assertEqual(parsed.provider_types, ["Story", "Bug", "Sub-task"])


class TaskServiceLifecycleTests(unittest.TestCase):
    def test_derive_is_closed_prefers_boolean_flag(self):
        self.assertTrue(TaskService._derive_is_closed({"is_closed": True, "status": "todo"}))
        self.assertFalse(TaskService._derive_is_closed({"is_closed": False, "status": "done"}))

    def test_derive_is_closed_falls_back_to_status(self):
        self.assertTrue(TaskService._derive_is_closed({"status": "done"}))
        self.assertFalse(TaskService._derive_is_closed({"status": "blocked"}))


if __name__ == "__main__":
    unittest.main()
