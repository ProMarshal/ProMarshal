"""Dramatiq broker bootstrap for worker runtime."""

from __future__ import annotations

import os
from typing import Any

from app.core.config import settings
from app.core.redis_queue import initialize_redis

try:
    import dramatiq
    from dramatiq.brokers.redis import RedisBroker
    from dramatiq.middleware import Retries, TimeLimit
    from dramatiq.middleware.asyncio import AsyncIO
except Exception:  # pragma: no cover - dramatiq may not be installed in all envs yet
    dramatiq = None
    RedisBroker = None
    Retries = None
    TimeLimit = None
    AsyncIO = None


def _require_dramatiq() -> None:
    if dramatiq is None or RedisBroker is None:
        raise RuntimeError(
            "Dramatiq runtime is not available. Install `dramatiq[redis]` "
            "before starting Dramatiq workers."
        )


def build_broker() -> Any:
    """Build and return the Dramatiq broker instance.

    Middleware ordering requirement:
    - AsyncIO before timeout/retry enforcement middleware for async actor support.
    """
    _require_dramatiq()
    # Keep shared Redis queue/lock helpers initialized in Dramatiq worker processes.
    # Cortex worker runtime depends on get_redis_connection()/queue handles.
    try:
        initialize_redis()
    except Exception as exc:  # pragma: no cover - startup best-effort
        print(f"[Dramatiq] Redis queue bootstrap init failed: {exc}")
    # Use a Redis Cluster hash-tagged namespace so Dramatiq Lua scripts
    # operate on keys in the same hash slot (required for ElastiCache Serverless).
    broker = RedisBroker(url=settings.redis_url, namespace="{dramatiq}")

    # Explicit ordering gate from migration plan Phase A.
    broker.add_middleware(AsyncIO())
    broker.add_middleware(Retries())
    broker.add_middleware(TimeLimit())
    return broker


if dramatiq is not None:
    dramatiq.set_broker(build_broker())


# Import actors after broker setup for registration side-effects.
from app.workers import dramatiq_actors as _dramatiq_actors  # noqa: E402,F401


def get_queue_names() -> list[str]:
    """Return canonical queue names for CLI helpers."""
    return [
        "chat_interactions",
        "workitem_events",
        "chat_commands",
        "cortex_runs",
        "action_item_extraction",
        "cadence_dm",
    ]


def render_start_hint() -> str:
    """Human-readable startup hint for local use."""
    queues = " ".join(get_queue_names())
    return f"python -m dramatiq app.workers.dramatiq_app --queues {queues}"


if __name__ == "__main__":
    # This module is meant to be started via `python -m dramatiq ...`.
    print(render_start_hint())
    if os.getenv("DRAMATIQ_BOOTSTRAP_VALIDATE", "false").strip().lower() in {"1", "true", "yes", "on"}:
        _require_dramatiq()
        print("[Dramatiq] bootstrap validation succeeded.")
