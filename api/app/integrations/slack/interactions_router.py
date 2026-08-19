"""Slack interactions router - handles modal submissions, button clicks, etc."""

import asyncio
import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from urllib.parse import parse_qs
from app.core.database import get_database
from app.core.queue_backends.dramatiq_backend import enqueue as dramatiq_enqueue
from app.core.queue_runtime import (
    QUEUE_CHAT_COMMANDS,
    QUEUE_CHAT_INTERACTIONS,
)
from app.core.security import encryption_service
from app.integrations.base.webhook_registry import require_webhook_auth
from app.integrations.slack import slack_bot_service
from app.integrations.slack.project_resolver import (
    list_slack_member_projects,
    persist_slack_active_project,
)
from app.integrations.slack.command_handlers import open_project_switch_modal_for_user
from app.cadence.idempotency import is_duplicate_cadence_interaction
from app.tasks.schemas import CreateTaskDTO
from app.tasks.service import TaskService
from app.integrations.slack.formatters import SlackFormatter
from app.core.config import settings
from app.action_items.slack_interaction_handler import (
    ACTION_ITEM_DIGEST_ACTION_IDS,
    handle_action_item_digest_block_action,
)
from app.integrations.slack.assignment_notifications import (
    WORK_ITEM_ASSIGNMENT_OPEN_MODAL_ACTION_ID,
    WORK_ITEM_ASSIGNMENT_UPDATE_CALLBACK_ID,
    handle_work_item_assignment_block_action,
    handle_work_item_assignment_modal_submission,
)
import httpx

router = APIRouter(prefix="/api/integrations/slack", tags=["slack-interactions"])

# Cadence action_ids that should be forwarded to handle_button_click_direct
_CADENCE_ACTIONS = {"open_update_modal", "view_backlog", "skip_backlog"}
_CADENCE_DEDUPED_ACTIONS = {"view_backlog", "skip_backlog"}
_TEAM_POLL_ACTIONS = {"team_poll_owner_skip"}
_PROJECT_CONTEXT_ACTIONS = {"project_switch_open_modal"}
_ASSIGNMENT_NOTIFICATION_ACTIONS = {WORK_ITEM_ASSIGNMENT_OPEN_MODAL_ACTION_ID}


