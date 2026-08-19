"""Queue runtime helpers.

Queue execution is Dramatiq-only. This module retains logical queue-name
constants and helper APIs to avoid widespread callsite churn.
"""

from __future__ import annotations

RUNTIME_RQ = "rq"
RUNTIME_DRAMATIQ = "dramatiq"

QUEUE_CHAT_INTERACTIONS = "chat_interactions"
QUEUE_WORKITEM_EVENTS = "workitem_events"
QUEUE_CHAT_COMMANDS = "chat_commands"
QUEUE_CORTEX_RUNS = "cortex_runs"
QUEUE_ACTION_ITEM_EXTRACTION = "action_item_extraction"
QUEUE_CADENCE_DM = "cadence_dm"


def get_queue_runtime() -> str:
    """Return active queue runtime."""
    return RUNTIME_DRAMATIQ


def is_dramatiq_runtime_enabled() -> bool:
    """Return True when global runtime is Dramatiq."""
    return True


def is_dramatiq_enabled_for_queue(queue_name: str) -> bool:
    """Return whether a queue is Dramatiq-backed.

    All supported queues are Dramatiq-backed in the current runtime.
    """
    normalized = str(queue_name or "").strip().lower()
    return normalized in {
        QUEUE_CORTEX_RUNS,
        QUEUE_WORKITEM_EVENTS,
        QUEUE_CHAT_INTERACTIONS,
        QUEUE_CHAT_COMMANDS,
        QUEUE_ACTION_ITEM_EXTRACTION,
        QUEUE_CADENCE_DM,
    }
