"""MongoDB session storage for Cadence sessions.

Cadence session payloads are managed by ``SessionRepository`` and stored in
project-scoped Brain collections.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import uuid
from app.core.config import settings
from app.core.database import get_brain_collection
from app.projects.recommendations.normalized_task_model import derive_status_category
from app.sessions.repository import SessionRepository

try:
    from motor.motor_asyncio import AsyncIOMotorDatabase
except Exception:  # pragma: no cover - local test environments may not install motor.
    AsyncIOMotorDatabase = Any  # type: ignore[misc,assignment]

# ---------------------------------------------------------------------------
# Source discriminator
# ---------------------------------------------------------------------------
SOURCE = "cadence"
ENTITY_TYPE = "cadence_session"
_ACTIVE_WORKLOAD_STATUSES = {"todo", "in_progress", "in_review", "blocked"}
logger = logging.getLogger("app.cadence.session_store")


def _repo(db: AsyncIOMotorDatabase) -> SessionRepository:
    return SessionRepository(db=db)


def _extract_last_comment_text(task: Dict[str, Any]) -> Optional[str]:
    comments = task.get("comments")
    if isinstance(comments, list):
        for item in reversed(comments):
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                if text:
                    return text
            elif isinstance(item, str):
                text = str(item).strip()
                if text:
                    return text
    fallback = str(task.get("comment") or "").strip()
    return fallback or None


async def create_session(
    db: AsyncIOMotorDatabase,
    user_id: str,
    project_id: str,
    tasks: List[Dict[str, Any]],
    correlation_id: Optional[str] = None,
    provider: Optional[str] = None,
    task_provider: Optional[str] = None,
    workspace_id: Optional[str] = None,
    external_user_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    member_email: Optional[str] = None,
    member_name: Optional[str] = None,
    session_type: str = "task_checkin",
    initial_status: str = "awaiting_status",
    completion_reason: Optional[str] = None,
    correlation_expected_count: Optional[int] = None,
    integration_health: Optional[Dict[str, Any]] = None,
    initial_reason_codes: Optional[List[str]] = None,
) -> str:
    """
    Create a new Cadence session.

    Args:
        db: MongoDB database instance
        user_id: User ID
        project_id: Project ID
        tasks: List of tasks for this user
        correlation_id: Cron run correlation ID

    Returns:
        Session ID
    """
    session_id = str(uuid.uuid4())

    # Prepare task list for session (keep original field names from tasks collection)
    session_tasks = []
    for task in tasks:
        last_comment = _extract_last_comment_text(task)
        session_tasks.append({
            "external_id": task.get("external_id"),
            "title": task.get("title"),
            "provider_type": task.get("provider_type") or task.get("issue_type"),
            "work_item_type": task.get("work_item_type"),
            "status": task.get("status"),
            "priority": task.get("priority"),
            "url": task.get("url"),
            "last_comment": last_comment,
            "assignee_email": task.get("assignee_email"),
            "assignee_name": task.get("assignee_name"),
            "assignee_account_id": task.get("assignee_account_id"),
            "available_transitions": list(task.get("available_transitions") or []),
            "updated_status": None,
            "comment": None,
            "jira_updated": False,
        })

    now = datetime.utcnow()
    normalized_provider = str(provider or "").strip().lower() or None
    normalized_task_provider = str(task_provider or "").strip().lower() or None
    normalized_workspace_id = str(workspace_id or "").strip() or None
    normalized_external_user_id = str(external_user_id or "").strip() or None
    normalized_channel_id = str(channel_id or "").strip() or None
    normalized_initial_status = str(initial_status or "awaiting_status").strip().lower()
    normalized_session_type = str(session_type or "task_checkin").strip().lower() or "task_checkin"
    normalized_member_email = str(member_email or "").strip().lower() or None
    normalized_member_name = str(member_name or "").strip() or None
    normalized_completion_reason = str(completion_reason or "").strip() or None
    normalized_reason_codes = sorted(
        {
            str(item).strip().lower()
            for item in (initial_reason_codes or [])
            if str(item).strip()
        }
    )
    integration_health_doc = {
        "task_provider_mapping_present": (
            bool((integration_health or {}).get("task_provider_mapping_present"))
            if (integration_health or {}).get("task_provider_mapping_present") is not None
            else None
        ),
        "slack_mapping_present": (
            bool((integration_health or {}).get("slack_mapping_present"))
            if (integration_health or {}).get("slack_mapping_present") is not None
            else None
        ),
    }
    is_terminal = normalized_initial_status == "completed"
    session_doc = {
        "entity_type": ENTITY_TYPE,
        "session_id": session_id,
        "session_key": f"cadence-{session_id}",
        "source": SOURCE,
        "user_id": user_id,
        "member_email": normalized_member_email,
        "member_name": normalized_member_name,
        "project_id": project_id,
        "status": normalized_initial_status,  # awaiting_status, awaiting_comment, clarification, completed
        "session_type": normalized_session_type,
        "tasks": session_tasks,
        "current_task_index": 0,
        "clarification_payload": None,
        "overall_update_input": None,
        "overall_update_input_skipped": False,
        "backlog_prompt_sent": False,
        "backlog_prompt_skipped_by_policy": False,
        "backlog_prompt_skip_reason": None,
        "backlog_prompt_active_workload_count": None,
        "backlog_prompt_threshold": None,
        "correlation_id": correlation_id,
        "provider": normalized_provider,
        "task_provider": normalized_task_provider,
        "workspace_id": normalized_workspace_id,
        "external_user_id": normalized_external_user_id,
        "channel_id": normalized_channel_id,
        "correlation_expected_count": (
            int(correlation_expected_count)
            if correlation_expected_count is not None
            else None
        ),
        "outcome": {
            "task_provider": normalized_task_provider,
            "delivery_status": "pending",
            "response_status": "pending",
            "integration_health": integration_health_doc,
            "reason_codes": normalized_reason_codes,
            "terminal": bool(is_terminal),
        },
        "created_at": now,
        "expires_at": now + timedelta(
            seconds=max(300, int(getattr(settings, "cadence_session_ttl_seconds", 7200) or 7200))
        ),
    }
    if is_terminal:
        session_doc["completed_at"] = now
        session_doc["expired_reason"] = normalized_completion_reason or "completed"

    await _repo(db).create_session(
        entity_type=ENTITY_TYPE,
        project_id=project_id,
        payload=session_doc,
    )

    return session_id


async def get_session(
    db: AsyncIOMotorDatabase,
    session_id: str,
    *,
    project_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Get a Cadence session by ID.

    Args:
        db: MongoDB database instance
        session_id: Session ID

    Returns:
        Session document or None
    """
    return await _repo(db).get_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        project_id=project_id,
    )


