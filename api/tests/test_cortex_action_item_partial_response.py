from app.agent_runtime.executor import ExecutorResult
from app.cortex.orchestrator import CortexOrchestrator


def test_normalize_partial_action_item_outcome_response_rewrites_contradictory_text():
    orchestrator = CortexOrchestrator()
    execute_result = ExecutorResult(
        ok=True,
        output_text="Action Item Created and assigned.",
        raw_response={
            "tool_calls_count": 2,
            "tool_success_count": 1,
            "tool_error_count": 1,
            "tool_names_called": ["action_item_create", "action_item_update"],
            "committed_actions": ["Created action item ACTION-4"],
            "tool_errors": [
                {
                    "tool_name": "action_item_update",
                    "user_message": "Owner is ambiguous. Please confirm assignee.",
                }
            ],
        },
    )

    text = orchestrator._normalize_partial_action_item_outcome_response(
        response_text=str(execute_result.output_text or ""),
        execute_result=execute_result,
    )

    assert "I completed part of your request." in text
    assert "Completed: Created action item ACTION-4." in text
    assert "Not completed:" in text
    assert "Owner is ambiguous. Please confirm assignee." in text


def test_normalize_partial_action_item_outcome_response_ignores_non_action_item_tools():
    orchestrator = CortexOrchestrator()
    execute_result = ExecutorResult(
        ok=True,
        output_text="Done",
        raw_response={
            "tool_calls_count": 1,
            "tool_success_count": 1,
            "tool_error_count": 1,
            "tool_names_called": ["jira_update_status"],
            "committed_actions": ["Updated status PRM-1"],
            "tool_errors": [{"tool_name": "jira_update_status", "user_message": "failed"}],
        },
    )

    text = orchestrator._normalize_partial_action_item_outcome_response(
        response_text=str(execute_result.output_text or ""),
        execute_result=execute_result,
    )
    assert text == "Done"


def test_normalize_partial_action_item_outcome_response_suppresses_non_blocking_update_target_clarification():
    orchestrator = CortexOrchestrator()
    execute_result = ExecutorResult(
        ok=True,
        output_text="I completed part of your request.",
        raw_response={
            "tool_calls_count": 2,
            "tool_success_count": 1,
            "tool_error_count": 1,
            "tool_names_called": ["action_item_create", "action_item_update"],
            "committed_actions": ["Created action item ACTION-1"],
            "tool_errors": [
                {
                    "tool_name": "action_item_update",
                    "error_code": "target_scope_clarification_required",
                    "user_message": (
                        "I need clarification on which items to update before I continue. "
                        "Please confirm the exact targets or ask me to fetch the current list first."
                    ),
                }
            ],
        },
    )

    text = orchestrator._normalize_partial_action_item_outcome_response(
        response_text=str(execute_result.output_text or ""),
        execute_result=execute_result,
    )
    assert text == "Completed: Created action item ACTION-1."


def test_prefer_deterministic_action_item_create_summary_prefers_committed_action_block():
    orchestrator = CortexOrchestrator()
    deterministic_summary = (
        "Action Item Created:\n"
        "- Title: Send meeting invite for customer tomorrow at 12pm\n"
        "- Action Item ID: ACTION-3\n"
        "- Status: Open\n"
        "- Assigned to: u-owner\n"
        "- Due Date: 2026-03-16 12:00 IST"
    )
    execute_result = ExecutorResult(
        ok=True,
        output_text="Action Item Created: (model paraphrase)",
        raw_response={
            "tool_calls_count": 1,
            "tool_success_count": 1,
            "tool_error_count": 0,
            "tool_names_called": ["action_item_create"],
            "committed_actions": [deterministic_summary],
            "tool_errors": [],
        },
    )
    text = orchestrator._prefer_deterministic_action_item_create_summary(
        response_text=str(execute_result.output_text or ""),
        execute_result=execute_result,
    )
    assert text == deterministic_summary


