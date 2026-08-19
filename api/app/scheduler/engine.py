"""Scheduler execution engine."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.scheduler.executors import EXECUTOR_REGISTRY
from app.scheduler.repository import SchedulerRepository
from app.scheduler.timecalc import compute_next_run_at


class SchedulerEngine:
    """Coordinates due-schedule selection, leases, runs, and executor dispatch."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db
        self._repo = SchedulerRepository(db)

    async def ensure_ready(self) -> None:
        await self._repo.ensure_indexes()

    async def tick(
        self,
        *,
        job_type: str,
        now: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        current = now or datetime.utcnow()
        batch_limit = max(1, int(limit or getattr(settings, "scheduler_max_due_per_tick", 100) or 100))
        lease_ttl = max(15, int(getattr(settings, "scheduler_lease_ttl_seconds", 120) or 120))
        owner = self._repo.lease_owner_id()

        await self.ensure_ready()
        due_schedules = await self._repo.list_due_schedules(
            job_type=job_type,
            now=current,
            limit=batch_limit,
        )

        result = {
            "job_type": job_type,
            "due_count": len(due_schedules),
            "processed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "errors": [],
        }

        normalized_job_type = str(job_type).strip()
        executor = EXECUTOR_REGISTRY.get(normalized_job_type)
        if executor is None and normalized_job_type.startswith("recommendation_full_"):
            executor = EXECUTOR_REGISTRY.get("recommendation_full")
        if not executor:
            return {
                **result,
                "failed": len(due_schedules),
                "errors": [f"executor_not_found:{job_type}"],
            }

        for due in due_schedules:
            schedule_id = str(due.get("schedule_id") or "").strip()
            if not schedule_id:
                result["skipped"] += 1
                continue

            lease_until = current + timedelta(seconds=lease_ttl)
            leased = await self._repo.acquire_lease(
                schedule_id=schedule_id,
                owner=owner,
                lease_until=lease_until,
                now=current,
            )
            if not leased:
                result["skipped"] += 1
                continue

            due_at = leased.get("next_run_at") or current
            created, run_doc = await self._repo.create_run(
                schedule=leased,
                due_at=due_at,
                started_at=current,
            )
            if not created or not run_doc:
                next_run = await self._compute_next_run_at(leased, current, due_at)
                await self._repo.update_schedule_after_run(
                    schedule_id=schedule_id,
                    now=current,
                    next_run_at=next_run,
                    success=True,
                )
                await self._repo.release_lease(schedule_id=schedule_id, owner=owner)
                result["skipped"] += 1
                continue

            result["processed"] += 1
            run_id = str(run_doc.get("run_id"))
            success = False
            error_msg = None
            metrics: Dict[str, Any] = {}

            try:
                metrics = dict(await executor(self._db, leased) or {})
                success = not bool(metrics.get("failed"))
            except Exception as exc:
                success = False
                error_msg = str(exc)
                result["errors"].append(f"{schedule_id}:{error_msg}")

            finished = datetime.utcnow()
            await self._repo.complete_run(
                run_id=run_id,
                status="success" if success else "failed",
                finished_at=finished,
                metrics=metrics,
                error=error_msg,
            )

            next_run = await self._compute_next_run_at(leased, finished, due_at)
            await self._repo.update_schedule_after_run(
                schedule_id=schedule_id,
                now=finished,
                next_run_at=next_run,
                success=success,
                error=error_msg,
            )
            await self._repo.release_lease(schedule_id=schedule_id, owner=owner)

            if success:
                result["success"] += 1
            else:
                result["failed"] += 1

        return result

    async def _compute_next_run_at(
        self,
        schedule: Dict[str, Any],
        now: datetime,
        due_at: datetime,
    ) -> datetime:
        schedule_spec = dict(schedule.get("schedule_spec") or {})
        project_timezone = await self._repo.resolve_project_timezone(schedule.get("project_id"))
        return compute_next_run_at(
            schedule_spec=schedule_spec,
            now_utc=now,
            project_timezone=project_timezone,
            current_due_at=due_at,
        )
