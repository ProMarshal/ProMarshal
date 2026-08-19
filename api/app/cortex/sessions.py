"""Redis-backed Cortex session lifecycle management."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.cortex.session_store_redis import RedisCortexSessionStore


CORTEX_ENTITY_TYPE = "cortex_session"
SOURCE_IDLE_SECONDS = {
    "slack_channel": 4 * 60 * 60,
    "slack_dm": 30 * 60,
    "web": 30 * 60,
}


@dataclass
class CortexSession:
    """Canonical Cortex session shape."""

    session_key: str
    project_id: str
    user_id: str
    source: str
    anchor: str
    created_at: datetime
    last_active_at: datetime
    expires_at: datetime
    updated_at: datetime
    turn_count: int = 0
    recent_turns: List[Dict[str, Any]] = field(default_factory=list)
    summary_history: List[Dict[str, Any]] = field(default_factory=list)
    pending_question: Optional[Dict[str, Any]] = None
    pending_confirmation: Optional[Dict[str, Any]] = None
    pending_plan: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    committed_actions: List[Dict[str, Any]] = field(default_factory=list)
    transcript: List[Dict[str, Any]] = field(default_factory=list)


def source_idle_timeout_seconds(source: str) -> int:
    """Return source-specific idle timeout in seconds."""
    return SOURCE_IDLE_SECONDS.get(source, SOURCE_IDLE_SECONDS["slack_channel"])


def build_session_key(project_id: str, user_id: str, source: str, anchor: str) -> str:
    """Build canonical session key by source and anchor."""
    if source not in SOURCE_IDLE_SECONDS:
        raise ValueError(f"Unsupported session source: {source}")
    if not project_id or not user_id or not anchor:
        raise ValueError("project_id, user_id, and anchor are required")
    return f"{project_id}:{user_id}:{anchor}"


class CortexSessionManager:
    """Session lifecycle management for create/load/update operations."""

    def __init__(self, db: Optional[AsyncIOMotorDatabase] = None):
        self._db = db
        self._redis = RedisCortexSessionStore()

    async def _ensure_indexes(self) -> None:
        return None

    def _new_expiry(self, source: str, now: datetime) -> datetime:
        return now + timedelta(seconds=source_idle_timeout_seconds(source))

    @staticmethod
    def _parse_datetime(value: Any, *, fallback: datetime) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                return fallback
        return fallback

    def _to_session(self, doc: Dict[str, Any]) -> CortexSession:
        now = datetime.utcnow()
        return CortexSession(
            session_key=str(doc.get("session_key") or doc.get("session_id") or ""),
            project_id=str(doc.get("project_id") or ""),
            user_id=str(doc.get("user_id") or ""),
            source=str(doc.get("source") or "slack_channel"),
            anchor=str(doc.get("anchor") or ""),
            created_at=self._parse_datetime(doc.get("created_at"), fallback=now),
            last_active_at=self._parse_datetime(doc.get("last_active_at"), fallback=now),
            expires_at=self._parse_datetime(doc.get("expires_at"), fallback=now),
            updated_at=self._parse_datetime(doc.get("updated_at"), fallback=now),
            turn_count=int(doc.get("turn_count", 0) or 0),
            recent_turns=list(doc.get("recent_turns") or []),
            summary_history=list(doc.get("summary_history") or []),
            pending_question=doc.get("pending_question"),
            pending_confirmation=doc.get("pending_confirmation"),
            pending_plan=doc.get("pending_plan"),
            metadata=dict(doc.get("metadata") or {}),
            committed_actions=list(doc.get("committed_actions") or []),
            transcript=list(doc.get("transcript") or []),
        )

    def _new_session_doc(
        self,
        *,
        session_key: str,
        project_id: str,
        user_id: str,
        source: str,
        anchor: str,
        now: datetime,
    ) -> Dict[str, Any]:
        expires_at = self._new_expiry(source, now)
        return {
            "entity_type": CORTEX_ENTITY_TYPE,
            "session_id": session_key,
            "session_key": session_key,
            "project_id": project_id,
            "user_id": user_id,
            "source": source,
            "anchor": anchor,
            "turn_count": 0,
            "recent_turns": [],
            "summary_history": [],
            "pending_question": None,
            "pending_confirmation": None,
            "pending_plan": None,
            "metadata": {},
            "committed_actions": [],
            "transcript": [],
            "created_at": now,
            "last_active_at": now,
            "expires_at": expires_at,
            "updated_at": now,
        }

    @staticmethod
    def _remaining_ttl_seconds(doc: Dict[str, Any], *, fallback_source: str) -> int:
        now = datetime.utcnow()
        expires_at = doc.get("expires_at")
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except Exception:
                expires_at = None
        if isinstance(expires_at, datetime):
            ttl = int((expires_at - now).total_seconds())
            if ttl > 0:
                return ttl
        source = str(doc.get("source") or fallback_source or "slack_channel")
        return max(1, source_idle_timeout_seconds(source))

    def _get_active_from_redis(self, *, session_key: str) -> Optional[Dict[str, Any]]:
        doc = self._redis.get(session_key=session_key)
        if not isinstance(doc, dict):
            return None
        now = datetime.utcnow()
        expires_at = doc.get("expires_at")
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except Exception:
                expires_at = None
        if isinstance(expires_at, datetime) and expires_at <= now:
            self._redis.delete(session_key=session_key)
            return None
        return doc

    async def load_or_create_session(
        self,
        *,
        session_key: str,
        project_id: str,
        user_id: str,
        source: str,
        anchor: str,
    ) -> CortexSession:
        """Load existing non-expired session or create/reset one."""
        await self._ensure_indexes()

        if source not in SOURCE_IDLE_SECONDS:
            raise ValueError(f"Unsupported source: {source}")

        now = datetime.utcnow()
        existing = self._get_active_from_redis(session_key=session_key)

        if existing:
            refreshed_expires_at = self._new_expiry(str(existing.get("source") or source), now)
            existing["last_active_at"] = now
            existing["expires_at"] = refreshed_expires_at
            existing["updated_at"] = now
            ttl_seconds = max(1, int((refreshed_expires_at - now).total_seconds()))
            self._redis.set(
                session_key=session_key,
                payload=existing,
                ttl_seconds=ttl_seconds,
            )
            latest = self._get_active_from_redis(session_key=session_key) or existing
            return self._to_session(latest)

        session_doc = self._new_session_doc(
            session_key=session_key,
            project_id=project_id,
            user_id=user_id,
            source=source,
            anchor=anchor,
            now=now,
        )
        self._redis.set(
            session_key=session_key,
            payload=session_doc,
            ttl_seconds=source_idle_timeout_seconds(source),
        )
        stored = self._get_active_from_redis(session_key=session_key) or session_doc
        return self._to_session(stored)

    async def get_session(self, *, session_key: str) -> Optional[CortexSession]:
        """Return non-expired session by key, else None."""
        await self._ensure_indexes()
        doc = self._get_active_from_redis(session_key=session_key)
        if isinstance(doc, dict):
            return self._to_session(doc)
        return None

    async def update_session_fields(
        self,
        *,
        session_key: str,
        fields: Dict[str, Any],
    ) -> bool:
        """Update selected session fields and refresh updated timestamp."""
        await self._ensure_indexes()
        if not fields:
            return False
        doc = self._get_active_from_redis(session_key=session_key)
        if not isinstance(doc, dict):
            return False

        payload_fields = dict(fields)
        payload_fields["updated_at"] = datetime.utcnow()
        ttl_seconds = self._remaining_ttl_seconds(
            doc,
            fallback_source=str(doc.get("source") or "slack_channel"),
        )

        return self._redis.update_doc(
            session_key=session_key,
            ttl_seconds=ttl_seconds,
            mutator=lambda row: row.update(payload_fields),
        )

    async def append_transcript_event(
        self,
        *,
        session_key: str,
        event: Dict[str, Any],
    ) -> None:
        """Append transcript event and keep recent turns window bounded."""
        await self._ensure_indexes()
        doc = self._get_active_from_redis(session_key=session_key)
        if not isinstance(doc, dict):
            return

        now = datetime.utcnow()
        source = str(doc.get("source") or "slack_channel")
        expires_at = self._new_expiry(source, now)
        ttl_seconds = max(1, int((expires_at - now).total_seconds()))
        recent_window = max(1, int(settings.cortex_recent_turns_window))
        event_type = str(event.get("event_type", ""))
        turn_increment = 1 if event_type == "user_message" else 0

        def _mutate(row: Dict[str, Any]) -> None:
            transcript = list(row.get("transcript") or [])
            transcript.append(event)
            recent = list(row.get("recent_turns") or [])
            recent.append(event)
            row["transcript"] = transcript
            row["recent_turns"] = recent[-recent_window:]
            row["turn_count"] = int(row.get("turn_count", 0) or 0) + turn_increment
            row["last_active_at"] = now
            row["expires_at"] = expires_at
            row["updated_at"] = now

        self._redis.update_doc(
            session_key=session_key,
            ttl_seconds=ttl_seconds,
            mutator=_mutate,
        )

    async def set_pending_question(
        self,
        *,
        session_key: str,
        pending_question: Optional[Dict[str, Any]],
    ) -> bool:
        """Set or clear pending question state."""
        return await self.update_session_fields(
            session_key=session_key,
            fields={"pending_question": pending_question},
        )

    async def set_pending_confirmation(
        self,
        *,
        session_key: str,
        pending_confirmation: Optional[Dict[str, Any]],
    ) -> bool:
        """Set or clear pending confirmation state."""
        return await self.update_session_fields(
            session_key=session_key,
            fields={"pending_confirmation": pending_confirmation},
        )

    async def set_pending_plan(
        self,
        *,
        session_key: str,
        pending_plan: Optional[Dict[str, Any]],
    ) -> bool:
        """Set or clear pending multi-step action plan state."""
        return await self.update_session_fields(
            session_key=session_key,
            fields={"pending_plan": pending_plan},
        )

    async def touch_session(self, *, session_key: str) -> None:
        """Refresh last-active timestamp and expiry."""
        await self._ensure_indexes()
        doc = self._get_active_from_redis(session_key=session_key)
        if not isinstance(doc, dict):
            return

        now = datetime.utcnow()
        source = str(doc.get("source") or "slack_channel")
        expires_at = self._new_expiry(source, now)
        ttl_seconds = max(1, int((expires_at - now).total_seconds()))

        self._redis.update_doc(
            session_key=session_key,
            ttl_seconds=ttl_seconds,
            mutator=lambda row: row.update(
                {
                    "last_active_at": now,
                    "expires_at": expires_at,
                    "updated_at": now,
                }
            ),
        )