async def update_task_status(
    db: AsyncIOMotorDatabase,
    session_id: str,
    task_index: int,
    updated_status: str
) -> bool:
    """
    Update task status in session.

    Args:
        db: MongoDB database instance
        session_id: Session ID
        task_index: Index of task to update
        updated_status: New status value

    Returns:
        True if successful
    """
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc={
            "$set": {
                f"tasks.{task_index}.updated_status": updated_status,
                "status": "awaiting_comment",
            },
            "$unset": {
                "clarification_payload": "",
            },
        },
    )


async def update_task_comment(
    db: AsyncIOMotorDatabase,
    session_id: str,
    task_index: int,
    comment: str
) -> bool:
    """
    Update task comment in session.

    Args:
        db: MongoDB database instance
        session_id: Session ID
        task_index: Index of task to update
        comment: Comment text

    Returns:
        True if successful
    """
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc={
            "$set": {
                f"tasks.{task_index}.comment": comment,
            }
        },
    )


async def mark_task_updated_in_jira(
    db: AsyncIOMotorDatabase,
    session_id: str,
    task_index: int
) -> bool:
    """
    Mark task as updated in Jira.

    Args:
        db: MongoDB database instance
        session_id: Session ID
        task_index: Index of task

    Returns:
        True if successful
    """
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc={
            "$set": {
                f"tasks.{task_index}.jira_updated": True,
            }
        },
    )


async def stamp_session_lease_version(
    db: AsyncIOMotorDatabase,
    session_id: str,
    lease_token: int,
) -> bool:
    """
    Persist active lease token on a cadence session document.

    This primes `lease_version` before fenced updates run with
    `CADENCE_LEASE_ENFORCE=true`.
    """
    token_value = int(lease_token or 0)
    if token_value <= 0:
        return False
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc={
            "$set": {
                "lease_version": token_value,
            }
        },
    )


async def move_to_next_task(db: AsyncIOMotorDatabase, session_id: str) -> bool:
    """
    Move to next task in session.

    Args:
        db: MongoDB database instance
        session_id: Session ID

    Returns:
        True if successful
    """
    session = await get_session(db, session_id)

    if not session:
        return False

    previous_index = int(session.get("current_task_index", 0))
    next_index = previous_index + 1

    # Check if there are more tasks
    if next_index >= len(session["tasks"]):
        # All tasks done - mark session as completed
        await complete_session(db, session_id, reason="agenda_complete")
        return False
    else:
        # Move to next task
        update_committed = await _repo(db).update_session_by_id(
            entity_type=ENTITY_TYPE,
            source=SOURCE,
            session_id=session_id,
            update_doc={
                "$set": {
                    "current_task_index": next_index,
                    "status": "awaiting_status",
                },
                "$unset": {
                    "clarification_payload": "",
                },
            },
        )
        logger.info(
            "move_to_next_task_committed session=%s prev_index=%s new_index=%s committed=%s",
            session_id,
            previous_index,
            next_index,
            bool(update_committed),
        )
        # Preserve agenda-flow behavior when there are remaining tasks.
        # Commit signal is logged for diagnostics; caller should continue.
        return True


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------

