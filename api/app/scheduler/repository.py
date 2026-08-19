"""Mongo repository for centralized scheduler collections."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.scheduler.timecalc import due_bucket_key


class SchedulerRepository:
    """Encapsulates DB operations for project_schedules and schedule_runs."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db
        self._schedules = db["project_schedules"]
        self._runs = db["schedule_runs"]

    async def ensure_indexes(self) -> None:
        await self._schedules.create_index(
            [("schedule_id", 1)],
            unique=True,
            name="project_schedules_schedule_id_uq_idx",
        )
        await self._schedules.create_index(
            [("enabled", 1), ("next_run_at", 1), ("job_type", 1)],
            name="project_schedules_enabled_next_job_idx",
        )
        await self._schedules.create_index(
            [("project_id", 1), ("job_type", 1)],
            unique=True,
            name="project_schedules_project_job_uq_idx",
        )
        await self._runs.create_index(
            [("run_id", 1)],
            unique=True,
            name="schedule_runs_run_id_uq_idx",
        )
        await self._runs.create_index(
            [("idempotency_key", 1)],
            unique=True,
            name="schedule_runs_idempotency_uq_idx",
        )
        await self._runs.create_index(
            [("schedule_id", 1), ("created_at", -1)],
            name="schedule_runs_schedule_created_idx",
        )
        await self._runs.create_index(
            [("created_at", 1)],
            name="schedule_runs_created_at_cleanup_idx",
        )

    async def upsert_schedule(
        self,
        *,
        job_type: str,
        project_id: Optional[str],
        schedule_spec: Dict[str, Any],
        next_run_at: datetime,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        now = datetime.utcnow()
        normalized_project_id = str(project_id or "").strip() or None
        doc = await self._schedules.find_one_and_update(
            {
                "job_type": str(job_type).strip(),
                "project_id": normalized_project_id,
            },
            {
                "$set": {
                    "job_type": str(job_type).strip(),
                    "project_id": normalized_project_id,
                    "enabled": bool(enabled),
                    "schedule_spec": dict(schedule_spec or {}),
                    "next_run_at": next_run_at,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "schedule_id": str(uuid.uuid4()),
                    "created_at": now,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return doc

    async def get_schedule(self, *, job_type: str, project_id: Optional[str]) -> Optional[Dict[str, Any]]:
        normalized_project_id = str(project_id or "").strip() or None
        return await self._schedules.find_one(
            {
                "job_type": str(job_type).strip(),
                "project_id": normalized_project_id,
            }
        )

    async def delete_schedule(self, *, job_type: str, project_id: Optional[str]) -> int:
        normalized_project_id = str(project_id or "").strip() or None
        result = await self._schedules.delete_one(
            {
                "job_type": str(job_type).strip(),
                "project_id": normalized_project_id,
            }
        )
        return int(result.deleted_count or 0)

    async def list_due_schedules(
        self,
        *,
        job_type: str,
        now: datetime,
        limit: int,
    ) -> List[Dict[str, Any]]:
        cursor = self._schedules.find(
            {
                "job_type": str(job_type).strip(),
                "enabled": True,
                "next_run_at": {"$lte": now},
            }
        ).sort("next_run_at", 1).limit(max(1, int(limit)))
        return await cursor.to_list(length=max(1, int(limit)))

    async def acquire_lease(
        self,
        *,
        schedule_id: str,
        owner: str,
        lease_until: datetime,
        now: datetime,
    ) -> Optional[Dict[str, Any]]:
        return await self._schedules.find_one_and_update(
            {
                "schedule_id": str(schedule_id).strip(),
                "enabled": True,
                "next_run_at": {"$lte": now},
                "$or": [
                    {"lease_until": {"$exists": False}},
                    {"lease_until": None},
                    {"lease_until": {"$lte": now}},
                ],
            },
            {
                "$set": {
                    "lease_owner": owner,
                    "lease_until": lease_until,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def release_lease(self, *, schedule_id: str, owner: str) -> None:
        await self._schedules.update_one(
            {
                "schedule_id": str(schedule_id).strip(),
                "lease_owner": str(owner).strip(),
            },
            {
                "$unset": {
                    "lease_owner": "",
                    "lease_until": "",
                }
            },
        )

    async def create_run(
        self,
        *,
        schedule: Dict[str, Any],
        due_at: datetime,
        started_at: datetime,
        attempt: int = 1,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        schedule_id = str(schedule.get("schedule_id") or "").strip()
        if not schedule_id:
            return False, None
        idempotency_key = f"{schedule_id}:{due_bucket_key(due_at)}"
        run_doc = {
            "run_id": str(uuid.uuid4()),
            "schedule_id": schedule_id,
            "due_at": due_at,
            "started_at": started_at,
            "finished_at": None,
            "status": "running",
            "attempt": max(1, int(attempt)),
            "error": None,
            "metrics": {},
            "idempotency_key": idempotency_key,
            "created_at": started_at,
        }
        try:
            await self._runs.insert_one(run_doc)
            return True, run_doc
        except DuplicateKeyError:
            return False, None

    async def complete_run(
        self,
        *,
        run_id: str,
        status: str,
        finished_at: datetime,
        metrics: Optional[Dict[str, Any]] = None,
        error: Optional[Any] = None,
    ) -> None:
        await self._runs.update_one(
            {"run_id": str(run_id).strip()},
            {
                "$set": {
                    "status": str(status).strip().lower(),
                    "finished_at": finished_at,
                    "metrics": dict(metrics or {}),
                    "error": error,
                }
            },
        )

    async def update_schedule_after_run(
        self,
        *,
        schedule_id: str,
        now: datetime,
        next_run_at: datetime,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        update: Dict[str, Any] = {
            "last_run_at": now,
            "next_run_at": next_run_at,
            "updated_at": now,
            "last_error": None if success else str(error or ""),
        }
        if success:
            update["last_success_at"] = now
        await self._schedules.update_one(
            {"schedule_id": str(schedule_id).strip()},
            {"$set": update},
        )

    async def resolve_project_timezone(self, project_id: Optional[str]) -> str:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            return "UTC"
        project = await self._db.projects.find_one(
            {"project_id": normalized_project_id},
            {"timezone": 1},
        )
        timezone = str((project or {}).get("timezone") or "UTC").strip() or "UTC"
        return timezone

    async def prune_schedule_runs_before(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> Dict[str, int]:
        """
        Delete old scheduler run history rows in bounded batches.

        Returns metrics for matched/selected/deleted rows.
        """
        safe_limit = max(1, int(limit or 1))
        base_query = {"created_at": {"$lt": cutoff}}
        matched = int(await self._runs.count_documents(base_query) or 0)
        if matched <= 0:
            return {"matched": 0, "selected": 0, "deleted": 0}

        candidates = await self._runs.find(
            base_query,
            {"run_id": 1},
        ).sort("created_at", 1).limit(safe_limit).to_list(length=safe_limit)
        run_ids = [
            str(item.get("run_id") or "").strip()
            for item in candidates
            if str(item.get("run_id") or "").strip()
        ]
        if not run_ids:
            return {"matched": matched, "selected": 0, "deleted": 0}

        result = await self._runs.delete_many({"run_id": {"$in": run_ids}})
        return {
            "matched": matched,
            "selected": len(run_ids),
            "deleted": int(result.deleted_count or 0),
        }

    async def prune_channel_index_before(
        self,
        *,
        cutoff: datetime,
        limit: int,
        active_project_only_null: bool = True,
    ) -> Dict[str, int]:
        """
        Delete stale channel_index rows in bounded batches.

        Phase-1 safety: default to pruning only rows with active_project_id == null/missing.
        """
        safe_limit = max(1, int(limit or 1))
        base_query: Dict[str, Any] = {"updated_at": {"$lt": cutoff}}
        if active_project_only_null:
            base_query["$or"] = [
                {"active_project_id": {"$exists": False}},
                {"active_project_id": None},
                {"active_project_id": ""},
            ]
        channel_index = self._db["channel_index"]
        matched = int(await channel_index.count_documents(base_query) or 0)
        if matched <= 0:
            return {"matched": 0, "selected": 0, "deleted": 0}

        candidates = await channel_index.find(
            base_query,
            {"_id": 1},
        ).sort("updated_at", 1).limit(safe_limit).to_list(length=safe_limit)
        object_ids = [item.get("_id") for item in candidates if item.get("_id") is not None]
        if not object_ids:
            return {"matched": matched, "selected": 0, "deleted": 0}
        result = await channel_index.delete_many({"_id": {"$in": object_ids}})
        return {
            "matched": matched,
            "selected": len(object_ids),
            "deleted": int(result.deleted_count or 0),
        }

    @staticmethod
    def lease_owner_id() -> str:
        host = str(os.getenv("RENDER_SERVICE_ID") or os.getenv("HOSTNAME") or "local").strip()
        pid = os.getpid()
        return f"{host}:{pid}"
