#!/usr/bin/env python
"""
Backfill `active_provider` on existing project documents.

Policy:
- Keep current active_provider when it is connected.
- If current active_provider is missing/invalid and exactly one PM provider is connected,
  set active_provider to that provider.
- Otherwise set active_provider to null.

This script is idempotent and safe to run repeatedly.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import (  # noqa: E402
    close_mongo_connection,
    connect_to_mongo,
    get_database,
)
from app.domain.capabilities.provider_selection_policy import (  # noqa: E402
    connected_pm_providers_from_project_context,
    normalize_provider,
)


def _normalize(value: Any) -> str:
    return str(value or "").strip()


def _recommended_active_provider(project: Dict[str, Any]) -> str | None:
    connected = connected_pm_providers_from_project_context(project)
    current_raw = normalize_provider((project or {}).get("active_provider"))
    if current_raw and current_raw in set(connected):
        return current_raw
    if len(connected) == 1:
        return connected[0]
    return None


def _needs_update(*, project: Dict[str, Any], recommended: str | None) -> bool:
    has_field = "active_provider" in (project or {})
    current_raw = normalize_provider((project or {}).get("active_provider"))
    current = current_raw or None
    if not has_field:
        return True
    return current != recommended


async def _target_projects(project_id: str) -> List[Dict[str, Any]]:
    db = get_database()
    if db is None:
        return []

    normalized_project_id = _normalize(project_id)
    query: Dict[str, Any] = {}
    if normalized_project_id:
        query["project_id"] = normalized_project_id

    cursor = db.projects.find(
        query,
        {
            "_id": 1,
            "project_id": 1,
            "integrations": 1,
            "active_provider": 1,
        },
    )
    return await cursor.to_list(length=None)


async def backfill(*, project_id: str, dry_run: bool) -> Dict[str, int]:
    stats: Dict[str, int] = {
        "projects_scanned": 0,
        "projects_with_connected_pm_provider": 0,
        "projects_missing_active_provider_field": 0,
        "projects_missing_or_invalid_active_provider": 0,
        "projects_updated": 0,
        "projects_already_compliant": 0,
    }

    db = get_database()
    if db is None:
        return stats

    projects = await _target_projects(project_id)
    for project in projects:
        stats["projects_scanned"] += 1

        connected = connected_pm_providers_from_project_context(project)
        if connected:
            stats["projects_with_connected_pm_provider"] += 1

        if "active_provider" not in project:
            stats["projects_missing_active_provider_field"] += 1

        recommended = _recommended_active_provider(project)
        current_raw = normalize_provider(project.get("active_provider"))
        current = current_raw or None
        if current != recommended:
            stats["projects_missing_or_invalid_active_provider"] += 1

        if not _needs_update(project=project, recommended=recommended):
            stats["projects_already_compliant"] += 1
            continue

        if dry_run:
            stats["projects_updated"] += 1
            continue

        result = await db.projects.update_one(
            {"_id": project["_id"]},
            {
                "$set": {
                    "active_provider": recommended,
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        if int(result.modified_count or 0) > 0:
            stats["projects_updated"] += 1

    return stats


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill `active_provider` for existing projects."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform writes. Without this flag, runs in dry-run mode.",
    )
    parser.add_argument(
        "--project-id",
        default="",
        help="Optional project_id. If omitted, scans all projects.",
    )
    return parser


async def _run() -> int:
    args = _build_parser().parse_args()
    dry_run = not bool(args.execute)
    target = _normalize(args.project_id) or "all"

    print(
        "Active-provider backfill start: "
        f"mode={'dry_run' if dry_run else 'execute'} project_id={target}"
    )
    await connect_to_mongo()
    try:
        stats = await backfill(project_id=args.project_id, dry_run=dry_run)
    finally:
        await close_mongo_connection()

    print("Active-provider backfill summary:")
    for key in sorted(stats.keys()):
        print(f"  {key}: {stats[key]}")
    print("Active-provider backfill result: SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))