async def set_backlog_decision_state(db: AsyncIOMotorDatabase, session_id: str) -> bool:
    """Set session state to waiting for backlog decision reply."""
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc={
            "$set": {
                "status": "awaiting_backlog_decision",
            },
            "$unset": {
                "completed_at": "",
                "expired_reason": "",
                "clarification_payload": "",
            },
        },
    )


async def set_overall_input_state(db: AsyncIOMotorDatabase, session_id: str) -> bool:
    """Set session state to waiting for overall blockers/risks/updates input."""
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc={
            "$set": {
                "status": "awaiting_overall_input",
            },
            "$unset": {
                "completed_at": "",
                "expired_reason": "",
                "clarification_payload": "",
            },
        },
    )


async def set_overall_input(
    db: AsyncIOMotorDatabase,
    session_id: str,
    *,
    text: str,
    skipped: bool,
) -> bool:
    """Persist overall member update captured after task-by-task check-in."""
    normalized = str(text or "").strip()
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc={
            "$set": {
                "overall_update_input": normalized if normalized else None,
                "overall_update_input_skipped": bool(skipped or not normalized),
                "overall_update_recorded_at": datetime.utcnow(),
            }
        },
    )


async def set_backlog_pick_state(db: AsyncIOMotorDatabase, session_id: str) -> bool:
    """Set session state to waiting for a backlog selection."""
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc={
            "$set": {
                "status": "awaiting_backlog_pick",
            },
            "$unset": {
                "completed_at": "",
                "expired_reason": "",
                "clarification_payload": "",
            },
        },
    )


async def set_clarification_state(
    db: AsyncIOMotorDatabase,
    session_id: str,
    *,
    status: str,
    payload: Dict[str, Any],
) -> bool:
    """Set a dedicated clarification state with persisted payload."""
    normalized_status = str(status or "").strip().lower()
    if not normalized_status:
        return False
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc={
            "$set": {
                "status": normalized_status,
                "clarification_payload": dict(payload or {}),
            },
            "$unset": {
                "completed_at": "",
                "expired_reason": "",
            },
        },
    )


async def clear_clarification_state(
    db: AsyncIOMotorDatabase,
    session_id: str,
    *,
    next_status: Optional[str] = None,
) -> bool:
    """Clear clarification payload and optionally set the next state."""
    update_doc: Dict[str, Any] = {"$unset": {"clarification_payload": ""}}
    normalized_next = str(next_status or "").strip().lower()
    if normalized_next:
        update_doc["$set"] = {"status": normalized_next}
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc=update_doc,
    )


async def set_backlog_context(
    db: AsyncIOMotorDatabase,
    session_id: str,
    backlog_tasks: List[Dict[str, Any]],
) -> bool:
    """Store backlog metadata for a session."""
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc={
            "$set": {
                "backlog_tasks": list(backlog_tasks or []),
                "has_backlog": bool(backlog_tasks),
            }
        },
    )


def _normalize_workload_status(raw_status: Any) -> str:
    return str(raw_status or "").strip().lower().replace(" ", "_")


def _is_completed_for_summary(
    *,
    status_value: Any,
    status_category_map: Optional[Dict[str, Any]] = None,
) -> bool:
    status_token = str(status_value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if status_token in {"done", "completed", "complete", "closed", "resolved", "finished", "shipped"}:
        return True
    category = derive_status_category(
        status=status_token,
        item={"status": str(status_value or "")},
        status_category_map=status_category_map if isinstance(status_category_map, dict) else None,
    )
    return category == "done"


def calculate_active_workload_count(session: Dict[str, Any]) -> int:
    """Count current active agenda tasks from final per-task status."""
    tasks = session.get("tasks") or []
    active_count = 0
    for task in tasks:
        if not isinstance(task, dict):
            continue
        final_status = task.get("updated_status")
        if final_status is None:
            final_status = task.get("status")
        normalized = _normalize_workload_status(final_status)
        if normalized in _ACTIVE_WORKLOAD_STATUSES:
            active_count += 1
    return active_count


async def set_backlog_prompt_state(
    db: AsyncIOMotorDatabase,
    session_id: str,
    *,
    prompted: bool,
    skipped_by_policy: bool,
    skip_reason: Optional[str] = None,
    active_workload_count: Optional[int] = None,
    threshold: Optional[int] = None,
) -> bool:
    """Persist whether backlog pickup prompt was sent or policy-skipped."""
    normalized_reason = str(skip_reason or "").strip() or None
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc={
            "$set": {
                "backlog_prompt_sent": bool(prompted),
                "backlog_prompt_skipped_by_policy": bool(skipped_by_policy),
                "backlog_prompt_skip_reason": normalized_reason,
                "backlog_prompt_active_workload_count": (
                    int(active_workload_count) if active_workload_count is not None else None
                ),
                "backlog_prompt_threshold": int(threshold) if threshold is not None else None,
            }
        },
    )