async def process_slack_interaction_payload(
    payload: dict,
    *,
    source_path: str = "/api/integrations/slack/interactions",
) -> JSONResponse:
    """Canonical interaction payload processor shared by canonical and shim endpoints."""
    interaction_type = payload.get("type")

    # ----- Cadence check-in: button clicks -----
    if interaction_type == "block_actions":
        actions = payload.get("actions", [])
        action_id = actions[0].get("action_id", "") if actions else ""
        if action_id in ACTION_ITEM_DIGEST_ACTION_IDS:
            try:
                await handle_action_item_digest_block_action(payload)
            except Exception as exc:
                print(f"Action item digest action handling failed: action_id={action_id} error={exc}")
            return JSONResponse(content={})
        if action_id in _ASSIGNMENT_NOTIFICATION_ACTIONS:
            try:
                await handle_work_item_assignment_block_action(payload)
            except Exception as exc:
                print(
                    f"Work item assignment action handling failed: action_id={action_id} error={exc}"
                )
            return JSONResponse(content={})
        if action_id in _TEAM_POLL_ACTIONS:
            if not bool(getattr(settings, "team_poll_enabled", True)):
                return JSONResponse(content={})
            try:
                from app.team_poll.interaction_handler import handle_owner_skip_button_action
                await handle_owner_skip_button_action(payload)
            except Exception as exc:
                print(f"Team poll action handling failed: action_id={action_id} error={exc}")
            return JSONResponse(content={})
        if action_id in _PROJECT_CONTEXT_ACTIONS:
            team_id = _normalize_text(((payload.get("team") or {}).get("id")))
            user_id = _normalize_text(((payload.get("user") or {}).get("id")))
            channel_id = _normalize_text(((payload.get("channel") or {}).get("id")))
            trigger_id = _normalize_text(payload.get("trigger_id"))
            if team_id and user_id and trigger_id:
                opened = await open_project_switch_modal_for_user(
                    team_id=team_id,
                    slack_user_id=user_id,
                    trigger_id=trigger_id,
                )
                if not opened:
                    slack_client = await _get_workspace_slack_client_for_team(
                        db=get_database(),
                        team_id=team_id,
                    )
                    if slack_client and channel_id:
                        try:
                            await slack_client.chat_postMessage(
                                channel=channel_id,
                                text=(
                                    "I couldn't open the project switcher right now. "
                                    "Run `/promarshal switch` in DM."
                                ),
                            )
                        except Exception:
                            pass
                    return JSONResponse(content={})
            return JSONResponse(content={})
        if action_id in _CADENCE_ACTIONS:
            # Allow repeated modal opens after user closes without submitting.
            # Idempotency still applies to mutation-like cadence actions.
            if action_id in _CADENCE_DEDUPED_ACTIONS and is_duplicate_cadence_interaction(payload):
                print(
                    f"Cadence duplicate block action ignored: action_id={action_id} source={source_path}"
                )
                return JSONResponse(content={})
            from app.cadence.interaction_handler import handle_button_click_direct

            await handle_button_click_direct(payload)
            return JSONResponse(content={})

    # ----- Modal submissions -----
    if interaction_type == "view_submission":
        callback_id = payload.get("view", {}).get("callback_id")

        # Create-task modal (from /promarshal create-task command)
        if callback_id == "create_task_modal":
            return await handle_create_task_modal_submission(payload)
        if callback_id == "project_switch_modal":
            return await handle_project_switch_modal_submission(payload)
        if callback_id == WORK_ITEM_ASSIGNMENT_UPDATE_CALLBACK_ID:
            return await handle_work_item_assignment_modal_submission(payload)

        # Cadence update-task modal
        if callback_id in {"cadence_update_modal", "task_update_modal"}:
            if is_duplicate_cadence_interaction(payload):
                print(
                    f"Cadence duplicate modal submission ignored: callback_id={callback_id} source={source_path}"
                )
                return JSONResponse(content={})
            from app.cadence.interaction_handler import handle_modal_submission_direct

            enqueue_result = dramatiq_enqueue(
                queue_name=QUEUE_CHAT_INTERACTIONS,
                actor_name="handle_modal_submission",
                kwargs={"payload": payload},
            )
            if bool(enqueue_result.get("accepted")):
                print(
                    "Cadence modal submission queued (Dramatiq): "
                    f"{enqueue_result.get('job_id') or 'none'} source={source_path}"
                )
                return JSONResponse(content={})
            print(
                "Failed to queue cadence modal with Dramatiq; processing direct: "
                f"{enqueue_result.get('reason') or 'unknown'}"
            )
            asyncio.create_task(handle_modal_submission_direct(payload))
            return JSONResponse(content={})

    # Unknown interaction
    return JSONResponse(content={})


@router.post("/interactions")
async def handle_slack_interaction(
    raw_body: bytes = Depends(require_webhook_auth("slack")),
):
    """
    Handle Slack interactions (modal submissions, button clicks).

    This endpoint receives:
    - Modal view submissions
    - Button clicks
    - Other interactive elements
    """
    try:
        # Parse payload
        form_data = parse_qs(raw_body.decode("utf-8"))
        payload_str = form_data.get("payload", [""])[0]
        payload = json.loads(payload_str)

        return await process_slack_interaction_payload(payload)

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error handling Slack interaction: {str(e)}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            content={"response_action": "errors", "errors": {"title_block": "An error occurred. Please try again."}},
            status_code=200
        )


