"""Jira webhook endpoints to receive task updates"""

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional
import json
from fastapi import APIRouter, Depends

from app.core.config import settings
from app.core.queue_backends.dramatiq_backend import enqueue as dramatiq_enqueue
from app.core.queue_runtime import QUEUE_WORKITEM_EVENTS
from app.core.database import get_database, get_brain_collection
from app.domain.work_items.types import canonicalize_work_item_payload
from app.integrations.base.webhook_registry import require_webhook_auth
from app.integrations.jira.normalizer import JiraNormalizer
from app.integrations.slack.assignment_notifications import (
    send_work_item_assignment_notification,
)
from app.projects.pm_board_cache import invalidate_pm_board_summary_cache
from app.projects.recommendations.recommendation_orchestrator import mark_project_task_dirty
from app.action_items.extraction import enqueue_from_task_comment_mutation

router = APIRouter(prefix="/api/jira/webhooks", tags=["jira-webhooks"])


def _resolve_task_key(issue: dict, normalized_task: Optional[dict] = None) -> str:
    """Resolve task key from webhook issue payload with normalized fallback."""
    direct = str((issue or {}).get("key") or "").strip()
    if direct:
        return direct
    if isinstance(normalized_task, dict):
        fallback = str(normalized_task.get("external_id") or "").strip()
        if fallback:
            return fallback
    return ""


def _mark_task_dirty_best_effort(custom_project_id: str, task_key: str) -> None:
    """Best-effort dirty marker write; must not fail webhook processing."""
    clean_project = str(custom_project_id or "").strip()
    clean_task = str(task_key or "").strip()
    if not clean_project or not clean_task:
        return
    try:
        mark_project_task_dirty(clean_project, clean_task)
    except Exception as dirty_exc:
        print(
            "[Recommendation] Dirty marker failed during Jira webhook "
            f"(project_id={clean_project}, task_key={clean_task}): {dirty_exc}"
        )


def _trigger_pm_summary_recompute_best_effort(*, project_id: str, custom_project_id: str, trigger: str) -> None:
    clean_project_id = str(project_id or "").strip()
    clean_custom_project_id = str(custom_project_id or "").strip()
    clean_trigger = str(trigger or "").strip().lower() or "jira_webhook"
    if not clean_project_id or not clean_custom_project_id:
        return
    try:
        from app.projects.router import trigger_pm_board_summary_recompute

        enqueue_result = trigger_pm_board_summary_recompute(
            project_id=clean_project_id,
            custom_project_id=clean_custom_project_id,
            trigger=clean_trigger,
        )
        print(
            "[PM Summary] Jira webhook recompute enqueue: "
            f"project_id={clean_custom_project_id} accepted={bool(enqueue_result.get('accepted'))} "
            f"reason={enqueue_result.get('reason') or 'n/a'}"
        )
    except Exception as pm_exc:
        print(
            "[PM Summary] Jira webhook recompute enqueue failed: "
            f"project_id={clean_custom_project_id} error={pm_exc}"
        )


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _is_jira_comment_create_event(payload: Dict[str, Any]) -> bool:
    webhook_event = _normalize_text((payload or {}).get("webhookEvent")).lower()
    issue_event_type = _normalize_text((payload or {}).get("issue_event_type_name")).lower()
    if webhook_event in {"comment_created", "jira:comment_created"}:
        return True
    if issue_event_type == "issue_commented":
        return True
    return False


def _extract_jira_comment_text(payload: Dict[str, Any]) -> str:
    comment = (payload or {}).get("comment") or {}
    if isinstance(comment, dict):
        return _normalize_text(comment.get("body"))
    return ""


def _extract_jira_comment_author(payload: Dict[str, Any]) -> Dict[str, str]:
    comment = (payload or {}).get("comment") or {}
    author = (comment or {}).get("author") or {}
    if not isinstance(author, dict):
        return {"account_id": "", "email": "", "name": ""}
    return {
        "account_id": _normalize_text(author.get("accountId")),
        "email": _normalize_text(author.get("emailAddress")).lower(),
        "name": _normalize_text(author.get("displayName")),
    }