async def complete_session(
    db: AsyncIOMotorDatabase,
    session_id: str,
    *,
    reason: str = "completed",
) -> bool:
    """Mark a session as completed with an explicit reason."""
    normalized_reason = str(reason or "completed").strip().lower() or "completed"
    responded_reasons = {
        "agenda_complete",
        "backlog_exhausted",
        "backlog_deferred",
        "backlog_skipped_workload_policy",
        "backlog_empty_after_pick",
        "agent_complete",
        "completed",
    }
    response_status = "responded" if normalized_reason in responded_reasons else "no_response_expired"
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc={
            "$set": {
                "status": "completed",
                "completed_at": datetime.utcnow(),
                "expired_reason": normalized_reason,
                "outcome.response_status": response_status,
                "outcome.terminal": True,
            },
            "$addToSet": {
                "outcome.reason_codes": normalized_reason,
            },
            "$unset": {
                "clarification_payload": "",
            },
        },
    )


async def set_session_outcome(
    db: AsyncIOMotorDatabase,
    session_id: str,
    *,
    delivery_status: Optional[str] = None,
    response_status: Optional[str] = None,
    task_provider_mapping_present: Optional[bool] = None,
    slack_mapping_present: Optional[bool] = None,
    terminal: Optional[bool] = None,
    reason_code: Optional[str] = None,
) -> bool:
    """Update structured cadence outcome fields on an existing session."""
    set_doc: Dict[str, Any] = {}
    add_to_set: Dict[str, Any] = {}
    if delivery_status is not None:
        normalized = str(delivery_status).strip().lower()
        if normalized:
            set_doc["outcome.delivery_status"] = normalized
    if response_status is not None:
        normalized = str(response_status).strip().lower()
        if normalized:
            set_doc["outcome.response_status"] = normalized
    if task_provider_mapping_present is not None:
        set_doc["outcome.integration_health.task_provider_mapping_present"] = bool(task_provider_mapping_present)
    if slack_mapping_present is not None:
        set_doc["outcome.integration_health.slack_mapping_present"] = bool(slack_mapping_present)
    if terminal is not None:
        set_doc["outcome.terminal"] = bool(terminal)
    normalized_reason = str(reason_code or "").strip().lower()
    if normalized_reason:
        add_to_set["outcome.reason_codes"] = normalized_reason

    update_doc: Dict[str, Any] = {}
    if set_doc:
        update_doc["$set"] = set_doc
    if add_to_set:
        update_doc["$addToSet"] = add_to_set
    if not update_doc:
        return True
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc=update_doc,
    )


async def mark_expiry_notice_sent(
    db: AsyncIOMotorDatabase,
    session_id: str,
    project_id: Optional[str] = None,
) -> bool:
    """Persist that expiry closure notice was delivered for this session."""
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        project_id=project_id,
        update_doc={
            "$set": {
                "expiry_notice_sent_at": datetime.utcnow(),
            },
            "$unset": {
                "expiry_notice_claimed_at": "",
            },
        },
    )


async def claim_expiry_notice_send(
    db: AsyncIOMotorDatabase,
    session_id: str,
    *,
    project_id: Optional[str] = None,
) -> bool:
    """Atomically claim expiry notice send rights for one timed-out session."""
    normalized_session_id = str(session_id or "").strip()
    normalized_project_id = str(project_id or "").strip()
    if not normalized_session_id:
        return False

    if not normalized_project_id:
        session = await get_session(db, normalized_session_id)
        normalized_project_id = str((session or {}).get("project_id") or "").strip()
    if not normalized_project_id:
        return False

    collection = get_brain_collection(normalized_project_id)
    result = await collection.update_one(
        {
            "entity_type": ENTITY_TYPE,
            "source": SOURCE,
            "session_id": normalized_session_id,
            "expired_reason": "timeout",
            "expiry_notice_sent_at": {"$exists": False},
            "expiry_notice_claimed_at": {"$exists": False},
        },
        {
            "$set": {
                "expiry_notice_claimed_at": datetime.utcnow(),
            }
        },
    )
    return bool(result.modified_count)