async def handle_create_task_modal_submission(payload: dict) -> JSONResponse:
    """
    Handle create task modal submission.

    Queues the task creation to background worker.
    """
    try:
        print("\n=== CREATE TASK MODAL SUBMISSION ===")

        # Extract values from modal
        view = payload.get("view", {})
        values = view.get("state", {}).get("values", {})
        private_metadata = view.get("private_metadata", "")

        print(f"[Create Task] Private metadata: {private_metadata}")

        # Parse metadata (project_id|team_id|created_by_email|channel_id)
        metadata_parts = private_metadata.split("|")
        if len(metadata_parts) < 3:
            print(f"[Create Task] ERROR: Invalid metadata format")
            raise ValueError("Invalid private metadata")

        project_id = metadata_parts[0]
        team_id = metadata_parts[1]
        created_by_email = metadata_parts[2]
        channel_id = metadata_parts[3] if len(metadata_parts) > 3 else None  # Fallback for old modals

        print(f"[Create Task] Project ID: {project_id}")
        print(f"[Create Task] Created by: {created_by_email}")

        # Extract form values
        title = values.get("title_block", {}).get("title_input", {}).get("value", "")
        description = values.get("description_block", {}).get("description_input", {}).get("value") or ""  # Default to empty string if None
        assignee_account_id = values.get("assignee_block", {}).get("assignee_select", {}).get("selected_option", {}).get("value")
        priority = values.get("priority_block", {}).get("priority_select", {}).get("selected_option", {}).get("value", "medium")

        print(f"[Create Task] Title: {title}")
        print(f"[Create Task] Description: {description if description else '(empty)'}")
        print(f"[Create Task] Assignee Account ID: {assignee_account_id}")
        print(f"[Create Task] Priority: {priority}")

        if not title or not assignee_account_id:
            errors = {}
            if not title:
                errors["title_block"] = "Title is required"
            if not assignee_account_id:
                errors["assignee_block"] = "Assignee is required"

            return JSONResponse(
                content={
                    "response_action": "errors",
                    "errors": errors
                },
                status_code=200
            )

        # Get user info
        user = payload.get("user", {})
        user_id = user.get("id")

        # Validate project exists and Jira is connected BEFORE queuing
        print(f"[Create Task] Validating project...")
        db = get_database()
        project = await db.projects.find_one({"project_id": project_id})

        if not project:
            print(f"[Create Task] ERROR: Project {project_id} not found")
            return JSONResponse(
                content={
                    "response_action": "errors",
                    "errors": {"title_block": "Project not found. Please try again."}
                },
                status_code=200
            )

        # Check if Jira is connected
        jira_status = project.get("integrations", {}).get("jira", {}).get("status")
        if jira_status != "connected":
            print(f"[Create Task] ERROR: Jira not connected (status: {jira_status})")
            return JSONResponse(
                content={
                    "response_action": "errors",
                    "errors": {"title_block": "Jira integration not connected. Please connect Jira first."}
                },
                status_code=200
            )

        print(f"[Create Task] Validation passed. Project: {project.get('project_name')}")

        # Queue task creation to background worker (handles concurrency properly)
        enqueue_result = dramatiq_enqueue(
            queue_name=QUEUE_CHAT_COMMANDS,
            actor_name="process_create_task",
            kwargs={
                "project_id": project_id,
                "team_id": team_id,
                "created_by_email": created_by_email,
                "title": title,
                "description": description,
                "assignee_account_id": assignee_account_id,
                "priority": priority,
                "user_id": user_id,
                "channel_id": channel_id,
            },
        )
        if bool(enqueue_result.get("accepted")):
            print(
                f"[Create Task] Queued Dramatiq message {enqueue_result.get('job_id') or 'none'} "
                f"for user {user_id}"
            )
            return JSONResponse(content={"response_action": "clear"}, status_code=200)

        print(
            "[Create Task] Failed to queue with Dramatiq; using async fallback: "
            f"{enqueue_result.get('reason') or 'unknown'}"
        )
        import asyncio
        asyncio.create_task(
            process_create_task_sync(
                project_id=project_id,
                team_id=team_id,
                created_by_email=created_by_email,
                title=title,
                description=description,
                assignee_account_id=assignee_account_id,
                priority=priority,
                user_id=user_id,
                channel_id=channel_id or user_id,
            )
        )

        # Close modal immediately (task creation happens in background)
        return JSONResponse(content={"response_action": "clear"}, status_code=200)

    except Exception as e:
        print(f"Error handling modal submission: {str(e)}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            content={
                "response_action": "errors",
                "errors": {"title_block": "Failed to create task. Please try again."}
            }
        )


async def process_create_task_sync(
    project_id: str,
    team_id: str,
    created_by_email: str,
    title: str,
    description: str,
    assignee_account_id: Optional[str],
    priority: str,
    user_id: str
):
    """
    Process task creation synchronously (when queue is not available).
    """
    try:
        db = get_database()

        # Get project
        project = await db.projects.find_one({"project_id": project_id})
        if not project:
            print(f"Project {project_id} not found")
            return

        # Create task via Task Service
        task_data = CreateTaskDTO(
            title=title,
            description=description,
            assignee_account_id=assignee_account_id,
            priority=priority,
            project_id=project_id
        )

        result = await TaskService.create_task(
            task_data=task_data,
            project=project,
            created_by=created_by_email
        )

        # Send confirmation DM to user
        slack_token = encryption_service.decrypt(
            project["integrations"]["slack"]["access_token"]
        )

        confirmation_text = (
            f"Task created successfully!\n\n"
            f"*[{result.external_id}] {result.title}*\n"
            f"Assigned to: {result.assignee_name or 'Unassigned'}\n"
            f"Priority: {result.priority.capitalize() if result.priority else 'Medium'}\n\n"
            f"<{result.url}|View in Jira>"
        )

        async with httpx.AsyncClient() as client:
            await client.post(
                "https://slack.com/api/chat.postMessage",
                json={
                    "channel": user_id,
                    "text": confirmation_text
                },
                headers={
                    "Authorization": f"Bearer {slack_token}",
                    "Content-Type": "application/json"
                }
            )

        print(f"Task {result.external_id} created successfully")

    except Exception as e:
        print(f"Error processing task creation: {str(e)}")
        import traceback
        traceback.print_exc()


