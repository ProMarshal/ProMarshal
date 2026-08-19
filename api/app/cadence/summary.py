"""Project-level daily Cadence summary generation and owner notification.

Assembles a ``cadence_daily_summary`` document once all sessions for a given
cadence run (identified by ``correlation_id``) have been finalised (completed
or expired).  No separate cron job is needed — the generation is triggered
by the existing session lifecycle (``complete_session`` / ``expire_stale_sessions``).

After the summary document is stored, the project owner is optionally
notified via a Slack DM using the same Block Kit formatter patterns as
:pymod:`app.cadence.formatters`.
"""
from datetime import datetime, timezone
import re
from urllib.parse import urlencode
from typing import Any, Dict, List

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.cadence.session_store import (
    ENTITY_TYPE as SESSION_ENTITY_TYPE,
    SOURCE as SESSION_SOURCE,
    get_session,
)
from app.core.database import (
    get_brain_collection,
    ensure_project_cadence_summary_indexes,
)
from app.core.config import settings
from app.core.timezone import get_zoneinfo_or_utc
from app.projects.recommendations.normalized_task_model import derive_status_category
from app.sessions.repository import SessionRepository

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DAILY_SUMMARY_ENTITY_TYPE = "cadence_daily_summary"
_TASK_ID_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


def _repo(db: AsyncIOMotorDatabase) -> SessionRepository:
    return SessionRepository(db=db)


async def _list_source_sessions_for_correlation(
    *,
    project_id: str,
    correlation_id: str,
    limit: int = 5000,
) -> List[Dict[str, Any]]:
    """Load cadence session source docs for one correlation group."""
    safe_limit = max(1, min(int(limit or 5000), 5000))
    collection = get_brain_collection(project_id)
    cursor = collection.find(
        {
            "entity_type": SESSION_ENTITY_TYPE,
            "source": SESSION_SOURCE,
            "project_id": project_id,
            "correlation_id": correlation_id,
        }
    ).limit(safe_limit)
    return await cursor.to_list(length=safe_limit)