async def clear_expiry_notice_claim(
    db: AsyncIOMotorDatabase,
    session_id: str,
    *,
    project_id: Optional[str] = None,
) -> bool:
    """Release expiry notice claim when send fails before completion."""
    normalized_session_id = str(session_id or "").strip()
    normalized_project_id = str(project_id or "").strip()
    if not normalized_session_id:
        return False

    if not normalized_project_id:
        session = await get_session(db, normalized_session_id)
        normalized_project_id = str((session or {}).get("project_id") or "").strip()
    if not normalized_project_id:
        return False

    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=normalized_session_id,
        project_id=normalized_project_id,
        update_doc={
            "$unset": {
                "expiry_notice_claimed_at": "",
            }
        },
    )


async def build_and_store_session_summary(
    db: AsyncIOMotorDatabase,
    session_id: str,
    *,
    reason: str = "completed",
    member_name: str = "",
) -> bool:
    """Generate a structured session_summary and persist it on the session doc.

    Called after ``complete_session()`` succeeds.  For normal completions the
    summary includes task analysis and an LLM-inferred ``key_input``.  For
    expired / timed-out sessions the summary is minimal
    (``responded: false``).

    This is deliberately separate from ``complete_session()`` so the
    low-level store function stays fast and side-effect free.

    Args:
        db: MongoDB database instance.
        session_id: The completed session's ID.
        reason: Completion reason (``agenda_complete``, ``timeout``,
            ``superseded``, etc.).
        member_name: Display name of the session's team member.  Falls
            back to ``user_id`` if empty.

    Returns:
        True if the summary was persisted successfully.
    """
    session = await get_session(db, session_id)
    if not session:
        print(f"[cadence] Cannot build summary — session not found: {session_id}")
        return False

    now = datetime.utcnow()
    resolved_name = member_name or session.get("user_id", "Unknown")

    # --- Timeout / non-responsive sessions get a minimal summary --------
    if reason in ("timeout", "superseded", "new_session", "new_cron_run"):
        summary = {
            "member_name": resolved_name,
            "responded": False,
            "expired_reason": reason,
            "generated_at": now,
        }
        return await _persist_session_summary(db, session_id, summary)

    # --- Normal completion: analyse tasks and infer key_input -----------
    tasks = session.get("tasks") or []
    overall_update_input = str(session.get("overall_update_input") or "").strip()
    overall_update_skipped = bool(session.get("overall_update_input_skipped"))

    project_id = str(session.get("project_id") or "").strip()
    status_category_map: Dict[str, Any] = {}
    if project_id:
        project_doc = await db.projects.find_one(
            {"project_id": project_id},
            {"recommendation_status_category_map": 1, "status_category_map": 1},
        )
        candidate_map = (
            (project_doc or {}).get("recommendation_status_category_map")
            if isinstance((project_doc or {}).get("recommendation_status_category_map"), dict)
            else (project_doc or {}).get("status_category_map")
        )
        if isinstance(candidate_map, dict):
            status_category_map = candidate_map

    tasks_completed_list = []
    tasks_updated_list = []
    tasks_in_progress_list = []
    tasks_skipped = 0
    task_comments: List[Dict[str, str]] = []
    notable_comments: List[Dict[str, str]] = []

    for task in tasks:
        ext_id = task.get("external_id", "")
        title = task.get("title", "")
        original_status = task.get("status", "")
        updated_status = task.get("updated_status")
        comment = task.get("comment", "")

        status_changed = (
            updated_status is not None and updated_status != original_status
        )
        has_comment = bool(comment and comment.strip())
        final_status_raw = updated_status if status_changed else original_status
        final_status = str(final_status_raw or "").strip().lower().replace(" ", "_")

        if status_changed and _is_completed_for_summary(
            status_value=updated_status,
            status_category_map=status_category_map,
        ):
            tasks_completed_list.append({"key": ext_id, "title": title})
        elif status_changed:
            tasks_updated_list.append({
                "key": ext_id,
                "title": title,
                "old_status": original_status,
                "new_status": updated_status,
            })
        elif not has_comment:
            tasks_skipped += 1

        if final_status in {"in_progress", "in_review", "blocked"}:
            tasks_in_progress_list.append({
                "key": ext_id,
                "title": title,
                "status": final_status,
            })

        if has_comment:
            normalized_comment = str(comment).strip()
            task_comments.append(
                {
                    "task_key": ext_id,
                    "task_title": title,
                    "comment": normalized_comment,
                }
            )
            lower_comment = normalized_comment.lower()
            tag = "info"
            if any(token in lower_comment for token in ("blocked", "on hold", "stuck")):
                tag = "blocker"
            elif any(token in lower_comment for token in ("dependency", "waiting on", "pending from")):
                tag = "dependency"
            elif any(token in lower_comment for token in ("risk", "concern", "slip", "delay")):
                tag = "risk"
            elif any(token in lower_comment for token in ("need decision", "approve", "approval", "clarify")):
                tag = "decision_needed"
            notable_comments.append(
                {
                    "task_key": ext_id,
                    "task_title": title,
                    "comment": normalized_comment[:220],
                    "tag": tag,
                }
            )

    if overall_update_input:
        task_comments.insert(
            0,
            {
                "task_key": "OVERALL",
                "task_title": "Overall update",
                "comment": overall_update_input,
            },
        )

    # Infer key_input from comments via LLM (called once, result stored)
    key_input = None
    if task_comments:
        try:
            from app.cadence.llm_service import get_llm_service

            llm_svc = get_llm_service()
            if llm_svc:
                key_input = await llm_svc.generate_key_input(
                    resolved_name, task_comments,
                )
        except Exception as exc:
            print(f"[cadence] key_input generation failed: {exc}")

    if overall_update_input:
        overall_note = {
            "task_key": "OVERALL",
            "task_title": "Overall update",
            "comment": overall_update_input[:220],
            "tag": "overall",
        }
        notable_comments.insert(0, overall_note)
        if not key_input:
            key_input = overall_update_input[:160]

    # Backlog analysis
    backlog_action = None
    backlog_items_picked: List[Dict[str, str]] = []
    picked_tasks = session.get("backlog_picked_tasks") or []
    has_backlog = session.get("has_backlog", False)
    backlog_reason = session.get("expired_reason", "")

    if picked_tasks:
        backlog_action = "picked_up"
        backlog_items_picked = [
            {"key": t.get("external_id", ""), "title": t.get("title", "")}
            for t in picked_tasks
        ]
    elif backlog_reason == "backlog_skipped_workload_policy":
        backlog_action = "policy_skipped"
    elif backlog_reason == "backlog_deferred":
        backlog_action = "deferred"
    elif has_backlog:
        backlog_action = "deferred"

    # Response time
    created_at = session.get("created_at")
    completed_at = session.get("completed_at") or now
    response_time_minutes = None
    if created_at:
        delta = (completed_at - created_at).total_seconds()
        response_time_minutes = round(delta / 60, 1)

    summary = {
        "member_name": resolved_name,
        "responded": True,
        "tasks_reviewed": len(tasks),
        "tasks_completed": tasks_completed_list,
        "tasks_in_progress": tasks_in_progress_list,
        "tasks_updated": tasks_updated_list,
        "tasks_skipped": tasks_skipped,
        "notable_comments": notable_comments[:6],
        "key_input": key_input,
        "overall_update_input": overall_update_input if overall_update_input else None,
        "overall_update_input_skipped": overall_update_skipped,
        "backlog_action": backlog_action,
        "backlog_items_picked": backlog_items_picked,
        "backlog_prompt_sent": bool(session.get("backlog_prompt_sent")),
        "backlog_prompt_skipped_by_policy": bool(session.get("backlog_prompt_skipped_by_policy")),
        "backlog_prompt_skip_reason": str(session.get("backlog_prompt_skip_reason") or "").strip() or None,
        "backlog_prompt_active_workload_count": session.get("backlog_prompt_active_workload_count"),
        "backlog_prompt_threshold": session.get("backlog_prompt_threshold"),
        "response_time_minutes": response_time_minutes,
        "generated_at": now,
    }

    success = await _persist_session_summary(db, session_id, summary)
    if success:
        print(
            f"[cadence] Session summary stored | Session: {session_id} | "
            f"Member: {resolved_name} | Completed: {len(tasks_completed_list)}"
        )
        if settings.action_items_enabled and settings.action_items_auto_detect_enabled:
            try:
                from app.action_items.extraction import enqueue_from_cadence_session_summary

                actor_user_id = str(session.get("user_id") or "").strip()
                project_id = str(session.get("project_id") or "").strip()
                if actor_user_id and project_id:
                    enqueue_from_cadence_session_summary(
                        project_id=project_id,
                        session_id=session_id,
                        actor_user_id=actor_user_id,
                        summary=summary,
                        reason=reason,
                    )
            except Exception as exc:
                print(
                    f"[cadence] Action item extraction enqueue failed for session "
                    f"{session_id}: {exc}"
                )
    return success


