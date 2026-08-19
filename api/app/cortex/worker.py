"""RQ worker entrypoints for Cortex run processing."""

import atexit
import asyncio
import os
from contextlib import suppress
from time import perf_counter
from typing import Any, Dict, Optional
from uuid import uuid4

from app.cortex.orchestrator import CortexOrchestrator
from app.cortex.queue_runtime import (
    build_queue_item,
    clear_inflight,
    has_active_run,
    has_pending_dispatch,
    pop_session_queue_item,
    push_session_queue_item,
    recover_stale_inflight,
    release_dispatch_if_owner,
    release_run_lock_if_owner,
    renew_run_lock_if_owner,
    session_key_from_ingress,
    session_queue_length,
    set_inflight,
    try_claim_dispatch,
    try_claim_run_lock,
)
from app.core.config import settings
from app.core.database import (
    close_mongo_connection,
    ensure_mongo_ready,
)
from app.core.queue_backends.dramatiq_backend import enqueue as dramatiq_enqueue
from app.core.queue_runtime import (
    QUEUE_CORTEX_RUNS,
    is_dramatiq_enabled_for_queue,
)
from app.core.redis_queue import get_redis_connection
from app.domain.capabilities.startup import install_builtin_bundles

_WORKER_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _is_non_fork_runtime() -> bool:
    """Return True when running in a non-fork worker runtime (for example Windows/SimpleWorker)."""
    return not hasattr(os, "fork")


def _get_worker_loop() -> asyncio.AbstractEventLoop:
    """Return a reusable event loop for non-fork runtimes."""
    global _WORKER_LOOP
    if _WORKER_LOOP is None or _WORKER_LOOP.is_closed():
        _WORKER_LOOP = asyncio.new_event_loop()
    return _WORKER_LOOP


def _run_async_entrypoint(coro: Any) -> None:
    """Execute a coroutine in the appropriate worker runtime."""
    if _run_async_via_dramatiq_event_loop(coro):
        return
    if _is_non_fork_runtime():
        loop = _get_worker_loop()
        loop.run_until_complete(coro)
        return
    asyncio.run(coro)


def _run_async_via_dramatiq_event_loop(coro: Any) -> bool:
    """Run coroutine via Dramatiq AsyncIO event-loop thread when available."""
    if not is_dramatiq_enabled_for_queue(QUEUE_CORTEX_RUNS):
        return False
    try:
        from dramatiq.asyncio import get_event_loop_thread
    except Exception:
        return False
    try:
        loop_thread = get_event_loop_thread()
    except Exception as exc:
        print(f"[CortexWorker] Dramatiq event loop lookup failed: {exc}")
        return False
    if loop_thread is None:
        return False
    run_coroutine = getattr(loop_thread, "run_coroutine", None)
    if run_coroutine is None:
        return False
    result = run_coroutine(coro)
    if hasattr(result, "result"):
        result.result()
    return True


def _mongo_runtime_owner() -> Optional[str]:
    """Return runtime-owner token for Mongo loop-binding compatibility checks."""
    if is_dramatiq_enabled_for_queue(QUEUE_CORTEX_RUNS):
        return f"dramatiq_event_loop_thread:{os.getpid()}"
    return None


def _shutdown_non_fork_runtime() -> None:
    """Close persistent non-fork loop resources on worker shutdown."""
    global _WORKER_LOOP
    if not _is_non_fork_runtime():
        return
    loop = _WORKER_LOOP
    if loop is None or loop.is_closed() or loop.is_running():
        return
    try:
        loop.run_until_complete(close_mongo_connection())
    except Exception as exc:  # pragma: no cover - best-effort process shutdown path
        print(f"[CortexWorker] non-fork shutdown cleanup failed: {exc}")
    finally:
        loop.close()
        _WORKER_LOOP = None


atexit.register(_shutdown_non_fork_runtime)


def process_cortex_run(
    ingress: Optional[Dict[str, Any]] = None,
    raw_event: Optional[Dict[str, Any]] = None,
    drain_session_key: str = "",
    dispatch_token: str = "",
) -> None:
    """Sync RQ entrypoint; executes async Cortex pipeline."""
    _run_async_entrypoint(
        process_cortex_run_async(
            ingress=ingress or {},
            raw_event=raw_event or {},
            drain_session_key=drain_session_key or "",
            dispatch_token=dispatch_token or "",
        )
    )


async def _ensure_mongo_ready() -> str:
    """Ensure Mongo client is usable in the current async runtime context."""
    runtime_owner = _mongo_runtime_owner()
    return await ensure_mongo_ready(runtime_owner=runtime_owner)


