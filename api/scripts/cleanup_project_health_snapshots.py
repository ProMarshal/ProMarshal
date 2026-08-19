"""Remove legacy project health snapshot documents from Brain collections.

Usage:
  python -m scripts.cleanup_project_health_snapshots --dry-run
  python -m scripts.cleanup_project_health_snapshots --apply
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Dict

from app.core.config import settings
from motor.motor_asyncio import AsyncIOMotorClient


SNAPSHOT_ENTITY_TYPE = "project_health_snapshot"


async def cleanup(*, apply: bool) -> Dict[str, int]:
    client = AsyncIOMotorClient(settings.mongodb_uri)
    db = client.get_default_database()
    brain_db = client[settings.brain_db_name]
    stats = {
        "projects_scanned": 0,
        "collections_with_snapshots": 0,
        "snapshot_docs_found": 0,
        "snapshot_docs_deleted": 0,
    }
    try:
        projects = await db.projects.find({}, {"project_id": 1}).to_list(length=None)
        for project in projects:
            custom_project_id = str(project.get("project_id") or "").strip()
            if not custom_project_id:
                continue
            stats["projects_scanned"] += 1
            collection = brain_db[custom_project_id]
            count = await collection.count_documents({"entity_type": SNAPSHOT_ENTITY_TYPE})
            if count <= 0:
                continue
            stats["collections_with_snapshots"] += 1
            stats["snapshot_docs_found"] += int(count)
            if apply:
                deleted = await collection.delete_many({"entity_type": SNAPSHOT_ENTITY_TYPE})
                stats["snapshot_docs_deleted"] += int(deleted.deleted_count or 0)
        return stats
    finally:
        client.close()


async def _run(args: argparse.Namespace) -> int:
    result = await cleanup(apply=bool(args.apply))
    mode = "apply" if args.apply else "dry-run"
    print(f"[cleanup_project_health_snapshots] mode={mode} result={result}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup legacy project health snapshots")
    parser.add_argument("--apply", action="store_true", help="Delete snapshots")
    parser.add_argument("--dry-run", action="store_true", help="Preview only (default)")
    args = parser.parse_args()
    if not args.apply and not args.dry_run:
        args.dry_run = True
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