def _resolve_actor_user_id_from_jira_author(project: Dict[str, Any], author: Dict[str, str]) -> Optional[str]:
    account_id = _normalize_text((author or {}).get("account_id"))
    email = _normalize_text((author or {}).get("email")).lower()
    name = _normalize_text((author or {}).get("name")).lower()
    members = project.get("members") or []

    # First pass: strongest identifiers (Jira account ID and email).
    for member in members:
        if not isinstance(member, dict):
            continue
        if _normalize_text(member.get("status")).lower() != "active":
            continue
        user_id = _normalize_text(member.get("user_id"))
        if not user_id:
            continue
        integration_ids = member.get("integration_ids") or {}
        member_jira_id = _normalize_text(
            integration_ids.get("jira_account_id")
            or integration_ids.get("jira_user_id")
            or member.get("jira_account_id")
        )
        member_email = _normalize_text(member.get("email")).lower()
        if account_id and member_jira_id and member_jira_id == account_id:
            return user_id
        if email and member_email and member_email == email:
            return user_id

    # Second pass: strict name fallback.
    for member in members:
        if not isinstance(member, dict):
            continue
        if _normalize_text(member.get("status")).lower() != "active":
            continue
        user_id = _normalize_text(member.get("user_id"))
        member_name = _normalize_text(member.get("name")).lower()
        if name and member_name and member_name == name and user_id:
            return user_id

    return None


def _build_jira_webhook_comment_source_event_key(payload: Dict[str, Any], issue_key: str) -> str:
    comment = (payload or {}).get("comment") or {}
    comment_id = _normalize_text((comment or {}).get("id"))
    if comment_id and issue_key:
        return f"jira:comment:{issue_key}:{comment_id}"
    return ""


@router.post("/{project_id}/task-updated")
async def receive_jira_webhook(
    project_id: str,
    raw_body: bytes = Depends(require_webhook_auth("jira")),
):
    """
    Receive webhook from Jira when tasks are updated

    This endpoint is called by Jira automatically when:
    - A task is created
    - A task is updated
    - A task is deleted

    Queues the payload for background processing to respond quickly
    """
    try:
        payload = json.loads(raw_body.decode("utf-8"))
        issue_key = payload.get("issue", {}).get("key", "Unknown")

        enqueue_result = dramatiq_enqueue(
            queue_name=QUEUE_WORKITEM_EVENTS,
            actor_name="process_jira_webhook",
            kwargs={"payload": payload, "project_id": project_id},
        )
        if bool(enqueue_result.get("accepted")):
            print(
                "Jira webhook queued (Dramatiq): "
                f"{enqueue_result.get('job_id') or 'none'} (Task: {issue_key})"
            )
            return {"ok": True}

        print(
            "Failed to queue Jira webhook with Dramatiq; processing inline: "
            f"{enqueue_result.get('reason') or 'unknown'}"
        )
        await process_jira_webhook(payload, project_id=project_id)

        # Return immediately
        return {"ok": True}

    except Exception as e:
        print(f"Error receiving webhook: {str(e)}")
        return {"ok": False, "error": str(e)}