async def process_cortex_run_async(
    *,
    ingress: Dict[str, Any],
    raw_event: Dict[str, Any],
    drain_session_key: str = "",
    dispatch_token: str = "",
) -> None:
    """Async Cortex run worker implementation."""
    install_builtin_bundles()
    non_fork_runtime = _is_non_fork_runtime()
    mongo_ready_started = perf_counter()
    mongo_state = await _ensure_mongo_ready()
    mongo_ready_ms = int((perf_counter() - mongo_ready_started) * 1000)
    print(f"[CortexWorker] mongo_ready state={mongo_state} duration_ms={mongo_ready_ms}")

    redis_conn = get_redis_connection()
    dramatiq_enabled = is_dramatiq_enabled_for_queue(QUEUE_CORTEX_RUNS)
    if redis_conn is None or not dramatiq_enabled:
        print("[CortexWorker] skipped: redis_queue_unavailable")
        return

    session_key = str(drain_session_key or "").strip()
    has_primary_ingress = bool(ingress)
    if not session_key and ingress:
        try:
            session_key = session_key_from_ingress(ingress)
        except Exception as exc:
            print(f"[CortexWorker] invalid session key: {exc}")
            return
    if not session_key:
        print("[CortexWorker] missing session key context")
        return

    if has_primary_ingress:
        try:
            ingress_session_key = session_key_from_ingress(ingress)
        except Exception as exc:
            print(f"[CortexWorker] invalid ingress session key: {exc}")
            return
        if ingress_session_key != session_key:
            print(
                "[CortexWorker] session isolation guard (primary ingress): "
                f"drain_session_key={session_key} ingress_session_key={ingress_session_key}"
            )
            queued_item = build_queue_item(ingress=ingress, raw_event=raw_event)
            routed = push_session_queue_item(
                redis_conn=redis_conn,
                session_key=ingress_session_key,
                item=queued_item,
            )
            if routed:
                _enqueue_dispatch_job_if_needed(
                    redis_conn=redis_conn,
                    session_key=ingress_session_key,
                )
            return

    run_id = str(uuid4())
    lock_ttl = max(30, int(getattr(settings, "cortex_run_lock_ttl_seconds", 60) or 60))
    heartbeat_interval = max(
        5,
        min(
            int(getattr(settings, "cortex_run_lock_heartbeat_seconds", 15) or 15),
            max(5, lock_ttl - 5),
        ),
    )
    inflight_ttl = max(60, int(getattr(settings, "cortex_inflight_ttl_seconds", 300) or 300))
    stale_seconds = max(15, int(getattr(settings, "cortex_inflight_stale_seconds", 45) or 45))
    requeue_guard_ttl = max(
        inflight_ttl,
        int(getattr(settings, "cortex_requeue_guard_ttl_seconds", 3600) or 3600),
    )
    drain_cap = max(1, int(getattr(settings, "cortex_queue_drain_cap", 2) or 2))

    if dispatch_token:
        release_dispatch_if_owner(
            redis_conn=redis_conn,
            session_key=session_key,
            dispatch_token=str(dispatch_token),
        )

    lock_acquired = try_claim_run_lock(
        redis_conn=redis_conn,
        session_key=session_key,
        run_id=run_id,
        ttl_seconds=lock_ttl,
    )
    if not lock_acquired:
        if has_primary_ingress:
            queued_item = build_queue_item(ingress=ingress, raw_event=raw_event)
            queued = push_session_queue_item(
                redis_conn=redis_conn,
                session_key=session_key,
                item=queued_item,
            )
            print(
                f"[CortexWorker] lock busy; queued current message session_key={session_key} queued={queued}"
            )
        else:
            print(f"[CortexWorker] lock busy; drain job skipped session_key={session_key}")
        return

    recovered, recovery_reason = recover_stale_inflight(
        redis_conn=redis_conn,
        session_key=session_key,
        stale_after_seconds=stale_seconds,
        requeue_guard_ttl_seconds=requeue_guard_ttl,
        inflight_ttl_seconds=inflight_ttl,
    )
    if recovered or recovery_reason not in {"no_inflight", "inflight_not_stale"}:
        print(
            f"[CortexWorker] inflight recovery session_key={session_key} "
            f"recovered={recovered} reason={recovery_reason}"
        )

    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _run_lock_heartbeat(
            redis_conn=redis_conn,
            session_key=session_key,
            run_id=run_id,
            ttl_seconds=lock_ttl,
            interval_seconds=heartbeat_interval,
            stop_event=heartbeat_stop,
        )
    )

    print(
        f"[CortexWorker] start session_key={session_key} has_primary_ingress={has_primary_ingress}"
    )

    try:
        orchestrator = CortexOrchestrator()

        if has_primary_ingress:
            await _process_one(
                orchestrator=orchestrator,
                ingress=ingress,
                raw_event=raw_event,
                session_key=session_key,
            )

        drained = 0
        while drained < drain_cap:
            queued_item = pop_session_queue_item(redis_conn=redis_conn, session_key=session_key)
            if queued_item is None:
                break

            try:
                queued_item_session_key = session_key_from_ingress(queued_item.ingress)
            except Exception as exc:
                print(
                    f"[CortexWorker] dropping malformed queued item session_key={session_key} "
                    f"queued_msg_id={queued_item.queued_msg_id} error={exc}"
                )
                drained += 1
                continue

            if queued_item_session_key != session_key:
                print(
                    "[CortexWorker] session isolation guard (drain item): "
                    f"current_session_key={session_key} queued_item_session_key={queued_item_session_key} "
                    f"queued_msg_id={queued_item.queued_msg_id}"
                )
                routed = push_session_queue_item(
                    redis_conn=redis_conn,
                    session_key=queued_item_session_key,
                    item=queued_item,
                )
                if routed:
                    _enqueue_dispatch_job_if_needed(
                        redis_conn=redis_conn,
                        session_key=queued_item_session_key,
                    )
                drained += 1
                continue

            set_inflight(
                redis_conn=redis_conn,
                session_key=session_key,
                item=queued_item,
                run_id=run_id,
                ttl_seconds=inflight_ttl,
            )
            try:
                await _process_one(
                    orchestrator=orchestrator,
                    ingress=queued_item.ingress,
                    raw_event=queued_item.raw_event,
                    session_key=session_key,
                )
                clear_inflight(redis_conn=redis_conn, session_key=session_key)
            except Exception:
                # Keep inflight for stale recovery on next lock acquisition.
                raise
            drained += 1

        remaining = session_queue_length(redis_conn=redis_conn, session_key=session_key)
        if remaining > 0:
            queued_job_id = _enqueue_dispatch_job_if_needed(
                redis_conn=redis_conn,
                session_key=session_key,
            )
            if queued_job_id:
                print(
                    f"[CortexWorker] queued continuation job session_key={session_key} "
                    f"remaining={remaining} job_id={queued_job_id}"
                )
            else:
                print(
                    f"[CortexWorker] continuation dispatch already pending session_key={session_key} "
                    f"remaining={remaining}"
                )

        print("[CortexWorker] completed")
    except Exception as exc:
        print(f"[CortexWorker] exception: {exc}")
        raise
    finally:
        heartbeat_stop.set()
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
        release_run_lock_if_owner(
            redis_conn=redis_conn,
            session_key=session_key,
            run_id=run_id,
        )


