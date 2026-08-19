"""Shared queue depth helpers for Dramatiq-backed queues."""

from __future__ import annotations

from app.core.redis_queue import get_redis_connection


DRAMATIQ_NAMESPACE = "{dramatiq}"


def get_queue_depth(queue_name: str) -> int:
    """
    Return Dramatiq queue depth (pending + in-flight message bodies).

    Reads HLEN on `{dramatiq}:<queue>.msgs` which tracks message bodies that the
    broker currently owns for that queue.

    Returns:
    - `>= 0`: known depth
    - `-1`: unknown (redis unavailable/error)
    """
    normalized_queue = str(queue_name or "").strip()
    if not normalized_queue:
        return -1

    redis_conn = get_redis_connection()
    if redis_conn is None:
        return -1

    key = f"{DRAMATIQ_NAMESPACE}:{normalized_queue}.msgs"
    try:
        return int(redis_conn.hlen(key) or 0)
    except Exception:
        return -1
