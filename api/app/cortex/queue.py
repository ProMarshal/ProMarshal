"""Cortex queue entry helpers for background run processing."""

from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import uuid4

from app.core.config import settings
from app.core.queue_backends.dramatiq_backend import enqueue as dramatiq_enqueue
from app.core.queue_runtime import QUEUE_CORTEX_RUNS
from app.core.redis_queue import get_redis_connection
from app.cortex.queue_runtime import (
    build_queue_item,
    has_active_run,
    has_pending_dispatch,
    push_session_queue_item,
    session_key_from_ingress,
    try_claim_dispatch,
)


@dataclass(frozen=True)
class CortexQueueEnqueueResult:
    """Result of attempting to enqueue a Cortex run."""

    queued: bool
    reason: str = ""
    job_id: Optional[str] = None


def enqueue_cortex_run(
    *,
    ingress: Dict[str, Any],
    raw_event: Optional[Dict[str, Any]] = None,
) -> CortexQueueEnqueueResult:
    """Enqueue one Cortex run into `cortex_runs` queue."""
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return CortexQueueEnqueueResult(queued=False, reason="cortex_queue_unavailable")

    try:
        session_key = session_key_from_ingress(ingress)
    except Exception:
        return CortexQueueEnqueueResult(queued=False, reason="invalid_session_key")

    queued_item = build_queue_item(ingress=ingress, raw_event=raw_event)

    try:
        if has_active_run(redis_conn=redis_conn, session_key=session_key) or has_pending_dispatch(
            redis_conn=redis_conn,
            session_key=session_key,
        ):
            queued = push_session_queue_item(
                redis_conn=redis_conn,
                session_key=session_key,
                item=queued_item,
            )
            return CortexQueueEnqueueResult(
                queued=queued,
                reason="queued_for_active_run" if queued else "session_queue_push_failed",
            )

        dispatch_token = str(uuid4())
        dispatch_ttl = max(30, int(getattr(settings, "cortex_dispatch_ttl_seconds", 120)))
        claimed = try_claim_dispatch(
            redis_conn=redis_conn,
            session_key=session_key,
            dispatch_token=dispatch_token,
            ttl_seconds=dispatch_ttl,
        )
        if not claimed:
            queued = push_session_queue_item(
                redis_conn=redis_conn,
                session_key=session_key,
                item=queued_item,
            )
            return CortexQueueEnqueueResult(
                queued=queued,
                reason="queued_for_pending_dispatch" if queued else "session_queue_push_failed",
            )

        enqueue_result = dramatiq_enqueue(
            queue_name=QUEUE_CORTEX_RUNS,
            actor_name="process_cortex_run",
            kwargs={
                "ingress": ingress,
                "raw_event": raw_event or {},
                "drain_session_key": "",
                "dispatch_token": dispatch_token,
            },
        )
        if not bool(enqueue_result.get("accepted")):
            return CortexQueueEnqueueResult(
                queued=False,
                reason=str(enqueue_result.get("reason") or "cortex_queue_enqueue_failed"),
            )
        job_id = str(enqueue_result.get("job_id") or "")
        return CortexQueueEnqueueResult(
            queued=True,
            reason="queued_new_dispatch",
            job_id=job_id,
        )
    except Exception as exc:
        print(f"Cortex enqueue failed: {exc}")
        return CortexQueueEnqueueResult(queued=False, reason="cortex_queue_enqueue_failed")