async def process_jira_webhook(payload: dict, *, project_id: str):
    """
    Process Jira webhook payload (async version)

    Args:
        payload: Jira webhook payload
        project_id: ProMarshal custom project id (`proj-xxxx-yyyy`)
    """
    try:
        print(f"\n{'='*60}")
        print(f"Processing Jira Webhook")
        print(f"{'='*60}")

        webhook_event = payload.get("webhookEvent")
        issue_event_type = payload.get("issue_event_type_name")

        print(f"Event: {webhook_event}")
        print(f"Issue Event: {issue_event_type}")

        # Extract issue data
        issue = payload.get("issue", {})
        if not issue:
            print("No issue data in webhook")
            return

        issue_key = _resolve_task_key(issue)
        print(f"Task Key: {issue_key}")

        db = get_database()
        project = await db.projects.find_one(
            {
                "project_id": str(project_id or "").strip(),
                "integrations.jira.provider": "jira",
            }
        )

        if not project:
            print(f"No ProMarshal project found for webhook project_id={project_id}")
            return

        mongo_project_id = project["_id"]
        custom_project_id = project.get("project_id", str(mongo_project_id))
        project_name = project.get("project_name", "Unknown")
        project_site_url = project.get("integrations", {}).get("jira", {}).get("site_url")
        project_cloud_id = project.get("integrations", {}).get("jira", {}).get("cloud_id")

        print(f"Updating project: {project_name} (ID: {custom_project_id})")

        if webhook_event == "jira:issue_deleted":
            brain_collection = get_brain_collection(custom_project_id)
            result = await brain_collection.delete_one({
                "integration_type": "jira",
                "external_id": issue_key
            })
            if result.deleted_count > 0:
                print(f"Deleted task {issue_key} from brain/{custom_project_id}")
            else:
                print(f"Task {issue_key} not found in brain/{custom_project_id}")
            _mark_task_dirty_best_effort(custom_project_id, issue_key)
            invalidate_pm_board_summary_cache(custom_project_id)
            _trigger_pm_summary_recompute_best_effort(
                project_id=str(mongo_project_id),
                custom_project_id=custom_project_id,
                trigger="jira_webhook_issue_deleted",
            )
        else:
            normalizer = JiraNormalizer(cloud_id=project_cloud_id, site_url=project_site_url)
            normalized_task = canonicalize_work_item_payload(normalizer.normalize_task(issue))
            issue_key = _resolve_task_key(issue, normalized_task)
            if issue_key:
                normalized_task["external_id"] = issue_key

            # Determine if this webhook is a status change event
            changelog_items = payload.get("changelog", {}).get("items", [])
            status_changed = any(item.get("field") == "status" for item in changelog_items)

            if status_changed:
                # Status changed in this event - record the time
                normalized_task["status_changed_at"] = datetime.utcnow()
            else:
                # Not a status change - remove from $set to preserve existing value in DB
                normalized_task.pop("status_changed_at", None)

            brain_collection = get_brain_collection(custom_project_id)
            existing_task = await brain_collection.find_one(
                {"integration_type": "jira", "external_id": issue_key}
            )
            result = await brain_collection.update_one(
                {"integration_type": "jira", "external_id": issue_key},
                {"$set": normalized_task},
                upsert=True
            )

            if result.upserted_id:
                print(f"Created new task {issue_key} in brain/{custom_project_id}")
            elif result.modified_count > 0:
                print(f"Updated existing task {issue_key} in brain/{custom_project_id}")
            else:
                print(f"Task {issue_key} already up-to-date in brain/{custom_project_id}")

            _mark_task_dirty_best_effort(custom_project_id, issue_key)
            invalidate_pm_board_summary_cache(custom_project_id)
            _trigger_pm_summary_recompute_best_effort(
                project_id=str(mongo_project_id),
                custom_project_id=custom_project_id,
                trigger="jira_webhook_issue_upsert",
            )
            try:
                await send_work_item_assignment_notification(
                    db=db,
                    project=project,
                    task=normalized_task,
                    old_task=existing_task if isinstance(existing_task, dict) else None,
                    actor_hint=None,
                )
            except Exception as notify_exc:
                print(
                    "Work-item assignment notification failed during Jira webhook: "
                    f"project_id={custom_project_id} task={issue_key} error={notify_exc}"
                )
            print(f"   Status: {normalized_task['status']}")
            print(f"   Assignee: {normalized_task['assignee_name']} ({normalized_task['assignee_email']})")

            if (
                settings.action_items_enabled
                and settings.action_items_auto_detect_enabled
                and _is_jira_comment_create_event(payload)
                and issue_key
            ):
                comment_text = _extract_jira_comment_text(payload)
                if comment_text:
                    author = _extract_jira_comment_author(payload)
                    actor_user_id = _resolve_actor_user_id_from_jira_author(project, author)
                    source_event_key = _build_jira_webhook_comment_source_event_key(payload, issue_key)
                    mutation_timestamp = _normalize_text(
                        ((payload or {}).get("comment") or {}).get("updated")
                        or ((payload or {}).get("comment") or {}).get("created")
                        or ((issue or {}).get("fields") or {}).get("updated")
                    )
                    enqueue_result = enqueue_from_task_comment_mutation(
                        project_id=custom_project_id,
                        task_key=issue_key,
                        raw_comment_text=comment_text,
                        posted_comment_text=comment_text,
                        actor_user_id=actor_user_id,
                        updated_by_user_id=actor_user_id,
                        provider="jira",
                        source="jira_webhook_comment_create",
                        source_event_key=source_event_key,
                        mutation_timestamp=mutation_timestamp,
                    )
                    print(
                        "[JiraWebhook] Action item extraction enqueue result: "
                        f"task={issue_key} actor_user_id={actor_user_id or 'none'} "
                        f"queued={bool(enqueue_result.get('queued'))} "
                        f"job_id={enqueue_result.get('job_id') or 'none'} "
                        f"reason={enqueue_result.get('reason') or 'n/a'}"
                    )

        print(f"{'='*60}\n")

    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        import traceback
        traceback.print_exc()


def process_jira_webhook_sync(payload: dict, project_id: str):
    """
    Synchronous wrapper for process_jira_webhook for RQ workers

    Args:
        payload: Jira webhook payload
    """
    async def run_with_setup():
        """Setup MongoDB connection and run the handler"""
        from app.core.database import ensure_mongo_ready

        await ensure_mongo_ready()
        await process_jira_webhook(payload, project_id=project_id)

    # Run with asyncio
    asyncio.run(run_with_setup())
