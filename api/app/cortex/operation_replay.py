"""Operation-id result replay cache for non-idempotent Cortex tools."""

from __future__ import annotations

import json
from threading import Lock
from time import time
from typing import Any, Dict, Optional, Tuple

from app.core.config import settings
from app.core.redis_queue import get_redis_connection


class CortexOperationReplayCache:
    """Lookup/store tool results by operation id with a bounded TTL window."""

    _local_cache: Dict[str, Tuple[float, str]] = {}
    _local_lock = Lock()

    def __init__(
        self,
        *,
        ttl_seconds: int = 1800,
        key_prefix: str = "cortex:op:",
        allow_local_fallback: Optional[bool] = None,
    ) -> None:
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.key_prefix = key_prefix
        self.allow_local_fallback = (
            bool(getattr(settings, "cortex_allow_redis_degraded", False))
            if allow_local_fallback is None
            else bool(allow_local_fallback)
        )

    def _cache_key(self, operation_id: str) -> str:
        return f"{self.key_prefix}{operation_id}"

    def guard_status(self) -> Tuple[bool, str]:
        """
        Return Redis guard availability for non-idempotent operation replay.

        This guard must be available on mutation/security-critical paths unless
        explicit degraded mode is enabled.
        """
        redis_conn = get_redis_connection()
        if redis_conn is None:
            return False, "redis_connection_missing"
        try:
            redis_conn.ping()
            return True, "redis_available"
        except Exception as exc:
            print(f"Cortex operation replay Redis guard check failed: {exc}")
            return False, "redis_connection_unhealthy"

    def get(self, *, operation_id: str) -> Optional[Dict[str, Any]]:
        """Return cached replay payload for operation id if still valid."""
        key = self._cache_key(operation_id)
        payload = self._get_redis(key)
        if payload is None and self.allow_local_fallback:
            payload = self._get_local(key)
        if not payload:
            return None

        try:
            parsed = json.loads(payload)
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    def set(
        self,
        *,
        operation_id: str,
        value: Dict[str, Any],
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Persist replay payload with TTL, preferring Redis and falling back local."""
        key = self._cache_key(operation_id)
        ttl = max(1, int(ttl_seconds or self.ttl_seconds))
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        self._set_redis(key, payload, ttl)
        if self.allow_local_fallback:
            self._set_local(key, payload, ttl)

    def _get_redis(self, key: str) -> Optional[str]:
        redis_conn = get_redis_connection()
        if redis_conn is None:
            return None

        try:
            raw = redis_conn.get(key)
        except Exception as exc:
            print(f"Cortex operation replay Redis read failed: {exc}")
            return None

        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return str(raw)

    def _set_redis(self, key: str, payload: str, ttl_seconds: int) -> None:
        redis_conn = get_redis_connection()
        if redis_conn is None:
            return
        try:
            redis_conn.set(key, payload, ex=ttl_seconds)
        except Exception as exc:
            print(f"Cortex operation replay Redis write failed: {exc}")

    def _purge_local_expired(self, now: float) -> None:
        expired_keys = [
            cache_key
            for cache_key, (expires_at, _payload) in self._local_cache.items()
            if expires_at <= now
        ]
        for cache_key in expired_keys:
            self._local_cache.pop(cache_key, None)

    def _get_local(self, key: str) -> Optional[str]:
        now = time()
        with self._local_lock:
            self._purge_local_expired(now)
            cached = self._local_cache.get(key)
            if not cached:
                return None
            return cached[1]

    def _set_local(self, key: str, payload: str, ttl_seconds: int) -> None:
        now = time()
        with self._local_lock:
            self._purge_local_expired(now)
            self._local_cache[key] = (now + max(1, int(ttl_seconds)), payload)
