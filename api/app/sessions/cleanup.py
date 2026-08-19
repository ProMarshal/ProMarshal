"""Unified cleanup routines for Cadence/Team Poll session artifacts."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.database import get_brain_collection


def _utcnow() -> datetime:
    return datetime.utcnow()


async def run_unified_session_cleanup(
    db: AsyncIOMotorDatabase,
    *,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """
    Clean stale session artifacts for Cadence and Team Poll.

    Scope:
    - Delete stale `session_index` rows for `cadence_session` and `team_poll_session`.
    - Delete aged `cadence_session` docs from project Brain collections.
    - Delete terminal/expired `team_poll_session` docs from project Brain collections.
    - Keep `cadence_daily_summary` untouched.
    """
    started_at = now or _utcnow()

    index_grace_minutes = max(5, int(getattr(settings, "session_cleanup_index_grace_minutes", 60) or 60))
    cadence_index_grace_minutes = max(
        5, int(getattr(settings, "session_cleanup_cadence_index_grace_minutes", 240) or 240)
    )
    cadence_retention_days = max(1, int(getattr(settings, "session_cleanup_cadence_retention_days", 7) or 7))
    team_poll_grace_minutes = max(
        5, int(getattr(settings, "team_poll_session_terminal_grace_minutes", 60) or 60)
    )

    index_cutoff = started_at - timedelta(minutes=index_grace_minutes)
    cadence_index_cutoff = started_at - timedelta(minutes=cadence_index_grace_minutes)
    team_poll_index_cutoff = started_at - timedelta(minutes=team_poll_grace_minutes)
    cadence_cutoff = started_at - timedelta(days=cadence_retention_days)
    team_poll_doc_cutoff = started_at - timedelta(minutes=team_poll_grace_minutes)

    session_index = db["session_index"]

    # Cadence index rows are deleted only after they are terminal.
    # This preserves non-terminal rows for timeout finalization jobs.
    cadence_index_query = {
        "entity_type": "cadence_session",
        "$and": [
            {
                "$or": [
                    {"terminal": True},
                    {"status": "completed"},
                ],
            },
            {"updated_at": {"$lte": cadence_index_cutoff}},
        ],
    }
    team_poll_index_query = {
        "entity_type": "team_poll_session",
        "$or": [
            {"terminal": True, "updated_at": {"$lte": team_poll_index_cutoff}},
            {"status": "completed", "updated_at": {"$lte": team_poll_index_cutoff}},
            {"expires_at": {"$lte": team_poll_index_cutoff}},
        ],
    }
    team_poll_project_ids = await session_index.distinct("project_id", team_poll_index_query)
    index_query = {
        "entity_type": {"$in": ["cadence_session", "team_poll_session"]},
        "$or": [
            cadence_index_query,
            team_poll_index_query,
        ],
    }
    index_delete_result = await session_index.delete_many(index_query)

    project_ids = await db.projects.distinct("project_id")
    cadence_docs_deleted = 0
    team_poll_docs_deleted = 0
    cadence_projects_touched = 0
    team_poll_projects_touched = 0

    cadence_doc_query = {
        "entity_type": "cadence_session",
        "$or": [
            {"completed_at": {"$lte": cadence_cutoff}},
            {"expires_at": {"$lte": cadence_cutoff}},
        ],
    }
    team_poll_doc_query = {
        "entity_type": "team_poll_session",
        "$or": [
            {"completed_at": {"$lte": team_poll_doc_cutoff}},
            {"expires_at": {"$lte": team_poll_doc_cutoff}},
        ],
    }

    for project_id in project_ids:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            continue
        collection = get_brain_collection(normalized_project_id)

        result = await collection.delete_many(cadence_doc_query)
        deleted_count = int(result.deleted_count or 0)
        if deleted_count > 0:
            cadence_projects_touched += 1
            cadence_docs_deleted += deleted_count

    # Team poll cleanup is index-driven to avoid broad full-project scans.
    for project_id in team_poll_project_ids:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            continue
        collection = get_brain_collection(normalized_project_id)
        team_poll_result = await collection.delete_many(team_poll_doc_query)
        team_poll_deleted_count = int(team_poll_result.deleted_count or 0)
        if team_poll_deleted_count > 0:
            team_poll_projects_touched += 1
            team_poll_docs_deleted += team_poll_deleted_count

    completed_at = _utcnow()
    return {
        "status": "success",
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_ms": int((completed_at - started_at).total_seconds() * 1000),
        "cutoffs": {
            "session_index_before": index_cutoff.isoformat(),
            "cadence_session_index_before": cadence_index_cutoff.isoformat(),
            "team_poll_session_index_before": team_poll_index_cutoff.isoformat(),
            "cadence_sessions_before": cadence_cutoff.isoformat(),
            "team_poll_sessions_before": team_poll_doc_cutoff.isoformat(),
        },
        "deleted": {
            "session_index_rows": int(index_delete_result.deleted_count or 0),
            "cadence_session_docs": cadence_docs_deleted,
            "cadence_projects_touched": cadence_projects_touched,
            "team_poll_session_docs": team_poll_docs_deleted,
            "team_poll_projects_touched": team_poll_projects_touched,
        },
        "config": {
            "session_cleanup_index_grace_minutes": index_grace_minutes,
            "session_cleanup_cadence_index_grace_minutes": cadence_index_grace_minutes,
            "session_cleanup_cadence_retention_days": cadence_retention_days,
            "team_poll_session_terminal_grace_minutes": team_poll_grace_minutes,
        },
    }
