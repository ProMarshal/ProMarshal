"""Handle Slack interaction events"""
import asyncio
import contextlib
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from app.core.config import settings
from app.core.database import get_database, get_session_index_collection
from app.core.queue_metrics import get_queue_depth
from app.core.session_lease import (
    acquire_session_lease,
    release_session_lease,
    renew_session_lease,
    reset_active_session_lease,
    set_active_session_lease,
)
from app.core.security import encryption_service
from app.cadence.session_store import (
    SOURCE,
    claim_expiry_notice_send,
    clear_expiry_notice_claim,
    calculate_active_workload_count,
    get_session,
    mark_expiry_notice_sent,
    update_task_status,
    update_task_comment,
    mark_task_updated_in_jira,
    move_to_next_task,
    stamp_session_lease_version,
    _is_completed_for_summary,
    set_backlog_decision_state,
    set_overall_input_state,
    set_backlog_pick_state,
    complete_session,
    remove_backlog_task_at_index,
    add_backlog_picked_task,
    set_backlog_prompt_state,
)
from app.integrations.slack.client import SlackClient
from app.cadence.formatters import (
    format_paid_tier_message,
    format_backlog_nudge,
    format_backlog_pick_list,
    format_session_summary,
    format_update_task_modal,
)
from app.cadence.slack_identity import resolve_slack_user_id_for_member
from app.cadence.llm_service import get_llm_service
from app.cortex.tools.schemas import normalize_external_id
from app.projects.recommendations.recommendation_orchestrator import mark_project_task_dirty
from app.team_poll.post_cadence_reminder import maybe_send_post_cadence_pending_poll_reminder

# Rate limiting constant
JIRA_API_DELAY = 0.5  # 500ms between Jira API calls
logger = logging.getLogger("app.cadence.interaction_handler")


def _cadence_closed_handoff_text() -> str:
    """Unified user-facing handoff when a Cadence session is no longer active."""
    return (
        "This Cadence session is closed, but I can still help. "
        "Tell me what you want to update, and I'll do it."
    )


def _cadence_expiry_notice_text() -> str:
    return (
        "Your cadence session has expired due to no response. "
        "You can still chat and update task status anytime."
    )


async def send_cadence_expiry_notice(
    db,
    *,
    session: Dict[str, Any],
) -> bool:
    """Best-effort expiry closure notice for timed-out sessions."""
    if not isinstance(session, dict):
        return False

    session_id = _normalize_identifier(session.get("session_id"))
    expired_reason = _normalize_identifier(session.get("expired_reason")).lower()
    if not session_id:
        return False
    if expired_reason != "timeout":
        return False
    project_id = _normalize_identifier(session.get("project_id")) or None
    claimed = await claim_expiry_notice_send(
        db,
        session_id,
        project_id=project_id,
    )
    if not claimed:
        print(f"cadence_expiry_notice_skipped_claim_unavailable: session={session_id}")
        return False

    try:
        slack_user_id, slack_client, _ = await _resolve_slack_for_session(db, session)
        if not slack_user_id:
            print(f"cadence_expiry_notice_failed: session={session_id} reason=slack_user_unresolved")
            await clear_expiry_notice_claim(db, session_id, project_id=project_id)
            return False

        await slack_client.send_message(slack_user_id, text=_cadence_expiry_notice_text())
        await mark_expiry_notice_sent(db, session_id, project_id=project_id)
        print(f"cadence_expiry_notice_sent: session={session_id}")
        return True
    except Exception as exc:
        print(f"cadence_expiry_notice_failed: session={session_id} error={exc}")
        await clear_expiry_notice_claim(db, session_id, project_id=project_id)
        return False


def _session_active_state(session: Optional[Dict[str, Any]]) -> str:
    """
    Return one of: active, completed, expired, missing.
    """
    if not session:
        return "missing"

    status = str(session.get("status") or "").strip().lower()
    if status == "completed":
        return "completed"

    expires_at = session.get("expires_at")
    if isinstance(expires_at, datetime) and expires_at <= datetime.utcnow():
        return "expired"

    return "active"


def _normalize_identifier(value: Any) -> str:
    return str(value or "").strip()


def _resolve_updated_by_identity(session: Dict[str, Any]) -> str:
    """
    Resolve human-readable actor identity for Jira comment attribution.

    Cadence sessions are actor-bound, but `session.user_id` is a ProMarshal user id
    that can be opaque in Jira comments. Prefer member email/name for attribution.
    """
    member_email = str(session.get("member_email") or "").strip().lower()
    if member_email:
        return member_email
    member_name = str(session.get("member_name") or "").strip()
    if member_name:
        return member_name
    return str(session.get("user_id") or "Unknown User").strip() or "Unknown User"


def _log_interaction_rejection(
    *,
    reason_code: str,
    session_id: str,
    action_label: str,
    actor_slack_user_id: Optional[str] = None,
    payload_project_id: Optional[str] = None,
    resolved_project_id: Optional[str] = None,
    session_project_id: Optional[str] = None,
) -> None:
    print(
        "Cadence interaction rejected: "
        f"reason_code={reason_code} "
        f"session={session_id} "
        f"action={action_label} "
        f"actor={_normalize_identifier(actor_slack_user_id) or 'unknown'} "
        f"payload_project={_normalize_identifier(payload_project_id) or 'none'} "
        f"resolved_project={_normalize_identifier(resolved_project_id) or 'none'} "
        f"session_project={_normalize_identifier(session_project_id) or 'none'}"
    )


async def _resolve_project_id_from_session_index(session_id: str) -> Optional[str]:
    normalized_session_id = _normalize_identifier(session_id)
    if not normalized_session_id:
        return None
    doc = await get_session_index_collection().find_one(
        {"source": SOURCE, "session_id": normalized_session_id},
        {"project_id": 1},
    )
    project_id = _normalize_identifier((doc or {}).get("project_id"))
    return project_id or None