async def _run_lock_heartbeat(
    *,
    redis_conn: Any,
    session_key: str,
    run_id: str,
    ttl_seconds: int,
    interval_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(1, int(interval_seconds)))
            break
        except asyncio.TimeoutError:
            renewed = renew_run_lock_if_owner(
                redis_conn=redis_conn,
                session_key=session_key,
                run_id=run_id,
                ttl_seconds=ttl_seconds,
            )
            if not renewed:
                print(
                    f"[CortexWorker] lock heartbeat lost ownership session_key={session_key} run_id={run_id}"
                )
                stop_event.set()
                break


async def _process_one(
    *,
    orchestrator: CortexOrchestrator,
    ingress: Dict[str, Any],
    raw_event: Dict[str, Any],
    session_key: str,
) -> None:
    source = ingress.get("source")
    session_anchor = ingress.get("session_anchor")
    print(
        f"[CortexWorker] process source={source} session_anchor={session_anchor} session_key={session_key}"
    )
    result = await orchestrator.handle_turn({"ingress": ingress, "raw_event": raw_event})
    if result.ok:
        return
    error = str(result.error or "unknown_error")
    metadata = result.metadata or {}
    delivered = bool(metadata.get("delivered"))
    if error == "out_of_scope" and delivered:
        print(
            "[CortexWorker] handled terminal policy outcome: "
            f"error={error} delivered={delivered} session_key={session_key}"
        )
        return
    print(f"[CortexWorker] failed: {error}")
    raise RuntimeError(f"cortex_orchestrator_failed:{error}")


def _enqueue_dispatch_job_if_needed(
    *,
    redis_conn: Any,
    session_key: str,
) -> Optional[str]:
    """Claim one dispatch slot and enqueue a continuation job if eligible."""
    if has_active_run(redis_conn=redis_conn, session_key=session_key):
        return None
    if has_pending_dispatch(redis_conn=redis_conn, session_key=session_key):
        return None

    dispatch_token = str(uuid4())
    dispatch_ttl = max(30, int(getattr(settings, "cortex_dispatch_ttl_seconds", 120)))
    claimed = try_claim_dispatch(
        redis_conn=redis_conn,
        session_key=session_key,
        dispatch_token=dispatch_token,
        ttl_seconds=dispatch_ttl,
    )
    if not claimed:
        return None

    try:
        enqueue_result = dramatiq_enqueue(
            queue_name=QUEUE_CORTEX_RUNS,
            actor_name="process_cortex_run",
            kwargs={
                "ingress": {},
                "raw_event": {},
                "drain_session_key": session_key,
                "dispatch_token": dispatch_token,
            },
        )
        if bool(enqueue_result.get("accepted")):
            return str(enqueue_result.get("job_id") or "")
        raise RuntimeError(str(enqueue_result.get("reason") or "dramatiq_enqueue_failed"))
    except Exception as exc:
        print(f"[CortexWorker] failed to enqueue dispatch session_key={session_key} error={exc}")
        release_dispatch_if_owner(
            redis_conn=redis_conn,
            session_key=session_key,
            dispatch_token=dispatch_token,
        )
        return None
