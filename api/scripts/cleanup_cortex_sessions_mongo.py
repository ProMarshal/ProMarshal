"""Remove legacy Cortex session artifacts from MongoDB.

Usage:
  python -m scripts.cleanup_cortex_sessions_mongo --dry-run
  python -m scripts.cleanup_cortex_sessions_mongo --apply
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Dict

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings


async def cleanup(*, apply: bool) -> Dict[str, int]:
    client = AsyncIOMotorClient(settings.mongodb_uri)
    db = client.get_default_database()
    brain_db = client[settings.brain_db_name]
    stats = {
        "projects_scanned": 0,
        "brain_cortex_docs_found": 0,
        "brain_cortex_docs_deleted": 0,
        "session_index_rows_found": 0,
        "session_index_rows_deleted": 0,
    }
    try:
        projects = await db.projects.find({}, {"project_id": 1}).to_list(length=None)
        for project in projects:
            custom_project_id = str(project.get("project_id") or "").strip()
            if not custom_project_id:
                continue
            stats["projects_scanned"] += 1
            collection = brain_db[custom_project_id]
            count = await collection.count_documents({"entity_type": "cortex_session"})
            if count > 0:
                stats["brain_cortex_docs_found"] += int(count)
                if apply:
                    deleted = await collection.delete_many({"entity_type": "cortex_session"})
                    stats["brain_cortex_docs_deleted"] += int(deleted.deleted_count or 0)

        index_count = await db.session_index.count_documents({"entity_type": "cortex_session"})
        stats["session_index_rows_found"] = int(index_count or 0)
        if apply and index_count > 0:
            index_deleted = await db.session_index.delete_many({"entity_type": "cortex_session"})
            stats["session_index_rows_deleted"] = int(index_deleted.deleted_count or 0)
        return stats
    finally:
        client.close()


async def _run(args: argparse.Namespace) -> int:
    result = await cleanup(apply=bool(args.apply))
    mode = "apply" if args.apply else "dry-run"
    print(f"[cleanup_cortex_sessions_mongo] mode={mode} result={result}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup legacy cortex session docs/index rows")
    parser.add_argument("--apply", action="store_true", help="Delete artifacts")
    parser.add_argument("--dry-run", action="store_true", help="Preview only (default)")
    args = parser.parse_args()
    if not args.apply and not args.dry_run:
        args.dry_run = True
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