def _collect_member_slack_ids(member: Dict[str, Any]) -> set[str]:
    identifiers: set[str] = set()
    direct_keys = ("slack_user_id", "slack_id")
    for key in direct_keys:
        value = _normalize_identifier(member.get(key))
        if value:
            identifiers.add(value)

    integration_ids = member.get("integration_ids")
    if isinstance(integration_ids, dict):
        for key in direct_keys:
            value = _normalize_identifier(integration_ids.get(key))
            if value:
                identifiers.add(value)
        slack_obj = integration_ids.get("slack")
        if isinstance(slack_obj, dict):
            for key in ("user_id", "slack_user_id", "id"):
                value = _normalize_identifier(slack_obj.get(key))
                if value:
                    identifiers.add(value)
    return identifiers


async def _is_actor_authorized_for_session(
    db,
    *,
    session: Dict[str, Any],
    actor_slack_user_id: str,
) -> tuple[bool, str]:
    session_project_id = _normalize_identifier(session.get("project_id"))
    session_user_id = _normalize_identifier(session.get("user_id"))
    actor_id = _normalize_identifier(actor_slack_user_id)
    if not session_project_id or not session_user_id:
        return False, "actor_auth_session_fields_missing"
    if not actor_id:
        return False, "actor_missing"

    project = await db.projects.find_one({"project_id": session_project_id})
    if not project:
        return False, "actor_auth_project_not_found"

    members = project.get("members", [])
    owner_member = next(
        (member for member in members if _normalize_identifier(member.get("user_id")) == session_user_id),
        None,
    )
    if not owner_member:
        return False, "actor_auth_session_owner_not_member"

    known_ids = _collect_member_slack_ids(owner_member)
    if actor_id in known_ids:
        return True, "ok"

    slack_integration = ((project.get("integrations") or {}).get("slack") or {})
    encrypted_token = slack_integration.get("access_token")
    if not encrypted_token:
        return False, "actor_auth_slack_token_missing"

    try:
        slack_token = encryption_service.decrypt(encrypted_token)
    except Exception:
        return False, "actor_auth_slack_token_decrypt_failed"

    slack_client = SlackClient(slack_token)
    resolved_owner_slack = await resolve_slack_user_id_for_member(
        db=db,
        project=project,
        member=owner_member,
        slack_client=slack_client,
    )
    resolved_owner_slack = _normalize_identifier(resolved_owner_slack)
    if not resolved_owner_slack:
        return False, "actor_auth_session_owner_slack_unresolved"
    if resolved_owner_slack != actor_id:
        return False, "actor_mismatch"
    return True, "ok"


async def _enforce_interaction_isolation(
    db,
    *,
    session: Dict[str, Any],
    session_id: str,
    action_label: str,
    actor_slack_user_id: Optional[str],
    payload_project_id: Optional[str],
) -> bool:
    strict_binding_enabled = bool(
        getattr(settings, "session_strict_project_binding_enabled", False)
    )
    actor_auth_enabled = bool(
        getattr(settings, "session_actor_authorization_enabled", False)
    )
    if not strict_binding_enabled and not actor_auth_enabled:
        return True

    normalized_session_id = _normalize_identifier(session_id)
    normalized_payload_project_id = _normalize_identifier(payload_project_id)
    normalized_session_project_id = _normalize_identifier(session.get("project_id"))
    resolved_project_id = normalized_payload_project_id
    resolution_source = "payload" if resolved_project_id else ""
    if not resolved_project_id:
        resolved_project_id = await _resolve_project_id_from_session_index(normalized_session_id) or ""
        if resolved_project_id:
            resolution_source = "session_index"
            print(
                "Cadence interaction isolation metric: "
                f"metric=legacy_payload_fallback_hit session={normalized_session_id}"
            )
    if not resolved_project_id:
        resolved_project_id = normalized_session_project_id
        resolution_source = "session_doc"

    print(
        "Cadence interaction isolation metric: "
        f"metric=session_resolution_source source={resolution_source or 'unknown'} "
        f"session={normalized_session_id}"
    )

    if strict_binding_enabled:
        if not normalized_session_project_id or not resolved_project_id:
            _log_interaction_rejection(
                reason_code="project_binding_missing",
                session_id=normalized_session_id,
                action_label=action_label,
                actor_slack_user_id=actor_slack_user_id,
                payload_project_id=normalized_payload_project_id,
                resolved_project_id=resolved_project_id,
                session_project_id=normalized_session_project_id,
            )
            print("Cadence interaction isolation metric: metric=project_mismatch_rejected")
            return False
        if normalized_payload_project_id and normalized_payload_project_id != resolved_project_id:
            _log_interaction_rejection(
                reason_code="project_payload_resolved_mismatch",
                session_id=normalized_session_id,
                action_label=action_label,
                actor_slack_user_id=actor_slack_user_id,
                payload_project_id=normalized_payload_project_id,
                resolved_project_id=resolved_project_id,
                session_project_id=normalized_session_project_id,
            )
            print("Cadence interaction isolation metric: metric=project_mismatch_rejected")
            return False
        if resolved_project_id != normalized_session_project_id:
            _log_interaction_rejection(
                reason_code="project_resolved_session_mismatch",
                session_id=normalized_session_id,
                action_label=action_label,
                actor_slack_user_id=actor_slack_user_id,
                payload_project_id=normalized_payload_project_id,
                resolved_project_id=resolved_project_id,
                session_project_id=normalized_session_project_id,
            )
            print("Cadence interaction isolation metric: metric=project_mismatch_rejected")
            return False

    if actor_auth_enabled:
        actor_id = _normalize_identifier(actor_slack_user_id)
        authorized, reason = await _is_actor_authorized_for_session(
            db,
            session=session,
            actor_slack_user_id=actor_id,
        )
        if not authorized:
            _log_interaction_rejection(
                reason_code=reason,
                session_id=normalized_session_id,
                action_label=action_label,
                actor_slack_user_id=actor_id,
                payload_project_id=normalized_payload_project_id,
                resolved_project_id=resolved_project_id,
                session_project_id=normalized_session_project_id,
            )
            print("Cadence interaction isolation metric: metric=actor_mismatch_rejected")
            return False

    return True


