#!/usr/bin/env python
"""
Backfill `is_closed` for Jira task documents in project Brain collections.

Lifecycle policy depends on `is_closed` for non-closed default reads.
This script is safe to run repeatedly.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import (  # noqa: E402
    close_mongo_connection,
    connect_to_mongo,
    get_brain_collection,
    get_database,
)


def _normalize(value: Any) -> str:
    return str(value or "").strip()


async def _target_project_ids(project_id: str) -> List[str]:
    normalized = _normalize(project_id)
    if normalized:
        return [normalized]

    db = get_database()
    if db is None:
        return []
    rows = await db.projects.find({}, {"project_id": 1}).to_list(length=None)
    project_ids: List[str] = []
    for row in rows:
        candidate = _normalize((row or {}).get("project_id"))
        if candidate:
            project_ids.append(candidate)
    return sorted(set(project_ids))


async def backfill(*, project_id: str, dry_run: bool) -> Dict[str, int]:
    stats: Dict[str, int] = {
        "projects_scanned": 0,
        "docs_missing_flag": 0,
        "docs_marked_closed": 0,
        "docs_marked_non_closed": 0,
    }

    project_ids = await _target_project_ids(project_id)
    for pid in project_ids:
        stats["projects_scanned"] += 1
        collection = get_brain_collection(pid)
        missing_query = {
            "entity_type": "work_item",
            "integration_type": "jira",
            "is_closed": {"$exists": False},
        }
        missing_count = await collection.count_documents(missing_query)
        stats["docs_missing_flag"] += int(missing_count or 0)
        if dry_run or missing_count <= 0:
            continue

        closed_result = await collection.update_many(
            {
                **missing_query,
                "status": "done",
            },
            {"$set": {"is_closed": True}},
        )
        stats["docs_marked_closed"] += int(closed_result.modified_count or 0)

        non_closed_result = await collection.update_many(
            {
                **missing_query,
                "status": {"$ne": "done"},
            },
            {"$set": {"is_closed": False}},
        )
        stats["docs_marked_non_closed"] += int(non_closed_result.modified_count or 0)

    return stats


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill `is_closed` for Jira task docs in Brain collections."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform writes. Without this flag, script runs in dry-run mode.",
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
    print(
        "Task lifecycle backfill start: "
        f"mode={'dry_run' if dry_run else 'execute'} "
        f"project_id={_normalize(args.project_id) or 'all'}"
    )

    await connect_to_mongo()
    try:
        stats = await backfill(project_id=args.project_id, dry_run=dry_run)
    finally:
        await close_mongo_connection()

    print("Task lifecycle backfill summary:")
    for key in sorted(stats.keys()):
        print(f"  {key}: {stats[key]}")
    print("Task lifecycle backfill result: SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
