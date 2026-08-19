"""Dramatiq backend adapter for migration phases."""

from __future__ import annotations

from typing import Any, Dict
# Ensure broker bootstrap side-effects (dramatiq.set_broker) are loaded
# in API/web processes before actor.send(...) is used.
from app.workers import dramatiq_app as _dramatiq_app  # noqa: F401
from app.workers import dramatiq_actors


_ACTOR_REGISTRY = {
    "process_jira_webhook": dramatiq_actors.process_jira_webhook_actor,
    "process_work_item_migration_job": dramatiq_actors.process_work_item_migration_job_actor,
    "run_action_item_extraction": dramatiq_actors.run_action_item_extraction_actor,
    "handle_modal_submission": dramatiq_actors.handle_modal_submission_actor,
    "process_create_task": dramatiq_actors.process_create_task_actor,
    "process_cortex_run": dramatiq_actors.process_cortex_run_actor,
    "process_pm_board_summary_job": dramatiq_actors.process_pm_board_summary_job_actor,
    "process_planner_bootstrap_job": dramatiq_actors.process_planner_bootstrap_job_actor,
    "handle_cadence_dm": dramatiq_actors.handle_cadence_dm_actor,
}


def enqueue(*, queue_name: str, actor_name: str, kwargs: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Enqueue a job to Dramatiq actor by logical actor name."""
    normalized_queue = str(queue_name or "").strip()
    normalized_actor_name = str(actor_name or "").strip()
    actor = _ACTOR_REGISTRY.get(normalized_actor_name)
    if actor is None:
        return {
            "accepted": False,
            "reason": "actor_not_registered",
            "queue_name": normalized_queue,
            "job_id": None,
            "runtime": "dramatiq",
            "actor_name": normalized_actor_name,
        }

    send = getattr(actor, "send", None)
    if send is None:
        return {
            "accepted": False,
            "reason": "dramatiq_unavailable",
            "queue_name": normalized_queue,
            "job_id": None,
            "runtime": "dramatiq",
            "actor_name": normalized_actor_name,
        }

    try:
        message = send(**(kwargs or {}))
    except Exception as exc:
        return {
            "accepted": False,
            "reason": f"enqueue_failed:{exc}",
            "queue_name": normalized_queue,
            "job_id": None,
            "runtime": "dramatiq",
            "actor_name": normalized_actor_name,
        }

    message_id = str(getattr(message, "message_id", "") or "")
    return {
        "accepted": True,
        "reason": "queued",
        "queue_name": normalized_queue,
        "job_id": message_id,
        "runtime": "dramatiq",
        "actor_name": normalized_actor_name,
    }
