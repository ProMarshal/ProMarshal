"""Queued entrypoints for Cadence runtime flows."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress

from app.cadence.orchestrator import handle_cadence_dm
from app.cadence.session_store import get_session, stamp_session_lease_version
from app.core.config import settings
from app.core.database import get_database
from app.core.queue_metrics import get_queue_depth
from app.core.session_lease import (
    acquire_session_lease,
    release_session_lease,
    renew_session_lease,
    reset_active_session_lease,
    set_active_session_lease,
)
from app.integrations.slack import slack_bot_service


logger = logging.getLogger("app.cadence.queued")

async def _run_lease_heartbeat(*, session_id: str, lease_token: int) -> None:
    while True:
        heartbeat_seconds = max(2, int(getattr(settings, "cadence_lease_heartbeat_seconds", 15) or 15))
        ttl_seconds = max(5, int(getattr(settings, "cadence_lease_ttl_seconds", 45) or 45))
        await asyncio.sleep(heartbeat_seconds)
        renewed = renew_session_lease(
            session_id=session_id,
            token=lease_token,
            ttl_seconds=ttl_seconds,
        )
        if not renewed:
            logger.warning(
                "cadence_dm_lease_renew_failed session=%s token=%s",
                session_id,
                lease_token,
            )
            return


async def handle_cadence_dm_queued(
    *,
    session_id: str,
    project_id: str,
    workspace_id: str = "",
    channel: str = "",
    text: str = "",
    actor_slack_user_id: str = "",
) -> bool:
    """
    Queue-safe cadence DM entrypoint.

    Resolves session/project/slack client from IDs and delegates to the canonical
    `handle_cadence_dm` runtime flow.
    """
    normalized_session_id = str(session_id or "").strip()
    normalized_project_id = str(project_id or "").strip()
    normalized_workspace = str(workspace_id or "").strip()
    normalized_channel = str(channel or "").strip()
    normalized_text = str(text or "").strip()
    normalized_actor = str(actor_slack_user_id or "").strip() or None

    if not normalized_session_id or not normalized_channel:
        logger.warning(
            "cadence_dm_queued_invalid_input session=%s project=%s workspace=%s channel=%s",
            normalized_session_id or "missing",
            normalized_project_id or "missing",
            normalized_workspace or "missing",
            normalized_channel or "missing",
        )
        return False

    db = get_database()
    session = await get_session(db, normalized_session_id)
    if not isinstance(session, dict):
        logger.warning(
            "cadence_dm_queued_session_not_found session=%s project=%s workspace=%s",
            normalized_session_id,
            normalized_project_id or "unknown",
            normalized_workspace or "unknown",
        )
        return False

    resolved_project_id = str(session.get("project_id") or normalized_project_id).strip()
    if not resolved_project_id:
        logger.warning(
            "cadence_dm_queued_project_missing session=%s workspace=%s",
            normalized_session_id,
            normalized_workspace or "unknown",
        )
        return False

    project = await db.projects.find_one(
        {"project_id": resolved_project_id},
        {"integrations.slack.access_token": 1},
    )
    slack_integration = ((project or {}).get("integrations") or {}).get("slack") or {}
    encrypted_token = str(slack_integration.get("access_token") or "").strip()
    if not encrypted_token:
        logger.warning(
            "cadence_dm_queued_slack_token_missing session=%s project=%s workspace=%s",
            normalized_session_id,
            resolved_project_id,
            normalized_workspace or "unknown",
        )
        return False

    start_ms = int(time.perf_counter() * 1000)
    lease_wait_ms = 0
    lease_retries = 0

    try:
        slack_client = slack_bot_service.get_slack_client(encrypted_token)
    except Exception as exc:
        logger.exception(
            "cadence_dm_queued_slack_client_build_failed session=%s project=%s",
            normalized_session_id,
            resolved_project_id,
        )
        return False

    def _acquire() -> tuple[bool, int]:
        ttl_seconds = max(5, int(getattr(settings, "cadence_lease_ttl_seconds", 45) or 45))
        return acquire_session_lease(
            normalized_session_id,
            ttl_seconds=ttl_seconds,
        )

    lease_enabled = bool(getattr(settings, "cadence_dm_lease_enabled", True))
    if lease_enabled:
        acquire_started = int(time.perf_counter() * 1000)
        try:
            lease_acquired, lease_token = _acquire()
        except Exception as exc:
            logger.warning(
                "cadence_dm_lease_acquire_exception session=%s error=%s",
                normalized_session_id,
                exc,
            )
            lease_acquired, lease_token = False, 0
        lease_wait_ms = max(0, int(time.perf_counter() * 1000) - acquire_started)
    else:
        lease_acquired, lease_token = False, 0
        lease_wait_ms = 0

    if lease_enabled and not lease_acquired and int(lease_token or 0) > 0:
        retry_delay_ms = max(0, int(getattr(settings, "cadence_lease_retry_delay_ms", 2000) or 2000))
        await asyncio.sleep(retry_delay_ms / 1000.0)
        lease_retries = 1
        retry_started = int(time.perf_counter() * 1000)
        try:
            lease_acquired, lease_token = _acquire()
        except Exception:
            lease_acquired, lease_token = False, 0
        lease_wait_ms += max(0, int(time.perf_counter() * 1000) - retry_started)

    if lease_enabled and not lease_acquired and int(lease_token or 0) > 0:
        logger.info(
            "cadence_dm_lease_contended_drop session=%s token=%s",
            normalized_session_id,
            lease_token,
        )
        with suppress(Exception):
            await slack_client.chat_postMessage(
                channel=normalized_channel,
                text="Still processing your previous cadence response. Please retry in a few seconds.",
            )
        return False

    heartbeat_task = None
    lease_context_token = None
    if lease_enabled and lease_acquired and int(lease_token or 0) > 0:
        try:
            # Prime fenced session writes for this lease token.
            await stamp_session_lease_version(
                db,
                normalized_session_id,
                int(lease_token),
            )
        except Exception as exc:
            logger.warning(
                "cadence_dm_lease_version_stamp_failed session=%s token=%s error=%s",
                normalized_session_id,
                int(lease_token),
                exc,
            )
        lease_context_token = set_active_session_lease(
            session_id=normalized_session_id,
            lease_token=int(lease_token),
            enforce=bool(getattr(settings, "cadence_lease_enforce", False)),
        )
        heartbeat_task = asyncio.create_task(
            _run_lease_heartbeat(
                session_id=normalized_session_id,
                lease_token=int(lease_token),
            )
        )
    elif lease_enabled and int(lease_token or 0) == 0:
        logger.warning(
            "cadence_dm_lease_unavailable_fail_open session=%s",
            normalized_session_id,
        )
        lease_context_token = set_active_session_lease(
            session_id=normalized_session_id,
            lease_token=0,
            enforce=False,
        )
    else:
        lease_context_token = set_active_session_lease(
            session_id=normalized_session_id,
            lease_token=0,
            enforce=False,
        )

    try:
        handled = bool(
            await handle_cadence_dm(
                db,
                session=session,
                text=normalized_text,
                slack_client=slack_client,
                channel=normalized_channel,
                actor_slack_user_id=normalized_actor,
            )
        )
        return handled
    except Exception as exc:
        logger.exception(
            "cadence_dm_queued_execution_failed session=%s project=%s workspace=%s error=%s",
            normalized_session_id,
            resolved_project_id,
            normalized_workspace or "unknown",
            exc,
        )
        return False
    finally:
        total_ms = max(0, int(time.perf_counter() * 1000) - start_ms)
        queue_depth = get_queue_depth("cadence_dm")
        logger.info(
            "cadence_dm_turn_complete session=%s project=%s workspace=%s total_ms=%s "
            "lease_wait_ms=%s lease_retries=%s queue_depth=%s",
            normalized_session_id,
            resolved_project_id,
            normalized_workspace or "unknown",
            total_ms,
            lease_wait_ms,
            lease_retries,
            queue_depth,
        )
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
        if lease_context_token is not None:
            reset_active_session_lease(lease_context_token)
        if lease_acquired and int(lease_token or 0) > 0:
            release_session_lease(
                session_id=normalized_session_id,
                token=int(lease_token),
            )