async def _notify_closed_cadence_session(db, session: Optional[Dict[str, Any]], *, action_label: str) -> None:
    """
    Best-effort DM notice for stale modal/button interactions.
    """
    if not session:
        return

    try:
        slack_user_id, slack_client, _ = await _resolve_slack_for_session(db, session)
        if not slack_user_id:
            return
        await slack_client.send_message(
            slack_user_id,
            text=_cadence_closed_handoff_text(),
        )
        print(
            "Cadence stale interaction handoff sent: "
            f"session={session.get('session_id')} action={action_label}"
        )
    except Exception as exc:
        print(f"Cadence stale interaction handoff send failed: {exc}")


async def _get_active_session_for_interaction(
    db,
    *,
    session_id: str,
    action_label: str,
    notify_on_closed: bool = True,
    enforce_isolation: bool = False,
    actor_slack_user_id: Optional[str] = None,
    payload_project_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Return active session for an interaction; optionally notify user when stale/closed.
    """
    session = await get_session(db, session_id)
    state = _session_active_state(session)
    if state == "active":
        if enforce_isolation:
            authorized = await _enforce_interaction_isolation(
                db,
                session=session,
                session_id=session_id,
                action_label=action_label,
                actor_slack_user_id=actor_slack_user_id,
                payload_project_id=payload_project_id,
            )
            if not authorized:
                return None
        return session

    _log_interaction_rejection(
        reason_code=state,
        session_id=_normalize_identifier(session_id),
        action_label=action_label,
        actor_slack_user_id=actor_slack_user_id,
        payload_project_id=payload_project_id,
        session_project_id=_normalize_identifier((session or {}).get("project_id")),
    )
    if notify_on_closed and state in {"completed", "expired"}:
        await _notify_closed_cadence_session(db, session, action_label=action_label)
    return None


async def handle_interaction(payload: Dict[str, Any]):
    """
    Process Slack interaction (button click or modal submission)

    Args:
        payload: Slack interaction payload
    """
    db = get_database()

    interaction_type = payload.get("type")
    trigger_id = payload.get("trigger_id")

    try:
        if interaction_type == "block_actions":
            # Button click - no deduplication needed (opening modal is idempotent)
            await handle_button_click(db, payload, trigger_id)
        elif interaction_type == "view_submission":
            # Modal submission - uses session-based deduplication
            await handle_modal_submission(db, payload, trigger_id)
        else:
            print(f"Unknown interaction type: {interaction_type}")

    except Exception as e:
        print(f"Error handling interaction: {str(e)}")
        import traceback
        traceback.print_exc()


async def handle_button_click(db, payload: Dict[str, Any], trigger_id: str):
    """Handle button clicks: Update Task, View Backlog, Skip Backlog"""
    actions = payload.get("actions", [])
    if not actions:
        return

    action = actions[0]
    action_id = action.get("action_id", "")
    value_str = action.get("value")

    try:
        value = json.loads(value_str)
    except json.JSONDecodeError:
        print(f"Invalid button value: {value_str}")
        return

    session_id = _normalize_identifier(value.get("session_id"))
    interaction_project_id = _normalize_identifier(value.get("project_id"))
    actor_slack_user_id = _normalize_identifier((payload.get("user") or {}).get("id"))
    if not session_id:
        print(f"Invalid button payload: missing session_id action_id={action_id}")
        return

    # --- Route by action_id ---

    if action_id == "open_update_modal":
        task_index = value.get("task_index")
        session = await _get_active_session_for_interaction(
            db,
            session_id=session_id,
            action_label="open_update_modal",
            notify_on_closed=True,
            enforce_isolation=True,
            actor_slack_user_id=actor_slack_user_id,
            payload_project_id=interaction_project_id,
        )
        if not session:
            print(f"Session not found: {session_id}")
            return

        if not isinstance(task_index, int) or task_index < 0 or task_index >= len(session.get("tasks") or []):
            print(f"Invalid task index for modal open: session={session_id} task_index={task_index}")
            return

        task = session["tasks"][task_index]
        total_tasks = len(session["tasks"])

        project = await db.projects.find_one({"project_id": session["project_id"]})
        slack_token = encryption_service.decrypt(
            project["integrations"]["slack"]["access_token"]
        )
        slack_client = SlackClient(slack_token)

        modal = format_update_task_modal(
            session_id,
            task_index,
            task,
            total_tasks,
            project_id=_normalize_identifier(session.get("project_id")),
        )
        await slack_client.open_modal(trigger_id, modal)
        print(f"Update modal opened | Session: {session_id} | Task: {task['external_id']}")

    elif action_id == "view_backlog":
        await _handle_view_backlog(
            db,
            session_id,
            payload,
            enforce_isolation=True,
            actor_slack_user_id=actor_slack_user_id,
            payload_project_id=interaction_project_id,
        )

    elif action_id == "skip_backlog":
        await handle_backlog_skip_request(
            db,
            session_id,
            enforce_isolation=True,
            actor_slack_user_id=actor_slack_user_id,
            payload_project_id=interaction_project_id,
        )



async def handle_modal_submission(db, payload: Dict[str, Any], trigger_id: str):
    """Handle task update modal submission (status + comment)"""
    view = payload.get("view", {})
    values = view.get("state", {}).get("values", {})
    private_metadata = view.get("private_metadata", "{}")

    try:
        metadata = json.loads(private_metadata)
    except json.JSONDecodeError:
        print(f"Invalid private metadata: {private_metadata}")
        return

    session_id = _normalize_identifier(metadata.get("session_id"))
    task_index = metadata.get("task_index")
    interaction_project_id = _normalize_identifier(metadata.get("project_id"))
    actor_slack_user_id = _normalize_identifier((payload.get("user") or {}).get("id"))
    if not session_id:
        print("Invalid modal metadata: missing session_id")
        return

    # Get session first for deduplication check
    session = await _get_active_session_for_interaction(
        db,
        session_id=session_id,
        action_label="modal_submit",
        notify_on_closed=True,
        enforce_isolation=True,
        actor_slack_user_id=actor_slack_user_id,
        payload_project_id=interaction_project_id,
    )
    if not session:
        print(f"Session not found: {session_id}")
        return

    if not isinstance(task_index, int) or task_index < 0 or task_index >= len(session.get("tasks") or []):
        print(f"Invalid task index for modal submit: session={session_id} task_index={task_index}")
        return

    # Session-based deduplication: if task_index < current_task_index, already processed
    current_task_index = session.get("current_task_index", 0)
    if task_index < current_task_index:
        print(f"Task {task_index} already processed (current: {current_task_index}), skipping")
        return

    # Extract new status from dropdown
    status_block = values.get("status_input", {})
    status_value = status_block.get("status_value", {})
    selected_option = status_value.get("selected_option", {})
    new_status = selected_option.get("value", "")

    # Extract comment (optional)
    comment_block = values.get("comment_input", {})
    comment_value = comment_block.get("comment_value", {})
    comment = comment_value.get("value", "")

    task = session["tasks"][task_index]
    task_key = task["external_id"]
    current_status = task["status"]

    # Determine what changed
    status_changed = (new_status != current_status)
    has_comment = bool(comment and comment.strip())

    print(f"Task update | Session: {session_id} | Task: {task_key} | Status changed: {status_changed} | Has comment: {has_comment}")

    # Update session and Jira ONLY if something changed
    if status_changed or has_comment:
        # Update session with new values
        if status_changed:
            await update_task_status(db, session_id, task_index, new_status)

        if has_comment:
            await update_task_comment(db, session_id, task_index, comment)

        # Re-fetch session to get updated values
        session = await get_session(db, session_id)
        if not session:
            print(f"Session not found after update: {session_id}")
            return

        # Update Jira (status + comment)
        # Jira will then fire webhook, which auto-syncs back to tasks collection
        await update_jira_task(db, session, task_index)
    else:
        print(f"No changes for task {task_key} - skipping Jira update")

    # Capture previous task context for LLM
    previous_context = {
        "task_key": task["external_id"],
        "title": task["title"],
        "new_status": new_status,
        "comment": comment,
        "status_changed": status_changed,
        "has_comment": has_comment
    }

    is_last_agenda_task = (task_index + 1) >= len(session.get("tasks", []))

    if is_last_agenda_task:
        await set_overall_input_state(db, session_id)
        print(f"Cadence state transition: session={session_id} status=awaiting_overall_input")
        await send_overall_update_prompt(db, session_id)
        return

    # Always move to next task (even if no changes)
    has_more = await move_to_next_task(db, session_id)
    if has_more:
        # Send next task message WITH previous context
        await send_next_task_message(db, session_id, previous_context)
    else:
        print(f"Session completed | Session: {session_id}")
        await send_session_summary(db, session_id)


async def _dispatch_cadence_task_mutation(
    db,
    *,
    session: Dict[str, Any],
    capability_id: str,
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    from app.cadence.action_adapters.registry import (
        CadenceAdapterDispatchError,
        CadenceAdapterNotFoundError,
        cadence_action_adapter_registry,
    )

    adapter_provider = str(session.get("task_provider") or "jira").strip().lower() or "jira"
    try:
        result = await cadence_action_adapter_registry.dispatch(
            provider=adapter_provider,
            capability_id=capability_id,
            db=db,
            session=session,
            payload=payload,
            context={"source": "cadence.interaction_handler"},
        )
        return dict(result or {})
    except CadenceAdapterNotFoundError:
        logger.info(
            "cadence_adapter_missing provider=%s capability_id=%s",
            adapter_provider,
            capability_id,
        )
        return None
    except CadenceAdapterDispatchError as exc:
        logger.warning(
            "cadence_adapter_dispatch_failed provider=%s capability_id=%s error=%s",
            adapter_provider,
            capability_id,
            exc,
        )
        return None


async def update_jira_task(db, session: Dict[str, Any], task_index: int):
    """Update task through the registered capability adapter."""
    try:
        task = session["tasks"][task_index]
        task_key = str(task.get("external_id") or "").strip()
        updated_status = task.get("updated_status")
        comment = task.get("comment", "")
        capability_id = "workitem_update_status" if updated_status else "workitem_add_comment"
        payload: Dict[str, Any] = {
            "project_id": session.get("project_id"),
            "task_id": task_key,
            "status": updated_status,
            "comment": comment,
            "updated_by": _resolve_updated_by_identity(session),
            "source_label": "Slack",
            "add_status_auto_comment": True,
            "api_delay_seconds": JIRA_API_DELAY,
        }
        adapter_result = await _dispatch_cadence_task_mutation(
            db,
            session=session,
            capability_id=capability_id,
            payload=payload,
        )
        if not adapter_result or not adapter_result.get("ok"):
            task_provider = str(session.get("task_provider") or "jira").strip().lower() or "jira"
            error_code = (
                str(adapter_result.get("error") or "").strip()
                if isinstance(adapter_result, dict)
                else "adapter_unavailable"
            )
            logger.warning(
                "cadence_task_mutation_unavailable provider=%s capability_id=%s error=%s",
                task_provider,
                capability_id,
                error_code,
            )
            return

        if updated_status:
            if adapter_result.get("status_updated"):
                logger.info("jira_status_updated task=%s status=%s", task_key, updated_status)
            else:
                logger.warning("jira_status_update_failed task=%s", task_key)
        if comment or updated_status:
            if adapter_result.get("comment_added"):
                logger.info("jira_comment_added task=%s", task_key)
            else:
                logger.warning("jira_comment_add_failed task=%s", task_key)
        if not adapter_result.get("brain_sync_succeeded", True):
            logger.warning(
                "jira_brain_sync_unconfirmed task=%s webhook_compensation_expected=true",
                task_key,
            )
        project_id = str(session.get("project_id") or "").strip()
        if project_id and task_key:
            mark_project_task_dirty(project_id, task_key)
        await mark_task_updated_in_jira(db, session["session_id"], task_index)
        logger.info("jira_task_updated task=%s", task_key)
    except Exception as exc:
        logger.exception("cadence_update_jira_task_failed error=%s", exc)


async def send_next_task_message(db, session_id: str, previous_context: Dict[str, Any] = None):
    """Send message for the next task in the session agenda."""
    logger.info("send_next_task_message_called session=%s", session_id)
    try:
        session = await get_session(db, session_id)
        if not session:
            logger.warning("send_next_task_message_session_missing session=%s", session_id)
            return

        current_index = session["current_task_index"]
        tasks = session["tasks"]
        task = tasks[current_index]

        # Resolve Slack user
        slack_user_id, slack_client, user_member = await _resolve_slack_for_session(db, session)
        if not slack_user_id:
            logger.warning(
                "send_next_task_message_slack_unresolved session=%s user_id=%s",
                session_id,
                session.get("user_id"),
            )
            return

        project = await db.projects.find_one({"project_id": session["project_id"]})

        # Generate LLM transition message with previous context unless load-shed is active.
        llm_message = None
        queue_depth = get_queue_depth("cadence_dm")
        threshold = max(
            1,
            int(getattr(settings, "cadence_cosmetic_llm_queue_depth_threshold", 50) or 50),
        )
        cosmetic_llm_enabled = bool(getattr(settings, "cadence_cosmetic_llm_enabled", True))
        should_skip_llm = (not cosmetic_llm_enabled) or (queue_depth >= 0 and queue_depth > threshold)
        llm_svc = get_llm_service()
        if llm_svc and not should_skip_llm:
            try:
                llm_message = await llm_svc.generate_task_message(
                    project_name=project.get("project_name", "Your Project"),
                    user_name=user_member.get("name", session["user_id"]),
                    task=task,
                    task_index=current_index,
                    total_tasks=len(tasks),
                    previous_context=previous_context,
                )
            except Exception as e:
                print(f"Error generating LLM message: {str(e)}")

        # Use default transition if LLM didn't generate one
        if not llm_message and previous_context:
            llm_message = "Got it! Moving on..."

        # Format and send message
        message = format_paid_tier_message(
            task=task,
            task_index=current_index,
            total_tasks=len(tasks),
            session_id=session_id,
            project_id=_normalize_identifier(session.get("project_id")),
            llm_message=llm_message,
        )

        await slack_client.send_message(slack_user_id, blocks=message["blocks"])
        logger.info(
            "send_next_task_message_sent session=%s task_index=%s task_key=%s total=%s",
            session_id,
            current_index,
            str(task.get("external_id") or ""),
            len(tasks),
        )

    except Exception as e:
        logger.error("send_next_task_message_failed session=%s error=%s", session_id, e)


async def send_session_summary(db, session_id: str):
    """Send Block Kit summary message when all tasks are reviewed."""
    try:
        session = await get_session(db, session_id)
        if not session:
            print(f"Session not found for summary: {session_id}")
            return

        tasks = session["tasks"]
        total_tasks = len(tasks)

        # Analyze session results.
        # Prefer persisted session_summary counts so Slack recap aligns with
        # canonical summary logic (and avoids duplicate counting drift).
        tasks_completed = 0
        tasks_updated = 0
        tasks_skipped = 0

        # --- Per-session summary + daily summary trigger ---------------
        # Run independently from Slack DM delivery so persisted summary data
        # is not lost if Slack user resolution or DM send fails.
        summary_reason = str(session.get("expired_reason") or "agenda_complete").strip()
        member_name = ""
        try:
            project = await db.projects.find_one(
                {"project_id": session.get("project_id")},
                {"members": 1},
            )
            members = (project or {}).get("members") or []
            member = next(
                (m for m in members if m.get("user_id") == session.get("user_id")),
                None,
            )
            member_name = str((member or {}).get("name") or "").strip()
        except Exception as member_err:
            print(f"[cadence] Member name resolution failed for summary: {member_err}")

        try:
            from app.cadence.session_store import build_and_store_session_summary
            from app.cadence.summary import try_generate_daily_summary

            await build_and_store_session_summary(
                db,
                session_id,
                reason=summary_reason,
                member_name=member_name,
            )
            await try_generate_daily_summary(db, session_id)
            refreshed_session = await get_session(
                db,
                session_id,
                project_id=str(session.get("project_id") or "").strip() or None,
            )
            summary_payload = (refreshed_session or {}).get("session_summary") or {}
            if isinstance(summary_payload, dict) and summary_payload:
                tasks_completed = len(summary_payload.get("tasks_completed") or [])
                tasks_updated = len(summary_payload.get("tasks_updated") or [])
                try:
                    tasks_skipped = int(summary_payload.get("tasks_skipped") or 0)
                except Exception:
                    tasks_skipped = 0
                tasks = list((refreshed_session or {}).get("tasks") or tasks)
            else:
                # Fallback when summary payload is unavailable.
                for task in tasks:
                    updated_status = task.get("updated_status")
                    comment = task.get("comment", "")
                    status_changed = (updated_status is not None and updated_status != task["status"])
                    has_comment = bool(comment and comment.strip())

                    if status_changed and _is_completed_for_summary(status_value=updated_status):
                        tasks_completed += 1
                    elif status_changed or has_comment:
                        tasks_updated += 1
                    else:
                        tasks_skipped += 1
        except Exception as summary_err:
            print(f"[cadence] Post-session summary generation error: {summary_err}")
            # Fallback when summary pipeline fails.
            for task in tasks:
                updated_status = task.get("updated_status")
                comment = task.get("comment", "")
                status_changed = (updated_status is not None and updated_status != task["status"])
                has_comment = bool(comment and comment.strip())

                if status_changed and _is_completed_for_summary(status_value=updated_status):
                    tasks_completed += 1
                elif status_changed or has_comment:
                    tasks_updated += 1
                else:
                    tasks_skipped += 1

        # Resolve Slack user
        slack_user_id, slack_client, user_member = await _resolve_slack_for_session(db, session)
        if not slack_user_id:
            return

        # Generate LLM summary text
        summary_text = None
        queue_depth = get_queue_depth("cadence_dm")
        threshold = max(
            1,
            int(getattr(settings, "cadence_cosmetic_llm_queue_depth_threshold", 50) or 50),
        )
        cosmetic_llm_enabled = bool(getattr(settings, "cadence_cosmetic_llm_enabled", True))
        should_skip_llm = (not cosmetic_llm_enabled) or (queue_depth >= 0 and queue_depth > threshold)

        llm_svc = get_llm_service()
        if llm_svc and not should_skip_llm:
            try:
                display_name = (
                    user_member.get("name")
                    if user_member else member_name or session["user_id"]
                )
                summary_text = await llm_svc.generate_session_summary(
                    user_name=display_name,
                    total_tasks=total_tasks,
                    tasks_completed=tasks_completed,
                    tasks_updated=tasks_updated,
                    tasks_skipped=tasks_skipped,
                )
            except Exception as e:
                print(f"Error generating summary message: {str(e)}")

        # Fallback
        if not summary_text:
            if tasks_completed == total_tasks:
                summary_text = f"Great work! You completed all {total_tasks} tasks!"
            elif tasks_completed > 0:
                summary_text = f"Nice work! You completed {tasks_completed} out of {total_tasks} tasks."
            else:
                summary_text = "Thanks for reviewing your tasks!"

        # Send Block Kit summary
        summary_msg = format_session_summary(summary_text, tasks)
        await slack_client.send_message(slack_user_id, blocks=summary_msg["blocks"])

        print(f"Session summary sent | Session: {session_id} | Completed: {tasks_completed}/{total_tasks}")
        try:
            reminded = await maybe_send_post_cadence_pending_poll_reminder(
                db,
                cadence_session=session,
                slack_user_id=slack_user_id,
                slack_client=slack_client,
            )
            if reminded:
                logger.info("post_cadence_pending_poll_reminder_sent session=%s", session_id)
        except Exception as reminder_exc:
            logger.info(
                "post_cadence_pending_poll_reminder_failed_nonfatal session=%s error=%s",
                session_id,
                reminder_exc,
            )

    except Exception as e:
        print(f"Error sending session summary: {str(e)}")


# ---------------------------------------------------------------------------
#  Overall update prompt (post-agenda, pre-backlog)
# ---------------------------------------------------------------------------

async def send_overall_update_prompt(db, session_id: str):
    """Ask for overall blockers/risks/important updates before backlog step."""
    try:
        session = await get_session(db, session_id)
        if not session:
            return

        slack_user_id, slack_client, _ = await _resolve_slack_for_session(db, session)
        if not slack_user_id:
            return

        await slack_client.send_message(
            slack_user_id,
            text=(
                "Before we wrap this check-in, share any overall blockers, risks, "
                "or important updates I should highlight. Reply with details, or say `skip`."
            ),
        )
        print(f"Cadence overall update prompt sent | Session: {session_id}")
    except Exception as e:
        print(f"Error sending overall update prompt: {str(e)}")


# ---------------------------------------------------------------------------
#  Backlog nudge helpers
# ---------------------------------------------------------------------------

async def _send_backlog_nudge(db, session_id: str):
    """Send 'View Them / I'm Good' prompt after in-progress tasks are done."""
    try:
        session = await get_session(db, session_id)
        if not session:
            return

        backlog_tasks = session.get("backlog_tasks", [])
        if not backlog_tasks:
            await complete_session(db, session_id, reason="backlog_exhausted")
            await send_session_summary(db, session_id)
            return

        active_workload_count = calculate_active_workload_count(session)
        threshold = max(0, int(getattr(settings, "cadence_backlog_prompt_max_active_tasks", 1) or 1))
        if active_workload_count > threshold:
            await set_backlog_prompt_state(
                db,
                session_id,
                prompted=False,
                skipped_by_policy=True,
                skip_reason="workload_threshold",
                active_workload_count=active_workload_count,
                threshold=threshold,
            )
            await complete_session(db, session_id, reason="backlog_skipped_workload_policy")

            print(
                "Cadence backlog prompt skipped by workload policy: "
                f"session={session_id} active_workload={active_workload_count} threshold={threshold}"
            )
            await send_session_summary(db, session_id)
            return

        await set_backlog_prompt_state(
            db,
            session_id,
            prompted=True,
            skipped_by_policy=False,
            skip_reason=None,
            active_workload_count=active_workload_count,
            threshold=threshold,
        )

        await set_backlog_decision_state(db, session_id)

        slack_user_id, slack_client, _ = await _resolve_slack_for_session(db, session)
        if not slack_user_id:
            return

        nudge = format_backlog_nudge(
            backlog_tasks,
            session_id,
            project_id=_normalize_identifier(session.get("project_id")),
        )
        await slack_client.send_message(slack_user_id, blocks=nudge["blocks"])
        print(f"Backlog nudge sent | Session: {session_id} | Backlog: {len(backlog_tasks)}")
        print(f"Cadence state transition: session={session_id} status=awaiting_backlog_decision")

    except Exception as e:
        print(f"Error sending backlog nudge: {str(e)}")


async def _handle_view_backlog(
    db,
    session_id: str,
    payload: Dict[str, Any],
    *,
    enforce_isolation: bool = False,
    actor_slack_user_id: Optional[str] = None,
    payload_project_id: Optional[str] = None,
):
    """User tapped 'View Them' — send a numbered backlog list and wait for pick."""
    try:
        session = await _get_active_session_for_interaction(
            db,
            session_id=session_id,
            action_label="view_backlog",
            notify_on_closed=True,
            enforce_isolation=enforce_isolation,
            actor_slack_user_id=actor_slack_user_id,
            payload_project_id=payload_project_id,
        )
        if not session:
            return

        backlog_tasks = session.get("backlog_tasks", [])
        if not backlog_tasks:
            await complete_session(db, session_id, reason="backlog_exhausted")
            await send_session_summary(db, session_id)
            return

        slack_user_id, slack_client, _ = await _resolve_slack_for_session(db, session)
        if not slack_user_id:
            return

        await set_backlog_pick_state(db, session_id)
        list_msg = format_backlog_pick_list(backlog_tasks)
        await slack_client.send_message(slack_user_id, blocks=list_msg["blocks"])
        print(f"Backlog list sent | Session: {session_id} | Count: {len(backlog_tasks)}")
        print(f"Cadence state transition: session={session_id} status=awaiting_backlog_pick")

    except Exception as e:
        print(f"Error handling view backlog: {str(e)}")


async def handle_backlog_show_request(db, session_id: str) -> bool:
    """Shared backlog-show behavior for button and DM text paths."""
    await _handle_view_backlog(db, session_id, payload={})
    return True


async def handle_backlog_skip_request(
    db,
    session_id: str,
    *,
    enforce_isolation: bool = False,
    actor_slack_user_id: Optional[str] = None,
    payload_project_id: Optional[str] = None,
) -> bool:
    """Skip backlog for now and complete the session with a closure note."""
    session = await _get_active_session_for_interaction(
        db,
        session_id=session_id,
        action_label="skip_backlog",
        notify_on_closed=True,
        enforce_isolation=enforce_isolation,
        actor_slack_user_id=actor_slack_user_id,
        payload_project_id=payload_project_id,
    )
    if not session:
        return False

    slack_user_id, slack_client, _ = await _resolve_slack_for_session(db, session)
    if slack_user_id:
        await slack_client.send_message(
            slack_user_id,
            text=(
                "No problem. We'll leave those tasks in backlog for now. "
                "You can ask me anytime for your backlog or to-do tasks and I'll help."
            ),
        )
    await complete_session(db, session_id, reason="backlog_deferred")
    print(f"Cadence backlog deferred: session={session_id}")
    await send_session_summary(db, session_id)
    return True


async def handle_backlog_pick_start(
    db,
    session_id: str,
    *,
    backlog_index: int,
) -> Dict[str, Any]:
    """Move one backlog task to in_progress and update session backlog state."""
    session = await _get_active_session_for_interaction(
        db,
        session_id=session_id,
        action_label="pick_backlog_task",
        notify_on_closed=True,
    )
    if not session:
        return {"ok": False, "error": "session_not_active"}

    backlog_tasks = list(session.get("backlog_tasks") or [])
    if backlog_index < 0 or backlog_index >= len(backlog_tasks):
        return {"ok": False, "error": "invalid_backlog_index"}

    selected = backlog_tasks[backlog_index]
    raw_external_id = str(selected.get("external_id") or "").strip()
    if not raw_external_id:
        return {"ok": False, "error": "missing_external_id"}
    try:
        external_id = normalize_external_id(raw_external_id)
    except Exception:
        external_id = raw_external_id.upper()

    project_id = str(session.get("project_id") or "").strip()
    adapter_payload = {
        "project_id": project_id,
        "task_id": external_id,
        "status": "in_progress",
        "comment": None,
        "updated_by": _resolve_updated_by_identity(session),
        "source_label": "Slack",
        "add_status_auto_comment": True,
        "api_delay_seconds": JIRA_API_DELAY,
    }
    adapter_result = await _dispatch_cadence_task_mutation(
        db=db,
        session=session,
        capability_id="workitem_update_status",
        payload=adapter_payload,
    )
    if not adapter_result or not adapter_result.get("ok"):
        task_provider = str(session.get("task_provider") or "jira").strip().lower() or "jira"
        error_code = (
            str(adapter_result.get("error") or "").strip()
            if isinstance(adapter_result, dict)
            else "adapter_unavailable"
        )
        logger.warning(
            "cadence_backlog_pick_mutation_unavailable session=%s provider=%s error=%s",
            session_id,
            task_provider,
            error_code,
        )
        return {"ok": False, "error": error_code or "adapter_unavailable"}

    status_updated = bool(adapter_result.get("status_updated"))
    already_progressed = bool(adapter_result.get("already_progressed"))
    final_status = str(adapter_result.get("task_status") or "unknown").strip().lower()
    if not status_updated and not already_progressed:
        return {
            "ok": False,
            "error": "status_update_failed",
            "external_id": external_id,
            "final_status": final_status or "unknown",
        }
    if project_id and external_id:
        mark_project_task_dirty(project_id, external_id)

    removed = await remove_backlog_task_at_index(db, session_id, backlog_index)
    if not removed:
        return {"ok": False, "error": "backlog_remove_failed"}
    if not await add_backlog_picked_task(db, session_id, removed):
        logger.warning("cadence_backlog_pick_record_failed session=%s", session_id)

    refreshed = await get_session(db, session_id)
    remaining = len((refreshed or {}).get("backlog_tasks") or [])
    if remaining > 0:
        await set_backlog_decision_state(db, session_id)
        logger.info("cadence_state_transition session=%s status=awaiting_backlog_decision", session_id)
    else:
        await complete_session(db, session_id, reason="backlog_empty_after_pick")

    return {
        "ok": True,
        "external_id": external_id,
        "remaining_backlog": remaining,
        "status_updated": bool(status_updated),
        "already_progressed": bool(already_progressed),
        "final_status": final_status or "unknown",
    }


# ---------------------------------------------------------------------------
#  Shared helper: resolve Slack user for a session
# ---------------------------------------------------------------------------

async def _resolve_slack_for_session(db, session: Dict[str, Any]):
    """Return (slack_user_id, slack_client, user_member) or (None, None, None)."""
    project = await db.projects.find_one({"project_id": session["project_id"]})
    if not project:
        print(f"Project not found: {session['project_id']}")
        return None, None, None

    slack_token = encryption_service.decrypt(
        project["integrations"]["slack"]["access_token"]
    )
    slack_client = SlackClient(slack_token)

    user_id = session["user_id"]
    members = project.get("members", [])
    user_member = next((m for m in members if m.get("user_id") == user_id), None)

    if not user_member:
        print(f"User not found in project members: {user_id}")
        return None, None, None

    slack_user_id = await resolve_slack_user_id_for_member(
        db=db, project=project, member=user_member, slack_client=slack_client,
    )

    if not slack_user_id:
        print(f"Slack user not found for: {user_member.get('email')}")
        return None, None, None

    return slack_user_id, slack_client, user_member


# ==================== DIRECT HANDLERS (NO QUEUE) ====================

async def handle_button_click_direct(payload: Dict[str, Any]):
    """
    Handle button click DIRECTLY (no queue)

    Called directly from FastAPI endpoint to avoid trigger_id expiry.
    This is fast (~300ms): read session + format modal + call Slack API

    Args:
        payload: Slack interaction payload
    """
    db = get_database()
    trigger_id = payload.get("trigger_id")

    try:
        # No deduplication needed - opening modal is idempotent
        await handle_button_click(db, payload, trigger_id)
    except Exception as e:
        print(f"Error handling button click: {str(e)}")
        import traceback
        traceback.print_exc()


async def handle_modal_submission_direct(payload: Dict[str, Any]):
    """
    Async direct handler for modal submissions inside an existing event loop.

    Used by FastAPI fallback path when Redis queue is unavailable.
    """
    db = get_database()
    trigger_id = payload.get("trigger_id")
    view = payload.get("view", {})
    private_metadata = str(view.get("private_metadata") or "{}")
    session_id = ""
    try:
        metadata = json.loads(private_metadata)
        session_id = _normalize_identifier(metadata.get("session_id"))
    except Exception:
        session_id = ""

    lease_enabled = bool(getattr(settings, "cadence_modal_lease_enabled", True))
    lease_acquired = False
    lease_token = 0
    lease_ctx_token = None
    heartbeat_task: Optional[asyncio.Task] = None

    async def _lease_heartbeat() -> None:
        while True:
            await asyncio.sleep(max(2, int(getattr(settings, "cadence_lease_heartbeat_seconds", 15) or 15)))
            ok = renew_session_lease(
                session_id=session_id,
                token=lease_token,
                ttl_seconds=max(5, int(getattr(settings, "cadence_lease_ttl_seconds", 45) or 45)),
            )
            if not ok:
                return

    if lease_enabled and session_id:
        lease_acquired, lease_token = acquire_session_lease(
            session_id=session_id,
            ttl_seconds=max(5, int(getattr(settings, "cadence_lease_ttl_seconds", 45) or 45)),
        )
        if lease_acquired and int(lease_token or 0) > 0:
            try:
                await stamp_session_lease_version(
                    db,
                    session_id,
                    int(lease_token),
                )
            except Exception as exc:
                logger.warning(
                    "cadence_modal_lease_version_stamp_failed session=%s token=%s error=%s",
                    session_id,
                    int(lease_token),
                    exc,
                )
            lease_ctx_token = set_active_session_lease(
                session_id=session_id,
                lease_token=int(lease_token),
                enforce=bool(getattr(settings, "cadence_lease_enforce", False)),
            )
            heartbeat_task = asyncio.create_task(_lease_heartbeat())
        elif int(lease_token or 0) > 0:
            print(f"Cadence modal lease contention: session={session_id}")
            # Modal submit is already acked by interactions route; DM fallback UX only.
            session = await get_session(db, session_id)
            if session:
                slack_user_id, slack_client, _ = await _resolve_slack_for_session(db, session)
                if slack_user_id:
                    await slack_client.send_message(
                        slack_user_id,
                        text="Still processing your previous cadence update. Please retry in a few seconds.",
                    )
            return

    try:
        await handle_modal_submission(db, payload, trigger_id)
    except Exception as e:
        print(f"Error handling modal submission direct: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        if lease_ctx_token is not None:
            reset_active_session_lease(lease_ctx_token)
        if lease_acquired and int(lease_token or 0) > 0:
            release_session_lease(session_id=session_id, token=int(lease_token))


# ==================== SYNC WRAPPERS FOR RQ ====================

def handle_modal_submission_sync(payload: Dict[str, Any]):
    """
    Synchronous wrapper for modal submission for RQ workers

    Modal submissions are QUEUED because they're slow (1-5s):
    - Update MongoDB
    - Call Jira API
    - Send next message

    Args:
        payload: Slack interaction payload
    """
    async def run_with_setup():
        """Setup connections and run the handler"""
        from app.core.database import ensure_mongo_ready
        from app.core.config import settings
        from app.cadence.llm_service import initialize_llm_service

        await ensure_mongo_ready()

        # Initialize LLM service if configured
        if settings.groq_api_key and settings.groq_api_key != "your-groq-api-key-here":
            initialize_llm_service(settings.groq_api_key)

        db = get_database()
        trigger_id = payload.get("trigger_id")

        # Handle modal submission (deduplication is handled inside using session state)
        await handle_modal_submission(db, payload, trigger_id)

    # Run with asyncio
    asyncio.run(run_with_setup())
