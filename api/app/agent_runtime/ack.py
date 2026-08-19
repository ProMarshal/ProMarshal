"""Shared quick-ack infrastructure for Cortex and Cadence.

Ack messages are sent immediately (before the LLM starts) via a non-blocking
Slack call dispatched with asyncio.create_task(). Each feature has its own
rotating pool to avoid repetition and match the interaction tone.
"""

from __future__ import annotations

import random
from typing import Any, Dict, Optional


_CORTEX_ACK_POOL = [
    "Got it. Checking now.",
    "On it. I'll verify that.",
    "Understood. Let me look into it.",
    "Thanks, I'll pull that up.",
    "I'm on it now.",
    "Got your request. Checking it.",
    "Sure, let me confirm that.",
    "Okay, I'll verify and update you.",
    "Absolutely, checking that now.",
    "Working on it now.",
    "Let me check that for you.",
    "I'll take a quick look now.",
    "Got it, I'm verifying the details.",
    "One moment, I'll confirm that.",
    "Thanks, checking the latest now.",
]

_CADENCE_ACK_POOL = [
    "Got it, noted.",
    "Noted. Updating now.",
    "Thanks, captured.",
    "Understood, logging that.",
    "Perfect, updating this.",
    "Captured. Moving to the next one.",
    "Got it, I've recorded that.",
    "Noted, proceeding.",
    "Thanks, update captured.",
    "Okay, saved that.",
]


def build_ack_text(source: str) -> str:
    """
    Return a random ack message for the given source feature.

    source: "cortex" | "cadence"
    Falls back to the Cortex pool for unknown sources.
    """
    pool = _CADENCE_ACK_POOL if source == "cadence" else _CORTEX_ACK_POOL
    return random.choice(pool)


async def send_ack_best_effort(
    slack_client: Any,
    *,
    channel_id: str,
    thread_ts: Optional[str],
    source: str,
) -> bool:
    """
    Send a quick ack message as best effort (non-blocking).

    Callers should dispatch using asyncio.create_task(...) so webhook
    handling is not blocked while waiting for the Slack response.

    Returns True if Slack confirmed delivery, False on any failure.
    """
    from app.integrations.slack.delivery import chat_post_message_with_retry

    try:
        payload: Dict[str, Any] = {
            "channel": channel_id,
            "text": build_ack_text(source),
        }
        if thread_ts and source in {"cortex", "cadence"}:
            payload["thread_ts"] = thread_ts

        response = await chat_post_message_with_retry(
            slack_client=slack_client,
            payload=payload,
            operation_name=f"agent_runtime.ack.{source}",
        )
        return bool(response.get("ok", False))
    except Exception as exc:
        print(f"Ack send failed [{source}]: {exc}")
        return False
