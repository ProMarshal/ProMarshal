r"""Audit (and optionally drop) legacy Mongo collections.

Usage (PowerShell):
  cd api
  .\venv\Scripts\python .\scripts\audit_legacy_collections.py
  .\venv\Scripts\python .\scripts\audit_legacy_collections.py --include interaction_ledger
  .\venv\Scripts\python .\scripts\audit_legacy_collections.py --drop --yes
  .\venv\Scripts\python .\scripts\audit_legacy_collections.py --drop --yes --force-nonempty

Safety:
- Default mode is read-only audit.
- Drop mode requires both --drop and --yes.
- Non-empty collections are never dropped unless --force-nonempty is set.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Ensure `app.*` imports work when running this script directly.
API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.database import close_mongo_connection, connect_to_mongo, get_database


DEFAULT_LEGACY_COLLECTIONS = [
    "slack_sessions",
    "cortex_sessions",
]


async def _collection_stats(db: Any, name: str) -> Dict[str, Any]:
    exists = name in (await db.list_collection_names())
    if not exists:
        return {
            "collection": name,
            "exists": False,
            "document_count": 0,
            "oldest_created_at": None,
            "newest_created_at": None,
        }

    collection = db[name]
    count = int(await collection.count_documents({}))
    oldest = await collection.find_one({}, sort=[("created_at", 1)], projection={"created_at": 1})
    newest = await collection.find_one({}, sort=[("created_at", -1)], projection={"created_at": 1})
    return {
        "collection": name,
        "exists": True,
        "document_count": count,
        "oldest_created_at": (oldest or {}).get("created_at"),
        "newest_created_at": (newest or {}).get("created_at"),
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


async def _run(args: argparse.Namespace) -> int:
    await connect_to_mongo(runtime_owner="audit_legacy_collections")
    db = get_database()
    if db is None:
        print(json.dumps({"ok": False, "error": "database_unavailable"}, indent=2))
        return 2

    names: List[str] = []
    for item in DEFAULT_LEGACY_COLLECTIONS + list(args.include or []):
        normalized = str(item or "").strip()
        if not normalized:
            continue
        if normalized not in names:
            names.append(normalized)

    audit_rows: List[Dict[str, Any]] = []
    dropped_rows: List[Dict[str, Any]] = []
    skipped_rows: List[Dict[str, Any]] = []

    for name in names:
        stats = await _collection_stats(db, name)
        audit_rows.append(stats)

        if not args.drop:
            continue

        if not args.yes:
            skipped_rows.append(
                {
                    "collection": name,
                    "reason": "drop_requested_without_yes",
                }
            )
            continue

        if not stats.get("exists"):
            skipped_rows.append(
                {
                    "collection": name,
                    "reason": "not_found",
                }
            )
            continue

        doc_count = int(stats.get("document_count") or 0)
        if doc_count > 0 and not bool(args.force_nonempty):
            skipped_rows.append(
                {
                    "collection": name,
                    "reason": "nonempty_requires_force_nonempty",
                    "document_count": doc_count,
                }
            )
            continue

        await db.drop_collection(name)
        dropped_rows.append(
            {
                "collection": name,
                "dropped": True,
                "document_count_before_drop": doc_count,
            }
        )

    payload = {
        "ok": True,
        "mode": "drop" if args.drop else "audit",
        "database": str(getattr(db, "name", "")),
        "collections_checked": audit_rows,
        "dropped": dropped_rows,
        "skipped": skipped_rows,
    }
    print(json.dumps(_json_safe(payload), indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit (optionally drop) legacy Mongo collections.")
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Additional collection name to include (repeatable).",
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Attempt drop for matched collections (requires --yes).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Explicit confirmation required for --drop.",
    )
    parser.add_argument(
        "--force-nonempty",
        action="store_true",
        help="Allow dropping non-empty collections (high risk).",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    finally:
        try:
            asyncio.run(close_mongo_connection())
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
