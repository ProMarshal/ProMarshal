"""Internal ops routes for canonical work-item migration."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.internal_auth import require_internal_service_auth
from app.projects.work_item_migration import (
    generate_work_item_migration_verification_report,
    get_work_item_migration_status,
    start_work_item_migration,
)

router = APIRouter(prefix="/api/ops/work-item-migration", tags=["ops"])


class WorkItemMigrationStartRequest(BaseModel):
    trigger: str = "manual"
    dry_run: bool = False
    project_limit: Optional[int] = Field(default=None, ge=1, le=10000)


@router.get("/status")
async def work_item_migration_status(
    _internal_ok: None = Depends(require_internal_service_auth),
):
    return await get_work_item_migration_status()


@router.post("/start")
async def work_item_migration_start(
    payload: WorkItemMigrationStartRequest,
    _internal_ok: None = Depends(require_internal_service_auth),
):
    return await start_work_item_migration(
        trigger=str(payload.trigger or "manual"),
        dry_run=bool(payload.dry_run),
        project_limit=payload.project_limit,
    )


@router.get("/report")
async def work_item_migration_report(
    sample_limit: int = Query(default=25, ge=1, le=200),
    persist: bool = Query(default=True),
    _internal_ok: None = Depends(require_internal_service_auth),
):
    return await generate_work_item_migration_verification_report(
        sample_limit=int(sample_limit),
        persist=bool(persist),
    )
