"""Repository for project-scoped session payloads and shared session index."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase

from app.core.session_lease import get_active_session_lease
from app.core.database import (
    ensure_project_session_indexes,
    get_project_session_collection,
    get_session_index_collection,
)


def _utcnow() -> datetime:
    return datetime.utcnow()


@dataclass(frozen=True)
class UpdateSessionResult:
    matched_count: int
    modified_count: int
    upserted: bool

    @property
    def stale_rejected(self) -> bool:
        return self.matched_count == 0 and not self.upserted

    def __bool__(self) -> bool:
        return self.modified_count > 0 or self.upserted


class SessionRepository:
    """Storage abstraction for project payloads and shared lookup index."""

    def __init__(self, db: Optional[AsyncIOMotorDatabase] = None):
        self._db = db

    def _session_index_collection(self) -> AsyncIOMotorCollection:
        if self._db is not None:
            return self._db["session_index"]
        return get_session_index_collection()

    @staticmethod
    def _base_identifier_filter(
        *,
        source: str,
        session_id: Optional[str] = None,
        session_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_source = str(source or "").strip()
        normalized_session_id = str(session_id or "").strip()
        normalized_session_key = str(session_key or "").strip()
        if not normalized_source:
            raise ValueError("source is required")
        if not normalized_session_id and not normalized_session_key:
            raise ValueError("session_id or session_key is required")
        payload: Dict[str, Any] = {"source": normalized_source}
        if normalized_session_id:
            payload["session_id"] = normalized_session_id
        if normalized_session_key:
            payload["session_key"] = normalized_session_key
        return payload

    async def _project_collection(self, project_id: str) -> AsyncIOMotorCollection:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            raise ValueError("project_id is required")
        await ensure_project_session_indexes(normalized_project_id)
        return get_project_session_collection(normalized_project_id)

    async def _resolve_project_id_from_index(
        self,
        *,
        source: str,
        session_id: Optional[str] = None,
        session_key: Optional[str] = None,
    ) -> Optional[str]:
        query = self._base_identifier_filter(source=source, session_id=session_id, session_key=session_key)
        doc = await self._session_index_collection().find_one(query, {"project_id": 1})
        project_id = str((doc or {}).get("project_id") or "").strip()
        return project_id or None

    async def _upsert_index_doc(
        self,
        *,
        entity_type: str,
        project_id: str,
        payload: Dict[str, Any],
    ) -> bool:
        source = str(payload.get("source") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        session_key = str(payload.get("session_key") or "").strip()
        if not source or (not session_id and not session_key):
            return False

        query: Dict[str, Any] = {"source": source}
        if session_id:
            query["session_id"] = session_id
        elif session_key:
            query["session_key"] = session_key

        now = _utcnow()
        index_doc = {
            "entity_type": str(entity_type or "").strip(),
            "source": source,
            "session_id": session_id or None,
            "session_key": session_key or None,
            "project_id": str(project_id or "").strip(),
            "user_id": str(payload.get("user_id") or "").strip() or None,
            "member_email": str(payload.get("member_email") or "").strip().lower() or None,
            "member_name": str(payload.get("member_name") or "").strip() or None,
            "session_type": str(payload.get("session_type") or "").strip().lower() or None,
            "provider": str(payload.get("provider") or "").strip().lower() or None,
            "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
            "external_user_id": str(payload.get("external_user_id") or "").strip() or None,
            "channel_id": str(payload.get("channel_id") or "").strip() or None,
            "correlation_id": str(payload.get("correlation_id") or "").strip() or None,
            "correlation_expected_count": payload.get("correlation_expected_count"),
            "poll_id": str(payload.get("poll_id") or "").strip() or None,
            "owner_user_id": str(payload.get("owner_user_id") or "").strip() or None,
            "member_user_id": str(payload.get("user_id") or "").strip() or None,
            "status": str(payload.get("status") or "").strip() or None,
            "terminal": bool((payload.get("outcome") or {}).get("terminal")),
            "delivery_status": str((payload.get("outcome") or {}).get("delivery_status") or "").strip().lower() or None,
            "response_status": str((payload.get("outcome") or {}).get("response_status") or "").strip().lower() or None,
            "reason_codes": list((payload.get("outcome") or {}).get("reason_codes") or []),
            "task_provider": str((payload.get("outcome") or {}).get("task_provider") or "").strip().lower() or None,
            "task_provider_mapping_present": (
                (payload.get("outcome") or {}).get("integration_health", {}).get("task_provider_mapping_present")
            ),
            "slack_mapping_present": (payload.get("outcome") or {}).get("integration_health", {}).get("slack_mapping_present"),
            "expires_at": payload.get("expires_at"),
            "completed_at": payload.get("completed_at"),
            "expired_reason": str(payload.get("expired_reason") or "").strip() or None,
            "expiry_notice_sent_at": payload.get("expiry_notice_sent_at"),
            "created_at": payload.get("created_at") or now,
            "updated_at": now,
        }
        await self._session_index_collection().update_one(
            query,
            {"$set": index_doc, "$setOnInsert": {"first_seen_at": now}},
            upsert=True,
        )
        return True

    async def create_session(
        self,
        *,
        entity_type: str,
        project_id: str,
        payload: Dict[str, Any],
    ) -> None:
        """Insert a new session document into project-scoped storage."""
        now = _utcnow()
        entity = str(entity_type or "").strip()
        normalized_project_id = str(project_id or "").strip()
        if not entity:
            raise ValueError("entity_type is required")
        if not normalized_project_id:
            raise ValueError("project_id is required")

        doc = dict(payload or {})
        doc.setdefault("created_at", now)
        doc["updated_at"] = now
        doc["project_id"] = normalized_project_id
        project_doc = dict(doc)
        project_doc["entity_type"] = entity
        collection = await self._project_collection(normalized_project_id)
        await collection.insert_one(project_doc)
        await self._upsert_index_doc(
            entity_type=entity,
            project_id=normalized_project_id,
            payload=project_doc,
        )

    async def upsert_session(
        self,
        *,
        entity_type: str,
        project_id: str,
        payload: Dict[str, Any],
        match_fields: Iterable[str],
    ) -> None:
        """Upsert session doc by selected match fields (`session_id`/`session_key`)."""
        now = _utcnow()
        entity = str(entity_type or "").strip()
        normalized_project_id = str(project_id or "").strip()
        if not entity:
            raise ValueError("entity_type is required")
        if not normalized_project_id:
            raise ValueError("project_id is required")

        doc = dict(payload or {})
        doc["project_id"] = normalized_project_id
        doc["updated_at"] = now
        source = str(doc.get("source") or "").strip()
        if not source:
            raise ValueError("source is required in payload")
        query: Dict[str, Any] = {"source": source}
        for key in match_fields:
            if key not in doc:
                raise ValueError(f"match field missing in payload: {key}")
            query[key] = doc[key]

        project_doc = dict(doc)
        project_doc["entity_type"] = entity
        update_doc = self._build_project_upsert_update_doc(
            project_doc=project_doc,
            now=now,
        )
        collection = await self._project_collection(normalized_project_id)
        await collection.update_one(
            {**query, "entity_type": entity},
            update_doc,
            upsert=True,
        )
        await self._upsert_index_doc(
            entity_type=entity,
            project_id=normalized_project_id,
            payload=project_doc,
        )

    @staticmethod
    def _build_project_upsert_update_doc(
        *,
        project_doc: Dict[str, Any],
        now: datetime,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Build an upsert update doc that avoids Mongo path conflicts.

        `created_at` must not appear in both `$set` and `$setOnInsert`.
        """
        set_doc = dict(project_doc or {})
        created_at = set_doc.pop("created_at", None) or now
        return {
            "$set": set_doc,
            "$setOnInsert": {"created_at": created_at},
        }

    async def get_session_by_id(
        self,
        *,
        entity_type: str,
        source: str,
        session_id: str,
        project_id: Optional[str] = None,
        projection: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Read by `(source, session_id)` from project-scoped payload store."""
        entity = str(entity_type or "").strip()
        normalized_source = str(source or "").strip()
        normalized_session_id = str(session_id or "").strip()
        normalized_project_id = str(project_id or "").strip()
        if not entity or not normalized_source or not normalized_session_id:
            return None

        project_value = normalized_project_id
        if not project_value:
            project_value = await self._resolve_project_id_from_index(
                source=normalized_source,
                session_id=normalized_session_id,
            ) or ""

        if not project_value:
            return None
        collection = await self._project_collection(project_value)
        return await collection.find_one(
            {"entity_type": entity, "source": normalized_source, "session_id": normalized_session_id},
            projection,
        )

    async def get_session_by_key(
        self,
        *,
        entity_type: str,
        source: str,
        session_key: str,
        project_id: Optional[str] = None,
        projection: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Read by `(source, session_key)` from project-scoped payload store."""
        entity = str(entity_type or "").strip()
        normalized_source = str(source or "").strip()
        normalized_session_key = str(session_key or "").strip()
        normalized_project_id = str(project_id or "").strip()
        if not entity or not normalized_source or not normalized_session_key:
            return None

        project_value = normalized_project_id
        if not project_value:
            project_value = await self._resolve_project_id_from_index(
                source=normalized_source,
                session_key=normalized_session_key,
            ) or ""

        if not project_value:
            return None
        collection = await self._project_collection(project_value)
        return await collection.find_one(
            {"entity_type": entity, "source": normalized_source, "session_key": normalized_session_key},
            projection,
        )

    async def update_session_by_id(
        self,
        *,
        entity_type: str,
        source: str,
        session_id: str,
        update_doc: Dict[str, Any],
        project_id: Optional[str] = None,
        upsert: bool = False,
        filter_extension: Optional[Dict[str, Any]] = None,
    ) -> UpdateSessionResult:
        """
        Update one session by `(source, session_id)` in project-scoped storage.

        Returns an UpdateSessionResult that preserves legacy bool semantics.
        """
        entity = str(entity_type or "").strip()
        normalized_source = str(source or "").strip()
        normalized_session_id = str(session_id or "").strip()
        normalized_project_id = str(project_id or "").strip()
        if not entity or not normalized_source or not normalized_session_id:
            return UpdateSessionResult(matched_count=0, modified_count=0, upserted=False)
        if not isinstance(update_doc, dict) or not update_doc:
            return UpdateSessionResult(matched_count=0, modified_count=0, upserted=False)

        resolved_project_id = normalized_project_id
        if not resolved_project_id:
            resolved_project_id = await self._resolve_project_id_from_index(
                source=normalized_source,
                session_id=normalized_session_id,
            ) or ""
        if not resolved_project_id:
            return UpdateSessionResult(matched_count=0, modified_count=0, upserted=False)

        filter_doc: Dict[str, Any] = {
            "entity_type": entity,
            "source": normalized_source,
            "session_id": normalized_session_id,
        }
        if isinstance(filter_extension, dict) and filter_extension:
            filter_doc.update(filter_extension)

        resolved_update_doc: Dict[str, Any] = dict(update_doc)
        active_lease = get_active_session_lease()
        if (
            active_lease is not None
            and entity == "cadence_session"
            and normalized_source == "cadence"
            and normalized_session_id == str(active_lease.session_id or "").strip()
            and int(active_lease.lease_token or 0) > 0
        ):
            if bool(active_lease.enforce):
                filter_doc["lease_version"] = int(active_lease.lease_token)
            set_doc = resolved_update_doc.get("$set")
            if not isinstance(set_doc, dict):
                set_doc = {}
                resolved_update_doc["$set"] = set_doc
            set_doc["lease_version"] = int(active_lease.lease_token)

        collection = await self._project_collection(resolved_project_id)
        result = await collection.update_one(
            filter_doc,
            resolved_update_doc,
            upsert=upsert,
        )
        update_result = UpdateSessionResult(
            matched_count=int(result.matched_count or 0),
            modified_count=int(result.modified_count or 0),
            upserted=bool(result.upserted_id),
        )

        latest = await collection.find_one(
            {
                "entity_type": entity,
                "source": normalized_source,
                "session_id": normalized_session_id,
            }
        )
        if isinstance(latest, dict):
            await self._upsert_index_doc(
                entity_type=entity,
                project_id=resolved_project_id,
                payload=latest,
            )

        return update_result

    async def find_active_session(
        self,
        *,
        entity_type: str,
        source: str,
        user_id: str,
        project_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Find most recent active session for user+project+source.

        Uses `session_index` as the primary query source.
        """
        entity = str(entity_type or "").strip()
        normalized_source = str(source or "").strip()
        normalized_user_id = str(user_id or "").strip()
        normalized_project_id = str(project_id or "").strip()
        if not entity or not normalized_source or not normalized_user_id or not normalized_project_id:
            return None

        now = _utcnow()
        index_doc = await self._session_index_collection().find_one(
            {
                "entity_type": entity,
                "source": normalized_source,
                "user_id": normalized_user_id,
                "project_id": normalized_project_id,
                "status": {"$ne": "completed"},
                "expires_at": {"$gt": now},
            },
            sort=[("updated_at", -1)],
        )
        if not index_doc:
            return None

        session_id = str(index_doc.get("session_id") or "").strip()
        session_key = str(index_doc.get("session_key") or "").strip()
        if session_id:
            return await self.get_session_by_id(
                entity_type=entity,
                source=normalized_source,
                session_id=session_id,
                project_id=normalized_project_id,
            )
        if session_key:
            return await self.get_session_by_key(
                entity_type=entity,
                source=normalized_source,
                session_key=session_key,
                project_id=normalized_project_id,
            )
        return None

    async def _load_session_from_index_row(
        self,
        *,
        entity_type: str,
        source: str,
        index_row: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        normalized_project_id = str((index_row or {}).get("project_id") or "").strip()
        session_id = str((index_row or {}).get("session_id") or "").strip()
        session_key = str((index_row or {}).get("session_key") or "").strip()
        if session_id:
            return await self.get_session_by_id(
                entity_type=entity_type,
                source=source,
                session_id=session_id,
                project_id=normalized_project_id or None,
            )
        if session_key:
            return await self.get_session_by_key(
                entity_type=entity_type,
                source=source,
                session_key=session_key,
                project_id=normalized_project_id or None,
            )
        return None

    async def list_active_sessions_by_identity(
        self,
        *,
        entity_type: str,
        source: str,
        provider: str,
        workspace_id: str,
        external_user_id: str,
        channel_id: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """List active sessions for provider/workspace/external user identity."""
        entity = str(entity_type or "").strip()
        normalized_source = str(source or "").strip()
        normalized_provider = str(provider or "").strip().lower()
        normalized_workspace = str(workspace_id or "").strip()
        normalized_external_user_id = str(external_user_id or "").strip()
        normalized_channel_id = str(channel_id or "").strip() or None
        safe_limit = max(1, min(int(limit or 5), 20))
        if not (
            entity
            and normalized_source
            and normalized_provider
            and normalized_workspace
            and normalized_external_user_id
        ):
            return []

        now = _utcnow()
        query: Dict[str, Any] = {
            "entity_type": entity,
            "source": normalized_source,
            "provider": normalized_provider,
            "workspace_id": normalized_workspace,
            "external_user_id": normalized_external_user_id,
            "status": {"$ne": "completed"},
            "expires_at": {"$gt": now},
        }
        if normalized_channel_id:
            query["channel_id"] = normalized_channel_id

        index_rows = await self._session_index_collection().find(query).sort(
            [("updated_at", -1)]
        ).limit(safe_limit).to_list(length=safe_limit)
        hydrated: List[Dict[str, Any]] = []
        for row in index_rows:
            loaded = await self._load_session_from_index_row(
                entity_type=entity,
                source=normalized_source,
                index_row=row,
            )
            if isinstance(loaded, dict):
                hydrated.append(loaded)
        return hydrated

    async def find_latest_timeout_session_by_identity(
        self,
        *,
        entity_type: str,
        source: str,
        provider: str,
        workspace_id: str,
        external_user_id: str,
        lookback_hours: int = 48,
    ) -> Optional[Dict[str, Any]]:
        """Return latest completed timeout session for identity if within lookback window."""
        entity = str(entity_type or "").strip()
        normalized_source = str(source or "").strip()
        normalized_provider = str(provider or "").strip().lower()
        normalized_workspace = str(workspace_id or "").strip()
        normalized_external_user_id = str(external_user_id or "").strip()
        if not (
            entity
            and normalized_source
            and normalized_provider
            and normalized_workspace
            and normalized_external_user_id
        ):
            return None

        query: Dict[str, Any] = {
            "entity_type": entity,
            "source": normalized_source,
            "provider": normalized_provider,
            "workspace_id": normalized_workspace,
            "external_user_id": normalized_external_user_id,
            "status": "completed",
            "expired_reason": "timeout",
        }
        safe_lookback_hours = max(1, int(lookback_hours or 48))
        query["completed_at"] = {"$gte": _utcnow() - timedelta(hours=safe_lookback_hours)}

        index_row = await self._session_index_collection().find_one(
            query,
            sort=[("completed_at", -1), ("updated_at", -1)],
        )
        if not isinstance(index_row, dict):
            return None
        return await self._load_session_from_index_row(
            entity_type=entity,
            source=normalized_source,
            index_row=index_row,
        )

    async def list_session_index(
        self,
        *,
        query: Dict[str, Any],
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """List session index rows for bulk operations."""
        safe_limit = max(1, min(int(limit or 1000), 5000))
        cursor = self._session_index_collection().find(dict(query or {})).limit(safe_limit)
        return await cursor.to_list(length=safe_limit)
