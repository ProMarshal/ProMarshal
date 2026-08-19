"""Cadence check-in dispatch for paid-tier projects."""
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.cadence.formatters import (
    format_backlog_task,
    format_greeting_message,
    format_paid_tier_message,
)
from app.cadence.llm_service import get_llm_service
from app.cadence.logger import log_reminder_failed, log_reminder_sent
from app.cadence.member_matcher import match_tasks_to_members
from app.cadence.session_store import (
    complete_session,
    create_session,
    has_active_session,
    set_backlog_context,
    set_session_outcome,
)
from app.cadence.slack_identity import resolve_slack_user_id_for_member
from app.cadence.task_fetcher import fetch_tasks_for_project
from app.cadence.transition_fetcher import fetch_available_transitions
from app.core.config import settings
from app.core.security import encryption_service
from app.domain.capabilities.materialization import (
    connected_pm_providers_from_project_context,
    resolve_cadence_task_provider,
)
from app.integrations.slack.client import SlackClient

_IN_PROGRESS_STATUSES = {"in_progress", "in progress", "in review"}
_TODO_STATUSES = {"todo", "to do", "open", "new"}


def _sort_tasks(tasks: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split tasks into (in_progress, backlog)."""
    in_progress: List[Dict[str, Any]] = []
    backlog: List[Dict[str, Any]] = []
    for task in tasks:
        status = (task.get("status") or "").replace("_", " ").strip().lower()
        if status in _IN_PROGRESS_STATUSES:
            in_progress.append(task)
        elif status in _TODO_STATUSES:
            backlog.append(task)
    return in_progress, backlog


def _format_zero_task_prompt(*, member_name: str, project_name: str) -> Dict[str, Any]:
    display_name = str(member_name or "there").strip()
    display_project = str(project_name or "this project").strip()
    return {
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"Hi {display_name} - quick check-in for *{display_project}*.\n"
                        "I don't see any active work items assigned to you right now. "
                        "Any blockers, risks, or important updates you want to share with the PM?"
                    ),
                },
            }
        ]
    }


async def process_project_checkin(
    db: AsyncIOMotorDatabase,
    project: Dict[str, Any],
    correlation_id: Optional[str] = None,
) -> int:
    """Process a single paid-tier project and dispatch cadence DMs."""
    collection_name = project.get("project_id")
    project_name = project.get("project_name", "Unknown")
    reminders_sent = 0

    print(f"Processing paid tier project: {project_name} (Collection: {collection_name})")

    slack_integration = project.get("integrations", {}).get("slack", {})
    encrypted_token = slack_integration.get("access_token")
    if not encrypted_token:
        print(f"  No Slack token for project {collection_name}")
        return 0
    try:
        slack_token = encryption_service.decrypt(encrypted_token)
    except Exception as exc:
        print(f"  Failed to decrypt Slack token: {exc}")
        return 0

    task_provider = resolve_cadence_task_provider(project_context=project)
    if not task_provider:
        connected_pm_providers = connected_pm_providers_from_project_context(project)
        if len(connected_pm_providers) > 1:
            print(
                "cadence_checkin_skipped_provider_ambiguous: "
                f"project_id={collection_name} providers={connected_pm_providers}"
            )
            return 0
        if len(connected_pm_providers) == 1:
            task_provider = connected_pm_providers[0]
    if not task_provider:
        print(f"  No connected PM provider for project {collection_name}")
        return 0

    tasks = await fetch_tasks_for_project(db, collection_name, provider=task_provider)
    if tasks:
        print(f"  Found {len(tasks)} pending work items")
    else:
        print(f"  No pending work items for project {collection_name}; running zero-work-item cadence flow")

    members = project.get("members", [])
    active_members = [m for m in members if m.get("status") == "active"]
    cadence_policy = dict(project.get("_cadence_schedule_spec") or {})
    if bool(cadence_policy.get("ignore_followup_for_owner", False)):
        owner_user_id = str(project.get("user_id") or "").strip()
        if owner_user_id:
            active_members = [
                m for m in active_members
                if str(m.get("user_id") or "").strip() != owner_user_id
            ]
    if not active_members:
        print(f"  No active members for project {collection_name}")
        return 0

    member_tasks = match_tasks_to_members(tasks, active_members)
    if member_tasks:
        print(f"  Tasks matched to {len(member_tasks)} members")
    else:
        print(f"  No tasks matched to members for project {collection_name}")

    slack_client = SlackClient(slack_token)
    workspace_id = str(((project.get("integrations") or {}).get("slack") or {}).get("team_id") or "").strip()
    expected_count = len(active_members)

    for member in active_members:
        member_email = str(member.get("email") or "").strip().lower()
        member_user_id = str(member.get("user_id") or member_email).strip()
        member_name = str(member.get("name") or member_email.split("@")[0]).strip()
        integration_ids = member.get("integration_ids") or {}
        task_provider_mapping_present = (
            bool(str(integration_ids.get("jira_account_id") or "").strip())
            if str(task_provider or "").strip().lower() == "jira"
            else None
        )
        tasks_list = list(member_tasks.get(member_email, []))
        in_progress, backlog = _sort_tasks(tasks_list)
        agenda_tasks = in_progress or backlog
        session_type = "task_checkin" if agenda_tasks else "zero_task_checkin"
        initial_status = "awaiting_status" if agenda_tasks else "awaiting_overall_input"
        initial_reason_codes: List[str] = []
        if not agenda_tasks:
            initial_reason_codes.append("no_assigned_tasks")
        if task_provider_mapping_present is False:
            initial_reason_codes.append("task_provider_mapping_missing")

        try:
            slack_user_id = await resolve_slack_user_id_for_member(
                db=db,
                project=project,
                member=member,
                slack_client=slack_client,
            )

            cadence_scope = str(getattr(settings, "cadence_single_active_scope", "global") or "global")
            if await has_active_session(
                db,
                user_id=member_user_id,
                project_id=collection_name,
                scope=cadence_scope,
            ):
                skipped_session_id = await create_session(
                    db,
                    user_id=member_user_id,
                    project_id=collection_name,
                    tasks=agenda_tasks,
                    correlation_id=correlation_id,
                    provider="slack",
                    task_provider=task_provider,
                    workspace_id=workspace_id,
                    external_user_id=str(slack_user_id or "").strip() or None,
                    member_email=member_email,
                    member_name=member_name or None,
                    session_type=session_type,
                    initial_status=initial_status,
                    correlation_expected_count=expected_count,
                    integration_health={
                        "task_provider_mapping_present": task_provider_mapping_present,
                        "slack_mapping_present": bool(str(slack_user_id or "").strip()),
                    },
                    initial_reason_codes=initial_reason_codes + ["existing_active_session_conflict"],
                )
                await set_session_outcome(
                    db,
                    skipped_session_id,
                    delivery_status="blocked_active_session",
                    response_status="pending",
                    terminal=False,
                    reason_code="existing_active_session_conflict",
                )
                print(
                    "cadence_session_skipped_active_exists: "
                    f"user_id={member_user_id} project_id={collection_name} scope={cadence_scope}"
                )
                continue

            if not slack_user_id:
                unresolved_session_id = await create_session(
                    db,
                    user_id=member_user_id,
                    project_id=collection_name,
                    tasks=agenda_tasks,
                    correlation_id=correlation_id,
                    provider="slack",
                    task_provider=task_provider,
                    workspace_id=workspace_id,
                    external_user_id=None,
                    member_email=member_email,
                    member_name=member_name or None,
                    session_type=session_type,
                    initial_status="completed",
                    completion_reason="slack_identity_missing",
                    correlation_expected_count=expected_count,
                    integration_health={
                        "task_provider_mapping_present": task_provider_mapping_present,
                        "slack_mapping_present": False,
                    },
                    initial_reason_codes=initial_reason_codes + ["slack_identity_missing"],
                )
                await set_session_outcome(
                    db,
                    unresolved_session_id,
                    delivery_status="identity_unresolved",
                    response_status="no_response_expired",
                    task_provider_mapping_present=(
                        task_provider_mapping_present if task_provider_mapping_present is not None else None
                    ),
                    slack_mapping_present=False,
                    terminal=True,
                    reason_code="slack_identity_missing",
                )
                log_reminder_failed(collection_name, member_email, "Slack user not found", correlation_id)
                continue

            if agenda_tasks:
                import asyncio as _asyncio
                transition_results = await _asyncio.gather(
                    *[
                        fetch_available_transitions(
                            db=db,
                            project=project,
                            task_key=str(task.get("external_id") or ""),
                            task_provider=str(task_provider or ""),
                        )
                        for task in agenda_tasks
                    ],
                    return_exceptions=True,
                )
                for task, transitions in zip(agenda_tasks, transition_results):
                    task["available_transitions"] = transitions if isinstance(transitions, list) else []

                from app.cadence.session_store import _extract_last_comment_text
                for task in agenda_tasks:
                    if "last_comment" not in task:
                        task["last_comment"] = _extract_last_comment_text(task)

            session_id = await create_session(
                db,
                user_id=member_user_id,
                project_id=collection_name,
                tasks=agenda_tasks,
                correlation_id=correlation_id,
                provider="slack",
                task_provider=task_provider,
                workspace_id=workspace_id,
                external_user_id=str(slack_user_id or "").strip() or None,
                member_email=member_email,
                member_name=member_name or None,
                session_type=session_type,
                initial_status=initial_status,
                correlation_expected_count=expected_count,
                integration_health={
                    "task_provider_mapping_present": task_provider_mapping_present,
                    "slack_mapping_present": True,
                },
                initial_reason_codes=initial_reason_codes,
            )
            await set_session_outcome(
                db,
                session_id,
                task_provider_mapping_present=(
                    task_provider_mapping_present if task_provider_mapping_present is not None else None
                ),
                slack_mapping_present=True,
            )

            if in_progress and backlog:
                await set_backlog_context(db, session_id, backlog)

            if agenda_tasks:
                greeting = format_greeting_message(
                    member_name=member_name,
                    project_name=project_name,
                    in_progress_count=len(in_progress),
                    backlog_count=len(backlog),
                )
                await slack_client.send_message(slack_user_id, blocks=greeting["blocks"])

                first_task = agenda_tasks[0]
                is_backlog_only = not in_progress
                llm_message = None
                llm_svc = get_llm_service()
                if llm_svc:
                    try:
                        llm_message = await llm_svc.generate_task_message(
                            project_name=project_name,
                            user_name=member_name,
                            task=first_task,
                            task_index=0,
                            total_tasks=len(agenda_tasks),
                            previous_context=None,
                        )
                    except Exception as exc:
                        print(f"Error generating LLM message: {exc}")

                if is_backlog_only:
                    message = format_backlog_task(
                        task=first_task,
                        session_id=session_id,
                        task_index=0,
                        project_id=collection_name,
                    )
                else:
                    message = format_paid_tier_message(
                        task=first_task,
                        task_index=0,
                        total_tasks=len(agenda_tasks),
                        session_id=session_id,
                        project_id=collection_name,
                        llm_message=llm_message,
                    )
                success = await slack_client.send_message(slack_user_id, blocks=message["blocks"])
            else:
                zero_prompt = _format_zero_task_prompt(member_name=member_name, project_name=project_name)
                success = await slack_client.send_message(slack_user_id, blocks=zero_prompt["blocks"])

            if success:
                reminders_sent += 1
                await set_session_outcome(
                    db,
                    session_id,
                    delivery_status="sent",
                    reason_code="no_assigned_tasks" if not agenda_tasks else None,
                )
                log_reminder_sent(collection_name, member_email, len(agenda_tasks), "paid", correlation_id)
                print(
                    f"  Sent Cadence check-in to {member_email} "
                    f"(Session: {session_id}, in_progress={len(in_progress)}, backlog={len(backlog)})"
                )
            else:
                await set_session_outcome(
                    db,
                    session_id,
                    delivery_status="failed",
                    response_status="no_response_expired",
                    terminal=True,
                    reason_code="dm_delivery_failed",
                )
                await complete_session(db, session_id, reason="dm_delivery_failed")
                log_reminder_failed(collection_name, member_email, "Failed to send Slack message", correlation_id)
        except Exception as exc:
            log_reminder_failed(collection_name, member_email, str(exc), correlation_id)

    return reminders_sent