async def _persist_session_summary(
    db: AsyncIOMotorDatabase,
    session_id: str,
    summary: Dict[str, Any],
) -> bool:
    """Persist the session_summary field on a session document."""
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc={
            "$set": {
                "session_summary": summary,
            }
        },
    )


async def remove_backlog_task_at_index(
    db: AsyncIOMotorDatabase,
    session_id: str,
    backlog_index: int,
) -> Optional[Dict[str, Any]]:
    """Remove one backlog task by index and return the removed task."""
    session = await get_session(db, session_id)
    if not session:
        return None

    backlog_tasks = list(session.get("backlog_tasks") or [])
    if backlog_index < 0 or backlog_index >= len(backlog_tasks):
        return None

    selected = backlog_tasks.pop(backlog_index)
    await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc={
            "$set": {
                "backlog_tasks": backlog_tasks,
                "has_backlog": bool(backlog_tasks),
            }
        },
    )
    return selected


async def add_backlog_picked_task(
    db: AsyncIOMotorDatabase,
    session_id: str,
    task: Dict[str, Any],
) -> bool:
    """Append a backlog task that was picked into persistent session history."""
    if not isinstance(task, dict) or not task:
        return False

    picked_task = {
        "external_id": task.get("external_id", ""),
        "title": task.get("title", ""),
    }
    return await _repo(db).update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        update_doc={"$push": {"backlog_picked_tasks": picked_task}},
    )


