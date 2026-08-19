"""Dramatiq actors used during queue migration."""

from __future__ import annotations

import os
from typing import Any, Callable

try:
    import dramatiq
except Exception:  # pragma: no cover - dramatiq may be unavailable pre-install
    dramatiq = None


def _noop_actor(*args: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        return fn

    return _decorator


actor = dramatiq.actor if dramatiq is not None else _noop_actor


def _dramatiq_runtime_owner() -> str:
    return f"dramatiq_event_loop_thread:{os.getpid()}"


def _ensure_capability_bundles_ready() -> None:
    """
    Ensure built-in capability bundles are registered in worker runtime.

    Keep this out of `dramatiq_app.build_broker()` because that module is also
    imported by API processes for queue backend bootstrap side effects.
    """
    from app.domain.capabilities.startup import install_builtin_bundles

    install_builtin_bundles()


async def _ensure_dramatiq_mongo_ready() -> None:
    from app.core.database import ensure_mongo_ready

    await ensure_mongo_ready(runtime_owner=_dramatiq_runtime_owner())


@actor(queue_name="chat_interactions")
def phase_a_chat_interactions_placeholder(payload: dict) -> None:
    print(f"[Dramatiq][PhaseA] chat_interactions placeholder received keys={sorted((payload or {}).keys())}")


@actor(queue_name="workitem_events")
def phase_a_workitem_events_placeholder(payload: dict) -> None:
    print(f"[Dramatiq][PhaseA] workitem_events placeholder received keys={sorted((payload or {}).keys())}")


@actor(queue_name="chat_commands")
def phase_a_chat_commands_placeholder(payload: dict) -> None:
    print(f"[Dramatiq][PhaseA] chat_commands placeholder received keys={sorted((payload or {}).keys())}")


@actor(queue_name="cortex_runs")
def phase_a_cortex_runs_placeholder(payload: dict) -> None:
    print(f"[Dramatiq][PhaseA] cortex_runs placeholder received keys={sorted((payload or {}).keys())}")


@actor(queue_name="action_item_extraction")
def phase_a_action_item_extraction_placeholder(payload: dict) -> None:
    print(
        "[Dramatiq][PhaseA] action_item_extraction placeholder "
        f"received keys={sorted((payload or {}).keys())}"
    )


@actor(queue_name="cadence_dm")
def phase_a_cadence_dm_placeholder(payload: dict) -> None:
    print(f"[Dramatiq][PhaseA] cadence_dm placeholder received keys={sorted((payload or {}).keys())}")


@actor(queue_name="workitem_events")
async def process_jira_webhook_actor(payload: dict, project_id: str) -> None:
    """Phase B: Dramatiq actor for Jira webhook processing."""
    from app.routes.jira_webhooks import process_jira_webhook

    await _ensure_dramatiq_mongo_ready()
    await process_jira_webhook(payload, project_id=project_id)


@actor(queue_name="workitem_events", max_retries=0)
async def process_work_item_migration_job_actor(
    trigger: str = "manual",
    dry_run: bool = False,
    project_limit: int | None = None,
) -> None:
    """Run canonical work-item cutover migration as a background actor."""
    from app.projects.work_item_migration import run_work_item_migration_job

    await _ensure_dramatiq_mongo_ready()
    await run_work_item_migration_job(
        trigger=trigger,
        dry_run=bool(dry_run),
        project_limit=project_limit,
    )


@actor(queue_name="action_item_extraction")
async def run_action_item_extraction_actor(payload: dict) -> None:
    """Phase B: Dramatiq actor for action-item extraction."""
    from app.action_items.jobs import run_action_item_extraction_async

    await _ensure_dramatiq_mongo_ready()
    await run_action_item_extraction_async(payload)


@actor(queue_name="chat_interactions")
async def handle_modal_submission_actor(payload: dict) -> None:
    """Phase C: Dramatiq actor for cadence modal submissions."""
    from app.cadence.interaction_handler import handle_modal_submission_direct

    _ensure_capability_bundles_ready()
    await _ensure_dramatiq_mongo_ready()
    await handle_modal_submission_direct(payload)


@actor(queue_name="chat_commands")
async def process_create_task_actor(
    project_id: str,
    team_id: str,
    created_by_email: str,
    title: str,
    description: str = "",
    assignee_account_id: str | None = None,
    priority: str = "medium",
    user_id: str | None = None,
    channel_id: str | None = None,
) -> None:
    """Phase C: Dramatiq actor for create-task command processing."""
    from app.integrations.slack.workers import process_create_task_async

    await _ensure_dramatiq_mongo_ready()
    await process_create_task_async(
        project_id=project_id,
        team_id=team_id,
        created_by_email=created_by_email,
        title=title,
        description=description,
        assignee_account_id=assignee_account_id,
        priority=priority,
        user_id=user_id,
        channel_id=channel_id,
    )


@actor(queue_name="cortex_runs", max_retries=0)
def process_cortex_run_actor(
    ingress: dict | None = None,
    raw_event: dict | None = None,
    drain_session_key: str = "",
    dispatch_token: str = "",
) -> None:
    """Phase D: Dramatiq actor for Cortex run processing."""
    from app.cortex.worker import process_cortex_run

    process_cortex_run(
        ingress=ingress or {},
        raw_event=raw_event or {},
        drain_session_key=drain_session_key or "",
        dispatch_token=dispatch_token or "",
    )


@actor(queue_name="cortex_runs", max_retries=0)
async def process_pm_board_summary_job_actor(
    project_id: str,
    trigger: str = "async",
    custom_project_id: str = "",
) -> None:
    """Phase D: Dramatiq actor for PM summary async recompute."""
    from app.projects.router import _process_pm_board_summary_job_async

    await _ensure_dramatiq_mongo_ready()
    await _process_pm_board_summary_job_async(
        project_id=project_id,
        trigger=trigger,
        custom_project_id=custom_project_id,
    )


@actor(queue_name="cortex_runs", max_retries=0)
async def process_planner_bootstrap_job_actor(
    project_id: str,
) -> None:
    """Dramatiq actor for planner bootstrap async hydration."""
    from app.planner.router import _process_planner_bootstrap_job_async

    await _ensure_dramatiq_mongo_ready()
    await _process_planner_bootstrap_job_async(project_id=project_id)


@actor(queue_name="cadence_dm", max_retries=2)
async def handle_cadence_dm_actor(
    session_id: str,
    project_id: str,
    workspace_id: str = "",
    channel: str = "",
    text: str = "",
    actor_slack_user_id: str = "",
) -> None:
    """Dramatiq actor for cadence DM processing."""
    from app.cadence.queued import handle_cadence_dm_queued

    _ensure_capability_bundles_ready()
    await _ensure_dramatiq_mongo_ready()
    await handle_cadence_dm_queued(
        session_id=session_id,
        project_id=project_id,
        workspace_id=workspace_id,
        channel=channel,
        text=text,
        actor_slack_user_id=actor_slack_user_id,
    )
