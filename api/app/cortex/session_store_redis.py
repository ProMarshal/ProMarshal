"""Redis-backed storage for active Cortex sessions."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from redis.exceptions import WatchError

from app.core.redis_queue import get_redis_connection


_SESSION_KEY_PREFIX = "cortex:session:v1:"
_DT_MARKER = "__dt__"


def _session_key(session_key: str) -> str:
    return f"{_SESSION_KEY_PREFIX}{str(session_key or '').strip()}"


def _encode_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return {_DT_MARKER: value.isoformat()}
    if isinstance(value, list):
        return [_encode_value(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _encode_value(v) for k, v in value.items()}
    return value


def _decode_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode_value(item) for item in value]
    if isinstance(value, dict):
        marker = value.get(_DT_MARKER)
        if isinstance(marker, str):
            try:
                return datetime.fromisoformat(marker.replace("Z", "+00:00"))
            except Exception:
                return marker
        return {k: _decode_value(v) for k, v in value.items()}
    return value


class RedisCortexSessionStore:
    """Low-level Redis storage adapter for Cortex sessions."""

    def _conn(self):
        return get_redis_connection()

    @staticmethod
    def _loads(raw: Any) -> Optional[Dict[str, Any]]:
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        decoded = _decode_value(parsed)
        return decoded if isinstance(decoded, dict) else None

    @staticmethod
    def _dumps(payload: Dict[str, Any]) -> str:
        return json.dumps(_encode_value(payload), ensure_ascii=True, separators=(",", ":"))

    def get(self, *, session_key: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if conn is None:
            return None
        try:
            raw = conn.get(_session_key(session_key))
        except Exception:
            return None
        return self._loads(raw)

    def set(self, *, session_key: str, payload: Dict[str, Any], ttl_seconds: int) -> bool:
        conn = self._conn()
        if conn is None:
            return False
        try:
            conn.set(
                _session_key(session_key),
                self._dumps(payload),
                ex=max(1, int(ttl_seconds)),
            )
            return True
        except Exception:
            return False

    def delete(self, *, session_key: str) -> bool:
        conn = self._conn()
        if conn is None:
            return False
        try:
            conn.delete(_session_key(session_key))
            return True
        except Exception:
            return False

    def touch(self, *, session_key: str, ttl_seconds: int) -> bool:
        conn = self._conn()
        if conn is None:
            return False
        try:
            return bool(conn.expire(_session_key(session_key), max(1, int(ttl_seconds))))
        except Exception:
            return False

    def update_doc(
        self,
        *,
        session_key: str,
        ttl_seconds: int,
        mutator: Callable[[Dict[str, Any]], None],
    ) -> bool:
        conn = self._conn()
        if conn is None:
            return False

        key = _session_key(session_key)
        for _ in range(3):
            try:
                with conn.pipeline() as pipe:
                    pipe.watch(key)
                    raw = pipe.get(key)
                    doc = self._loads(raw)
                    if not isinstance(doc, dict):
                        pipe.reset()
                        return False
                    mutator(doc)
                    encoded = self._dumps(doc)
                    pipe.multi()
                    pipe.set(key, encoded, ex=max(1, int(ttl_seconds)))
                    pipe.execute()
                    return True
            except WatchError:
                continue
            except Exception:
                return False
        return False