def test_normalize_partial_jira_create_assign_outcome_response_reuses_create_summary_structure():
    orchestrator = CortexOrchestrator()
    execute_result = ExecutorResult(
        ok=True,
        output_text="The subtask has been created and assignment failed.",
        raw_response={
            "tool_calls_count": 2,
            "tool_success_count": 1,
            "tool_error_count": 1,
            "tool_names_called": ["jira_create_work_item", "jira_assign_work_item"],
            "committed_actions": [
                "Created Jira Subtask PRM-105 under PRM-48. There is no active sprint right now, so it remains in backlog."
            ],
            "tool_errors": [
                {
                    "tool_name": "jira_assign_work_item",
                    "user_message": "I couldn't find 'Sridevi Prabhu' in active project members with Jira mapping or Jira workspace users. Please provide the assignee email.",
                }
            ],
        },
    )

    text = orchestrator._normalize_partial_jira_create_assign_outcome_response(
        response_text=str(execute_result.output_text or ""),
        execute_result=execute_result,
    )

    assert text.startswith("Completed: Created Jira Subtask PRM-105 under PRM-48.")
    assert "Assignment pending:" in text
    assert "Please provide the assignee email." in text


def test_normalize_partial_jira_create_assign_outcome_response_ignores_non_jira_partial():
    orchestrator = CortexOrchestrator()
    execute_result = ExecutorResult(
        ok=True,
        output_text="Done",
        raw_response={
            "tool_calls_count": 2,
            "tool_success_count": 1,
            "tool_error_count": 1,
            "tool_names_called": ["jira_create_work_item", "jira_update_status"],
            "committed_actions": ["Created Jira Task PRM-101"],
            "tool_errors": [{"tool_name": "jira_update_status", "user_message": "failed"}],
        },
    )
    text = orchestrator._normalize_partial_jira_create_assign_outcome_response(
        response_text=str(execute_result.output_text or ""),
        execute_result=execute_result,
    )
    assert text == "Done"


def test_ensure_parent_line_for_jira_create_response_adds_parent_from_committed_summary():
    orchestrator = CortexOrchestrator()
    execute_result = ExecutorResult(
        ok=True,
        output_text=(
            "Subtask Created:\n"
            "- Title: demo subtask workflow\n"
            "- ID: PRM-107\n"
            "- Status: Todo"
        ),
        raw_response={
            "tool_calls_count": 1,
            "tool_success_count": 1,
            "tool_error_count": 0,
            "tool_names_called": ["jira_create_work_item"],
            "committed_actions": ["Created Jira Subtask PRM-107 under PRM-83."],
        },
    )
    text = orchestrator._ensure_parent_line_for_jira_create_response(
        response_text=str(execute_result.output_text or ""),
        execute_result=execute_result,
    )
    assert "- Parent: PRM-83" in text


def test_ensure_parent_line_for_jira_create_response_adds_na_when_parent_missing():
    orchestrator = CortexOrchestrator()
    execute_result = ExecutorResult(
        ok=True,
        output_text=(
            "Task Created:\n"
            "- Title: demo task\n"
            "- ID: PRM-108\n"
            "- Status: Todo"
        ),
        raw_response={
            "tool_calls_count": 1,
            "tool_success_count": 1,
            "tool_error_count": 0,
            "tool_names_called": ["jira_create_work_item"],
            "committed_actions": ["Created Jira Task PRM-108."],
        },
    )
    text = orchestrator._ensure_parent_line_for_jira_create_response(
        response_text=str(execute_result.output_text or ""),
        execute_result=execute_result,
    )
    assert "- Parent: N/A" in text


def test_ensure_parent_line_for_jira_create_response_does_not_duplicate_existing_parent_line():
    orchestrator = CortexOrchestrator()
    execute_result = ExecutorResult(
        ok=True,
        output_text=(
            "Subtask Created:\n"
            "- Title: demo subtask workflow\n"
            "- ID: PRM-107\n"
            "- Parent Task: PRM-83\n"
            "- Status: Todo"
        ),
        raw_response={
            "tool_calls_count": 1,
            "tool_success_count": 1,
            "tool_error_count": 0,
            "tool_names_called": ["jira_create_work_item"],
            "committed_actions": ["Created Jira Subtask PRM-107 under PRM-83."],
        },
    )
    text = orchestrator._ensure_parent_line_for_jira_create_response(
        response_text=str(execute_result.output_text or ""),
        execute_result=execute_result,
    )
    assert text.count("Parent") == 1