# ---------------------------------------------------------------------------
# One-session-per-user guard
# ---------------------------------------------------------------------------

async def has_active_session(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    project_id: Optional[str] = None,
    scope: str = "global",
) -> bool:
    """Return True if user already has an active Cadence session in configured scope."""
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return False
    normalized_scope = str(scope or "global").strip().lower()
    if normalized_scope == "project":
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            return False
        active = await _repo(db).find_active_session(
            entity_type=ENTITY_TYPE,
            source=SOURCE,
            user_id=normalized_user_id,
            project_id=normalized_project_id,
        )
        if active:
            return True
        return False

    now = datetime.utcnow()
    active_rows = await _repo(db).list_session_index(
        query={
            "entity_type": ENTITY_TYPE,
            "source": SOURCE,
            "user_id": normalized_user_id,
            "status": {"$ne": "completed"},
            "expires_at": {"$gt": now},
        },
        limit=1,
    )
    return bool(active_rows)


async def expire_session(
    db: AsyncIOMotorDatabase,
    session_id: str,
) -> bool:
    """Gracefully expire a single session (mark as completed with reason)."""
    return await complete_session(db, session_id, reason="superseded")


async def cancel_active_sessions(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    reason: str = "new_session",
    project_id: Optional[str] = None,
    scope: str = "project",
) -> int:
    """Cancel active sessions for a user in configured scope."""
    repo = _repo(db)
    now = datetime.utcnow()
    normalized_scope = str(scope or "project").strip().lower()
    index_query: Dict[str, Any] = {
        "entity_type": ENTITY_TYPE,
        "source": SOURCE,
        "user_id": user_id,
        "status": {"$ne": "completed"},
        "expires_at": {"$gt": now},
    }
    if normalized_scope == "project" and project_id:
        index_query["project_id"] = project_id

    rows = await repo.list_session_index(query=index_query, limit=5000)
    modified_count = 0
    finalized_session_ids: List[str] = []
    for row in rows:
        sid = str(row.get("session_id") or "").strip()
        pid = str(row.get("project_id") or "").strip()
        if not sid:
            continue
        updated = await repo.update_session_by_id(
            entity_type=ENTITY_TYPE,
            source=SOURCE,
            session_id=sid,
            project_id=pid or None,
            update_doc={
                "$set": {
                    "status": "completed",
                    "completed_at": now,
                    "expired_reason": reason,
                    "outcome.response_status": "no_response_expired",
                    "outcome.terminal": True,
                },
                "$addToSet": {
                    "outcome.reason_codes": str(reason or "new_session").strip().lower() or "new_session",
                },
                "$unset": {
                    "clarification_payload": "",
                },
            },
        )
        if updated:
            modified_count += 1
            finalized_session_ids.append(sid)

    if modified_count:
        scope_label = f"{normalized_scope}:{project_id}" if normalized_scope == "project" else "global"
        print(
            f"[cadence] Cancelled {modified_count} active session(s) for user {user_id} "
            f"(reason={reason}, scope={scope_label})"
        )

    if finalized_session_ids:
        # Build summaries first so daily aggregation has full per-session data.
        for sid in finalized_session_ids:
            try:
                await build_and_store_session_summary(db, sid, reason=reason)
            except Exception as exc:
                print(f"[cadence] Session summary for cancelled {sid} failed: {exc}")

        try:
            from app.cadence.summary import try_generate_for_expired_batch
            generated = await try_generate_for_expired_batch(db, finalized_session_ids)
            if generated:
                print(
                    f"[cadence] Generated {generated} daily summary(ies) "
                    f"after cancel batch (reason={reason})"
                )
        except Exception as exc:
            print(f"[cadence] Daily summary trigger after cancel batch failed: {exc}")
    return modified_count