def _normalize_status_token(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _status_category_for_summary(
    *,
    status_value: Any,
    status_category_map: Dict[str, Any],
) -> str:
    status_token = _normalize_status_token(status_value)
    return derive_status_category(
        status=status_token,
        item={"status": str(status_value or "")},
        status_category_map=status_category_map if isinstance(status_category_map, dict) else None,
    )


def _display_status(value: Any) -> str:
    token = _normalize_status_token(value)
    if not token:
        return ""
    if token == "todo":
        return "To Do"
    if token == "in_progress":
        return "In Progress"
    if token == "in_review":
        return "In Review"
    if token == "done":
        return "Done"
    if token == "blocked":
        return "Blocked"
    return token.replace("_", " ").title()


def _task_card(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": str(raw.get("title") or raw.get("key") or "Untitled task").strip(),
        "key": str(raw.get("key") or "").strip(),
        "status": _display_status(raw.get("status")),
    }


def _append_attention_point(points: List[str], text: str) -> None:
    normalized = str(text or "").strip()
    if not normalized:
        return
    if normalized not in points:
        points.append(normalized)


def _sanitize_attention_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    cleaned = _TASK_ID_PATTERN.sub("this task", cleaned)
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.replace("this task this task", "this task")
    return cleaned.strip()


def _sanitize_attention_points(points: List[str], *, max_points: int = 6) -> List[str]:
    sanitized: List[str] = []
    for raw in points:
        cleaned = _sanitize_attention_text(str(raw or ""))
        if not cleaned:
            continue
        if cleaned in sanitized:
            continue
        sanitized.append(cleaned)
        if len(sanitized) >= max_points:
            break
    return sanitized


def _normalize_reason_label(reason: str) -> str:
    token = str(reason or "").strip().lower()
    if token in {"timeout", "expired"}:
        return "No response before session timeout"
    if token in {"superseded", "new_session", "new_cron_run"}:
        return "Session replaced by a newer cadence run"
    if token in {"backlog_deferred"}:
        return "Backlog pickup deferred"
    if token in {"no_response", ""}:
        return "No response captured in this run"
    return token.replace("_", " ").strip().capitalize()


def _as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _project_local_date_from_utc(timestamp: datetime, project_timezone: str) -> str:
    tz = get_zoneinfo_or_utc(
        str(project_timezone or "UTC").strip() or "UTC",
        context="cadence_summary_project_local_date",
    )
    return _as_utc_datetime(timestamp).astimezone(tz).strftime("%Y-%m-%d")


def _build_active_member_roster(project_doc: Dict[str, Any]) -> List[Dict[str, str]]:
    members = (project_doc or {}).get("members") or []
    roster: List[Dict[str, str]] = []
    seen_ids: set[str] = set()

    for member in members:
        if not isinstance(member, dict):
            continue
        if str(member.get("status") or "").strip().lower() != "active":
            continue
        user_id = str(member.get("user_id") or "").strip()
        if not user_id or user_id in seen_ids:
            continue
        seen_ids.add(user_id)
        display_name = str(
            member.get("name")
            or member.get("email")
            or user_id
        ).strip()
        roster.append({"user_id": user_id, "member": display_name})

    return roster


# ---------------------------------------------------------------------------
# Trigger check — called after every session finalisation
# ---------------------------------------------------------------------------

async def try_generate_daily_summary(
    db: AsyncIOMotorDatabase,
    session_id: str,
) -> bool:
    """Check whether all sessions for the run are done; if so, generate summary.

    Call this after ``complete_session()`` or ``expire_stale_sessions()``
    succeeds.  It reads the ``correlation_id`` and ``project_id`` from the
    session, then queries the session index to determine whether any peer
    sessions are still active.  If none remain, it kicks off
    :func:`generate_daily_summary`.

    Args:
        db: MongoDB database instance.
        session_id: The session that was just finalised.

    Returns:
        True if a daily summary was generated, False otherwise.
    """
    session = await get_session(db, session_id)
    if not session:
        return False

    correlation_id = session.get("correlation_id")
    project_id = session.get("project_id")
    if not correlation_id or not project_id:
        return False

    # Check for existing summary first (idempotency guard)
    collection = get_brain_collection(project_id)
    existing = await collection.find_one({
        "entity_type": DAILY_SUMMARY_ENTITY_TYPE,
        "correlation_id": correlation_id,
    })
    if existing:
        return False  # already generated

    repo = _repo(db)
    index_rows = await repo.list_session_index(
        query={
            "entity_type": SESSION_ENTITY_TYPE,
            "source": SESSION_SOURCE,
            "project_id": project_id,
            "correlation_id": correlation_id,
        },
        limit=5000,
    )
    source_sessions = await _list_source_sessions_for_correlation(
        project_id=str(project_id),
        correlation_id=str(correlation_id),
        limit=5000,
    )
    if not source_sessions and not index_rows:
        return False

    # Source docs are canonical. If any source session remains non-terminal,
    # do not generate daily summary yet.
    pending_source = [
        session for session in source_sessions
        if str(session.get("status") or "").strip().lower() != "completed"
    ]
    if pending_source:
        return False

    return await generate_daily_summary(db, correlation_id, project_id)


async def try_generate_for_expired_batch(
    db: AsyncIOMotorDatabase,
    expired_session_ids: List[str],
) -> int:
    """Trigger check for each unique correlation group in a batch of expired sessions.

    Called from ``expire_stale_sessions()`` after processing a batch.

    Returns:
        Number of daily summaries successfully generated.
    """
    generated = 0
    seen_correlations: set = set()

    for sid in expired_session_ids:
        session = await get_session(db, sid)
        if not session:
            continue
        cid = session.get("correlation_id")
        if not cid or cid in seen_correlations:
            continue
        seen_correlations.add(cid)

        if await try_generate_daily_summary(db, sid):
            generated += 1

    return generated


# ---------------------------------------------------------------------------
# Daily summary assembly
# ---------------------------------------------------------------------------

async def generate_daily_summary(
    db: AsyncIOMotorDatabase,
    correlation_id: str,
    project_id: str,
) -> bool:
    """Assemble and store a cadence_daily_summary document.

    Reads all finalised sessions for the correlation group, extracts each
    session's pre-computed ``session_summary``, and aggregates them into
    the project-level daily summary stored in the Brain collection.

    Args:
        db: MongoDB database instance.
        correlation_id: Shared cron-run ID across all related sessions.
        project_id: The project these sessions belong to.

    Returns:
        True if the summary was successfully stored.
    """
    # Source cadence sessions are canonical.
    sessions = await _list_source_sessions_for_correlation(
        project_id=project_id,
        correlation_id=correlation_id,
        limit=5000,
    )

    if not sessions:
        print(f"[cadence/summary] No source sessions found for correlation: {correlation_id}")
        return False

    roster_by_user: Dict[str, str] = {}

    # Build the summary sections
    responded_names: List[str] = []
    non_responders: List[Dict[str, str]] = []
    member_updates: List[Dict[str, Any]] = []
    pm_attention_points: List[str] = []
    attendance: List[Dict[str, Any]] = []

    backlog_deferred_count = 0
    backlog_prompt_asked_count = 0
    session_by_user: Dict[str, Dict[str, Any]] = {}
    responded_user_ids: set[str] = set()

    project_doc = await db.projects.find_one(
        {"project_id": project_id},
        {"timezone": 1, "recommendation_status_category_map": 1, "status_category_map": 1},
    )
    status_category_map: Dict[str, Any] = {}
    candidate_status_map = (
        (project_doc or {}).get("recommendation_status_category_map")
        if isinstance((project_doc or {}).get("recommendation_status_category_map"), dict)
        else (project_doc or {}).get("status_category_map")
    )
    if isinstance(candidate_status_map, dict):
        status_category_map = candidate_status_map

    for session in sessions:
        summary = session.get("session_summary") or {}
        member_name = summary.get("member_name") or session.get("user_id", "Unknown")
        session_user_id = str(session.get("user_id") or "").strip()
        if session_user_id and session_user_id not in roster_by_user:
            roster_by_user[session_user_id] = str(
                session.get("member_name")
                or session.get("member_email")
                or member_name
                or session_user_id
            ).strip()
        if session_user_id:
            session_by_user[session_user_id] = session
        outcome = session.get("outcome") or {}
        reason_codes = {
            str(item).strip().lower()
            for item in (outcome.get("reason_codes") or [])
            if str(item).strip()
        }
        if "no_assigned_tasks" in reason_codes:
            _append_attention_point(pm_attention_points, f"{member_name} has no active tasks assigned.")
        if "task_provider_mapping_missing" in reason_codes:
            provider_label = str(session.get("task_provider") or "task provider").strip() or "task provider"
            _append_attention_point(
                pm_attention_points,
                f"{member_name} is active but {provider_label} mapping is missing; sync identity mapping.",
            )
        if "slack_identity_missing" in reason_codes:
            _append_attention_point(
                pm_attention_points,
                f"{member_name} could not be reached in Slack due to missing Slack identity mapping.",
            )
        if "dm_delivery_failed" in reason_codes:
            _append_attention_point(
                pm_attention_points,
                f"{member_name} cadence DM delivery failed.",
            )
        if "existing_active_session_conflict" in reason_codes:
            _append_attention_point(
                pm_attention_points,
                (
                    f"{member_name} had an existing active cadence session during this run; "
                    "follow up to confirm unresolved update."
                ),
            )

        if summary.get("responded", False):
            if session_user_id:
                responded_user_ids.add(session_user_id)
            responded_names.append(str(member_name))

            completed_raw = summary.get("tasks_completed") or []
            in_progress_raw = summary.get("tasks_in_progress") or []
            backlog_picked_raw = summary.get("backlog_items_picked") or []
            updated_raw = summary.get("tasks_updated") or []
            notable_comments_raw = summary.get("notable_comments") or []
            overall_update = str(summary.get("overall_update_input") or "").strip()

            # Fallback for older session summaries that don't carry explicit in-progress bucket.
            if (not in_progress_raw or not completed_raw) and updated_raw:
                for item in updated_raw:
                    new_status_value = item.get("new_status")
                    new_status = _normalize_status_token(new_status_value)
                    category = _status_category_for_summary(
                        status_value=new_status_value,
                        status_category_map=status_category_map,
                    )
                    if category == "done":
                        completed_raw.append(
                            {
                                "key": item.get("key"),
                                "title": item.get("title"),
                                "status": new_status,
                            }
                        )
                    elif category in {"in_progress", "blocked"}:
                        in_progress_raw.append(
                            {
                                "key": item.get("key"),
                                "title": item.get("title"),
                                "status": new_status,
                            }
                        )

            closed_tasks = [_task_card(item) for item in completed_raw if isinstance(item, dict)]
            in_progress_tasks = [_task_card(item) for item in in_progress_raw if isinstance(item, dict)]
            backlog_pickups = [_task_card(item) for item in backlog_picked_raw if isinstance(item, dict)]

            notable_comments: List[Dict[str, str]] = []
            for item in notable_comments_raw:
                if not isinstance(item, dict):
                    continue
                comment_text = str(item.get("comment") or "").strip()
                if not comment_text:
                    continue
                tag = str(item.get("tag") or "info").strip().lower() or "info"
                task_title = str(item.get("task_title") or item.get("task_key") or "").strip()
                task_key = str(item.get("task_key") or "").strip()
                notable_comments.append(
                    {
                        "task_title": task_title,
                        "task_key": task_key,
                        "comment": comment_text,
                        "tag": tag,
                    }
                )
                if tag in {"blocker", "dependency", "risk", "decision_needed"}:
                    tag_label = tag.replace("_", " ")
                    focus_label = task_title or "a task"
                    _append_attention_point(
                        pm_attention_points,
                        f"{member_name} flagged {tag_label} on {focus_label}: {comment_text}",
                    )

            backlog_action = str(summary.get("backlog_action") or "").strip().lower() or None
            backlog_prompt_sent = bool(summary.get("backlog_prompt_sent"))
            backlog_remaining_count = len(session.get("backlog_tasks") or [])
            if backlog_action == "deferred":
                backlog_deferred_count += 1
            if backlog_prompt_sent or backlog_action in {"picked", "deferred"}:
                backlog_prompt_asked_count += 1

            member_updates.append(
                {
                    "member": member_name,
                    "response_time_minutes": summary.get("response_time_minutes"),
                    "closed_tasks": closed_tasks,
                    "in_progress_tasks": in_progress_tasks,
                    "backlog_pickups": backlog_pickups,
                    "backlog_action": backlog_action,
                    "backlog_remaining_count": backlog_remaining_count,
                    "backlog_prompt_sent": backlog_prompt_sent,
                    "notable_comments": notable_comments,
                    "key_input": summary.get("key_input"),
                    "overall_update": overall_update or None,
                }
            )

            if overall_update:
                _append_attention_point(
                    pm_attention_points,
                    f"{member_name} important note: {overall_update}",
                )
            active_task_count = len(in_progress_tasks)
            if (
                backlog_remaining_count > 0
                and backlog_action != "picked"
                and active_task_count <= 1
            ):
                task_hint = ""
                if active_task_count == 1 and in_progress_tasks:
                    first_title = str(in_progress_tasks[0].get("title") or "").strip()
                    if first_title:
                        task_hint = f" ({first_title})"
                _append_attention_point(
                    pm_attention_points,
                    (
                        f"{member_name} has low active workload ({active_task_count} task(s){task_hint}) "
                        "and did not take additional work; align next priority."
                    ),
                )
        else:
            reason = _normalize_reason_label(str(summary.get("expired_reason") or "no_response"))
            _append_attention_point(
                pm_attention_points,
                f"{member_name} did not respond: {reason}.",
            )

    for user_id, roster_name in roster_by_user.items():
        member_name = str(roster_name or user_id).strip()
        attended = user_id in responded_user_ids
        attendance.append({"member": member_name, "attended": attended})
        if attended:
            continue
        related_session = session_by_user.get(user_id) or {}
        related_summary = related_session.get("session_summary") or {}
        reason = _normalize_reason_label(str(related_summary.get("expired_reason") or "no_response"))
        non_responders.append({"member": member_name, "reason": reason})
    responded_names = [entry["member"] for entry in attendance if entry.get("attended")]

    total_members = len(attendance) if attendance else len(responded_names) + len(non_responders)
    rate = round(len(responded_names) / total_members, 2) if total_members else 0

    # Build overall summary line
    overall_parts = [f"{len(responded_names)} of {total_members} members responded"]
    if backlog_deferred_count:
        overall_parts.append(f"{backlog_deferred_count} member(s) deferred backlog pickup")
    if non_responders:
        names = ", ".join(item.get("member", "Unknown") for item in non_responders)
        overall_parts.append(f"follow-up needed with {names}")
    overall_summary = ". ".join(part.rstrip(".") for part in overall_parts if str(part).strip()).strip()
    if overall_summary:
        overall_summary = overall_summary + "."

    if non_responders:
        names = ", ".join(item.get("member", "Unknown") for item in non_responders)
        _append_attention_point(pm_attention_points, f"Follow up with non-responders: {names}.")
        _append_attention_point(
            pm_attention_points,
            "At least one member missed cadence; confirm workload and assign the next priority.",
        )
    if backlog_deferred_count:
        _append_attention_point(
            pm_attention_points,
            f"{backlog_deferred_count} member(s) deferred backlog work; validate priority and capacity.",
        )

    # LLM-based PM attention synthesis with deterministic fallback.
    try:
        from app.cadence.llm_service import get_llm_service

        llm_service = get_llm_service()
        if llm_service:
            synthesized_points = await llm_service.generate_pm_attention_points(
                overall_summary=overall_summary,
                non_responders=[item.get("member", "Unknown") for item in non_responders],
                member_updates=member_updates,
                deterministic_points=pm_attention_points,
                include_backlog_context=bool(backlog_prompt_asked_count > 0),
                max_points=6,
            )
            if synthesized_points:
                pm_attention_points = synthesized_points
    except Exception as exc:
        print(f"[cadence/summary] PM attention LLM synthesis failed: {exc}")

    pm_attention_points = _sanitize_attention_points(pm_attention_points, max_points=6)

    project_timezone = str((project_doc or {}).get("timezone") or "UTC").strip() or "UTC"

    # Determine date from the earliest session in project-local timezone.
    created_values = [
        s.get("created_at")
        for s in sessions
        if isinstance(s.get("created_at"), datetime)
    ]
    earliest_created = min(created_values) if created_values else datetime.utcnow()
    date_str = _project_local_date_from_utc(earliest_created, project_timezone)

    now = datetime.utcnow()
    summary_doc = {
        "entity_type": DAILY_SUMMARY_ENTITY_TYPE,
        "project_id": project_id,
        "correlation_id": correlation_id,
        "date": date_str,
        "project_timezone": project_timezone,
        "date_basis": "project_local",
        "generated_at": now,
        "owner_notified": False,
        "participation": {
            "responded": responded_names,
            "not_responded": [item.get("member", "Unknown") for item in non_responders],
            "total": total_members,
            "rate": rate,
        },
        "attendance": attendance,
        "overall_summary": overall_summary,
        "pm_attention_points": pm_attention_points[:6],
        "member_updates": member_updates,
        "non_responders": non_responders,
    }

    # Store in Brain collection (atomic idempotency guard for race safety)
    await ensure_project_cadence_summary_indexes(project_id)
    collection = get_brain_collection(project_id)
    upsert_result = await collection.update_one(
        {
            "entity_type": DAILY_SUMMARY_ENTITY_TYPE,
            "correlation_id": correlation_id,
        },
        {"$setOnInsert": summary_doc},
        upsert=True,
    )
    if not upsert_result.upserted_id:
        return False
    summary_doc["_id"] = upsert_result.upserted_id

    print(
        f"[cadence/summary] Daily summary stored | Project: {project_id} | "
        f"Correlation: {correlation_id} | Responded: {len(responded_names)}/{total_members}"
    )

    # Notify owner via DM
    await _notify_project_owner(db, project_id, summary_doc)

    # Clean up session_index entries for this correlation group - no longer needed
    try:
        repo = _repo(db)
        deleted = await repo._session_index_collection().delete_many({
            "entity_type": SESSION_ENTITY_TYPE,
            "source": SESSION_SOURCE,
            "project_id": project_id,
            "correlation_id": correlation_id,
        })
        if deleted.deleted_count:
            print(
                f"[cadence/summary] Cleaned {deleted.deleted_count} session_index entries | "
                f"Correlation: {correlation_id}"
            )
    except Exception as exc:
        print(f"[cadence/summary] session_index cleanup failed (non-fatal): {exc}")

    return True


# ---------------------------------------------------------------------------
# Owner DM notification
# ---------------------------------------------------------------------------

async def _notify_project_owner(
    db: AsyncIOMotorDatabase,
    project_id: str,
    summary_doc: Dict[str, Any],
) -> None:
    """DM the daily summary to the project owner via Slack.

    Resolves the project owner, builds a Block Kit message mirroring
    the dashboard display, and sends it as a DM.  Marks
    ``owner_notified = true`` on the summary doc after success.
    """
    correlation_id = str(summary_doc.get("correlation_id") or "").strip() or "unknown"
    try:
        from app.cadence.slack_identity import resolve_slack_user_id_for_member
        from app.integrations.slack.client import SlackClient
        from app.core.security import encryption_service

        project = await db.projects.find_one({"project_id": project_id})
        if not project:
            print(
                "[cadence/summary] Owner DM skipped: project not found | "
                f"project={project_id} correlation={correlation_id}"
            )
            return

        # Find the owner member
        members = project.get("members", [])
        owner = next(
            (m for m in members if m.get("role") == "owner"),
            None,
        )
        if not owner:
            print(
                "[cadence/summary] Owner DM skipped: owner member not found | "
                f"project={project_id} correlation={correlation_id}"
            )
            return

        # Resolve Slack credentials
        slack_integration = (project.get("integrations") or {}).get("slack")
        if not slack_integration or not slack_integration.get("access_token"):
            print(
                "[cadence/summary] Owner DM skipped: Slack integration/token missing | "
                f"project={project_id} correlation={correlation_id}"
            )
            return

        slack_token = encryption_service.decrypt(slack_integration["access_token"])
        slack_client = SlackClient(slack_token)

        owner_slack_id = await resolve_slack_user_id_for_member(
            db=db, project=project, member=owner, slack_client=slack_client,
        )
        if not owner_slack_id:
            print(
                "[cadence/summary] Owner DM skipped: cannot resolve owner Slack user | "
                f"project={project_id} correlation={correlation_id} owner={owner.get('email')}"
            )
            return

        frontend_base = str(getattr(settings, "frontend_url", "") or "").strip().rstrip("/")
        project_mongo_id = str(project.get("_id") or "").strip()
        dashboard_url = ""
        if frontend_base and project_mongo_id:
            query = urlencode(
                {
                    "nav": "Pulse",
                    "tab": "Cadence Summary",
                    "projectId": project_mongo_id,
                }
            )
            dashboard_url = f"{frontend_base}/projects?{query}"

        # Build and send DM
        blocks = _build_summary_dm_blocks(summary_doc, dashboard_url=dashboard_url)
        await slack_client.send_message(owner_slack_id, blocks=blocks)

        # Mark as notified
        collection = get_brain_collection(project_id)
        update_filter: Dict[str, Any]
        if summary_doc.get("_id") is not None:
            update_filter = {"_id": summary_doc["_id"]}
        else:
            update_filter = {
                "entity_type": DAILY_SUMMARY_ENTITY_TYPE,
                "correlation_id": summary_doc.get("correlation_id"),
            }
        await collection.update_one(
            update_filter,
            {"$set": {"owner_notified": True}},
        )

        print(
            f"[cadence/summary] Owner DM sent | Project: {project_id} | "
            f"Correlation: {correlation_id} | Owner: {owner.get('email')}"
        )
    except Exception as exc:
        print(
            "[cadence/summary] Owner DM failed | "
            f"project={project_id} correlation={correlation_id} error={exc}"
        )


def _build_summary_dm_blocks(summary_doc: Dict[str, Any], dashboard_url: str = "") -> List[Dict[str, Any]]:
    """Build Slack Block Kit blocks for the daily cadence summary DM.

    Mirrors the dashboard structure: participation, overall summary, and PM attention.
    """
    date_str = summary_doc.get("date", "today")
    participation = summary_doc.get("participation") or {}
    attendance = summary_doc.get("attendance") or []
    overall = str(summary_doc.get("overall_summary") or "").strip()
    pm_attention_points = summary_doc.get("pm_attention_points") or []
    non_responders = summary_doc.get("non_responders") or []

    blocks: List[Dict[str, Any]] = []

    # Header
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"Cadence Summary - {date_str}",
        },
    })

    # -- Participation ---------------------------------------------------
    responded = participation.get("responded") or []
    not_responded = [item.get("member", "Unknown") for item in non_responders]

    participation_lines: List[str] = []
    attendee_names: List[str] = []
    absentee_names: List[str] = []
    if attendance:
        for item in attendance[:20]:
            member = str(item.get("member") or "Unknown").strip()
            attended = bool(item.get("attended"))
            if attended:
                attendee_names.append(member)
            else:
                absentee_names.append(member)
        if attendee_names:
            participation_lines.append(f"Attendees: {', '.join(attendee_names)}")
        if absentee_names:
            participation_lines.append(f"Absentees: {', '.join(absentee_names)}")
    else:
        if responded:
            participation_lines.append(f"Attendees: {', '.join(responded)}")
        if not_responded:
            participation_lines.append(f"Absentees: {', '.join(not_responded)}")
    if not participation_lines:
        participation_lines.append("No participation data")

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Participation*\n" + "\n".join(participation_lines),
        },
    })

    # -- Overall ---------------------------------------------------------
    if overall:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Overall Cadence Summary*\n{overall}",
            },
        })

    # -- PM Attention ----------------------------------------------------
    if pm_attention_points:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*PM Attention Needed*\n" + "\n".join(
                    f"- {str(point)}" for point in pm_attention_points[:5]
                ),
            },
        })

    blocks.append({"type": "divider"})
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"For detailed member updates, view <{dashboard_url}|Cadence Summary in ProMarshal>."
                    if dashboard_url
                    else "For detailed member updates, check Cadence Summary in the ProMarshal dashboard."
                ),
            },
        }
    )

    return blocks
