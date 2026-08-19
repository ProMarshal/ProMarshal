#!/usr/bin/env python
"""
Repair legacy Jira work-item records still stored as entity_type="task".

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
    get_brain_collection,
    get_database,
)
from app.domain.work_items.types import classify_work_item_type  # noqa: E402


def _normalize(value: Any) -> str:
    return str(value or "").strip()


async def _target_project_ids(project_id: str) -> List[str]:
    normalized = _normalize(project_id)
    if normalized:
        return [normalized]

    db = get_database()
    rows = await db.projects.find({}, {"project_id": 1}).to_list(length=None)
    project_ids: List[str] = []
    for row in rows:
        pid = _normalize((row or {}).get("project_id"))
        if pid:
            project_ids.append(pid)
    return sorted(set(project_ids))


async def repair(*, project_id: str, dry_run: bool) -> Dict[str, int]:
    stats: Dict[str, int] = {
        "projects_scanned": 0,
        "legacy_task_docs_found": 0,
        "docs_repaired": 0,
    }
    now = datetime.utcnow()
    project_ids = await _target_project_ids(project_id)
    for pid in project_ids:
        stats["projects_scanned"] += 1
        collection = get_brain_collection(pid)
        query = {
            "integration_type": "jira",
            "entity_type": "task",
        }
        docs = await collection.find(
            query,
            {"_id": 1, "provider_type": 1, "issue_type": 1, "work_item_type": 1},
        ).to_list(length=None)
        stats["legacy_task_docs_found"] += len(docs)
        if dry_run:
            continue
        for doc in docs:
            provider_type = _normalize(doc.get("provider_type") or doc.get("issue_type")) or None
            work_item_type = _normalize(doc.get("work_item_type")).lower() or None
            if not work_item_type:
                work_item_type = classify_work_item_type(provider_type)
            update_fields = {
                "entity_type": "work_item",
                "work_item_type": work_item_type,
                "updated_at": now,
            }
            if provider_type:
                update_fields["provider_type"] = provider_type
            result = await collection.update_one(
                {"_id": doc.get("_id")},
                {"$set": update_fields},
            )
            stats["docs_repaired"] += int(result.modified_count or 0)
    return stats


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repair legacy Jira entity_type='task' records to canonical work_item."
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
        "Work-item entity repair start: "
        f"mode={'dry_run' if dry_run else 'execute'} "
        f"project_id={_normalize(args.project_id) or 'all'}"
    )
    await connect_to_mongo()
    try:
        stats = await repair(project_id=args.project_id, dry_run=dry_run)
    finally:
        await close_mongo_connection()

    print("Work-item entity repair summary:")
    for key in sorted(stats.keys()):
        print(f"  {key}: {stats[key]}")
    print("Work-item entity repair result: SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