# ---------------------------------------------------------------------------
# Session expiry / cleanup
# ---------------------------------------------------------------------------

async def expire_stale_sessions(db: AsyncIOMotorDatabase) -> int:
    """Mark all expired Cadence sessions as completed.

    Called from cron at the start of each run to clean up sessions whose
    ``expires_at`` has passed but status was never set to ``completed``.

    After expiring, generates per-session summaries (``responded: false``)
    and triggers daily summary generation if all sessions for a given
    correlation group are now finalised.

    Returns:
        Number of sessions expired.
    """
    now = datetime.utcnow()
    repo = _repo(db)
    index_rows = await repo.list_session_index(
        query={
            "entity_type": ENTITY_TYPE,
            "source": SOURCE,
            "status": {"$ne": "completed"},
            "expires_at": {"$lte": now},
        },
        limit=5000,
    )
    # Source-of-truth fallback: include expired cadence sessions found in
    # project Brain collections even when session_index drifted.
    source_rows = await _list_expired_source_sessions(db=db, now=now, limit=5000)
    indexed_session_ids = {
        str(row.get("session_id") or "").strip()
        for row in index_rows
        if str(row.get("session_id") or "").strip()
    }
    for row in source_rows:
        sid = str(row.get("session_id") or "").strip()
        if not sid or sid in indexed_session_ids:
            continue
        index_rows.append(row)
    modified_count = 0
    expired_session_ids: List[str] = []

    for row in index_rows:
        sid = str(row.get("session_id") or "").strip()
        pid = str(row.get("project_id") or "").strip()
        if not sid:
            continue
        updated = await repo.update_session_by_id(
            entity_type=ENTITY_TYPE,
            source=SOURCE,
            session_id=sid,
            project_id=pid or None,
            update_doc={
                "$set": {
                    "status": "completed",
                    "completed_at": now,
                    "expired_reason": "timeout",
                    "outcome.response_status": "no_response_expired",
                    "outcome.terminal": True,
                },
                "$addToSet": {
                    "outcome.reason_codes": "timeout",
                },
                "$unset": {
                    "clarification_payload": "",
                },
            },
        )
        if updated:
            modified_count += 1
            expired_session_ids.append(sid)

    if modified_count:
        print(f"[cadence] Expired {modified_count} stale session(s)")

    # --- Build per-session summaries for expired sessions ---------------
    for sid in expired_session_ids:
        try:
            await build_and_store_session_summary(
                db, sid, reason="timeout",
            )
        except Exception as exc:
            print(f"[cadence] Session summary for expired {sid} failed: {exc}")
        try:
            from app.cadence.interaction_handler import send_cadence_expiry_notice

            refreshed = await get_session(db, sid)
            if isinstance(refreshed, dict):
                await send_cadence_expiry_notice(db, session=refreshed)
        except Exception as exc:
            print(f"[cadence] Expiry closure notice send failed for {sid}: {exc}")

    # --- Trigger daily summary for completed correlation groups --------
    if expired_session_ids:
        try:
            from app.cadence.summary import try_generate_for_expired_batch
            generated = await try_generate_for_expired_batch(db, expired_session_ids)
            if generated:
                print(f"[cadence] Generated {generated} daily summary(ies) after expiry batch")
        except Exception as exc:
            print(f"[cadence] Daily summary trigger after expiry failed: {exc}")

    return modified_count


async def _list_expired_source_sessions(
    *,
    db: AsyncIOMotorDatabase,
    now: datetime,
    limit: int = 5000,
) -> List[Dict[str, Any]]:
    """List expired non-completed cadence sessions directly from source docs."""
    safe_limit = max(1, min(int(limit or 5000), 5000))
    try:
        project_ids = await db.projects.distinct("project_id")
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    for project_id in project_ids:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            continue
        try:
            collection = get_brain_collection(normalized_project_id)
        except Exception:
            continue
        cursor = collection.find(
            {
                "entity_type": ENTITY_TYPE,
                "source": SOURCE,
                "status": {"$ne": "completed"},
                "expires_at": {"$lte": now},
            },
            {"session_id": 1},
        ).limit(safe_limit)
        docs = await cursor.to_list(length=safe_limit)
        for doc in docs:
            sid = str(doc.get("session_id") or "").strip()
            if not sid:
                continue
            rows.append({"session_id": sid, "project_id": normalized_project_id})
            if len(rows) >= safe_limit:
                return rows
    return rows