def _normalize_text(value: Optional[str]) -> str:
    return str(value or "").strip()


async def _get_workspace_slack_client_for_team(
    *,
    db,
    team_id: str,
    preferred_project_id: Optional[str] = None,
):
    query: dict = {
        "integrations.slack.team_id": team_id,
        "integrations.slack.status": "connected",
    }
    if preferred_project_id:
        query["project_id"] = preferred_project_id
    project = await db.projects.find_one(query)
    if not project and preferred_project_id:
        project = await db.projects.find_one(
            {
                "integrations.slack.team_id": team_id,
                "integrations.slack.status": "connected",
            }
        )
    encrypted_token = (((project or {}).get("integrations") or {}).get("slack") or {}).get("access_token")
    if not encrypted_token:
        return None
    try:
        return slack_bot_service.get_slack_client(encrypted_token)
    except Exception:
        return None


async def handle_project_switch_modal_submission(payload: dict) -> JSONResponse:
    """Persist active DM project from project switch modal submission."""
    try:
        view = payload.get("view", {}) or {}
        values = (view.get("state") or {}).get("values") or {}
        selected_project_id = (
            (((values.get("project_block") or {}).get("project_select") or {}).get("selected_option") or {}).get("value")
        )
        project_id = _normalize_text(selected_project_id)
        team_id = _normalize_text(((payload.get("team") or {}).get("id")))
        slack_user_id = _normalize_text(((payload.get("user") or {}).get("id")))
        if not project_id or not team_id or not slack_user_id:
            return JSONResponse(
                content={
                    "response_action": "errors",
                    "errors": {"project_block": "Unable to resolve project context. Please retry."},
                },
                status_code=200,
            )

        db = get_database()
        listing = await list_slack_member_projects(
            db=db,
            team_id=team_id,
            slack_user_id=slack_user_id,
        )
        if not listing.ok:
            return JSONResponse(
                content={
                    "response_action": "errors",
                    "errors": {"project_block": "Unable to load your project list right now."},
                },
                status_code=200,
            )
        candidate_projects = [item for item in listing.projects if isinstance(item, dict)]
        selected = next(
            (
                item
                for item in candidate_projects
                if _normalize_text(item.get("project_id")) == project_id
            ),
            None,
        )
        if not selected:
            return JSONResponse(
                content={
                    "response_action": "errors",
                    "errors": {"project_block": "That project is no longer available in your membership list."},
                },
                status_code=200,
            )

        selected_name = _normalize_text((selected or {}).get("project_name")) or project_id
        persisted = await persist_slack_active_project(
            db=db,
            team_id=team_id,
            slack_user_id=slack_user_id,
            user_email=listing.user_email,
            project_id=project_id,
            project_name=selected_name,
            updated_by="slack_project_switch_modal_submit",
        )
        if not persisted:
            return JSONResponse(
                content={
                    "response_action": "errors",
                    "errors": {"project_block": "I couldn't persist your selection. Please retry."},
                },
                status_code=200,
            )

        slack_client = await _get_workspace_slack_client_for_team(
            db=db,
            team_id=team_id,
            preferred_project_id=project_id,
        )
        if slack_client:
            try:
                await slack_client.chat_postMessage(
                    channel=slack_user_id,
                    text=f"Active project set to {selected_name} ({project_id}).",
                )
            except Exception:
                pass

        return JSONResponse(content={"response_action": "clear"}, status_code=200)
    except Exception as exc:
        print(f"Error handling project switch modal submission: {exc}")
        return JSONResponse(
            content={
                "response_action": "errors",
                "errors": {"project_block": "An error occurred while switching project. Please try again."},
            },
            status_code=200,
        )
