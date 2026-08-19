"""
One-time migration: action-item display keys -> ACTION-<n>.

Usage:
  python scripts/migrate_action_item_display_keys.py          # dry-run
  python scripts/migrate_action_item_display_keys.py --apply  # apply changes
"""

from __future__ import annotations

import argparse
import asyncio
import re
from typing import Any, Dict

from app.core.database import close_mongo_connection, connect_to_mongo, get_database


LEGACY_KEY_RE = re.compile(r"^(?:ACT|AI)-(\d+)$", re.IGNORECASE)


async def _run(*, apply: bool) -> Dict[str, Any]:
    await connect_to_mongo()
    db = get_database()
    if db is None:
        raise RuntimeError("Database is not initialized")

    updated = 0
    skipped_conflict = 0
    scanned = 0
    touched_collections = 0

    try:
        collections = await db.list_collection_names()
        for name in collections:
            if not str(name).startswith("proj-"):
                continue
            collection = db[name]
            touched_collections += 1
            cursor = collection.find(
                {"entity_type": "action_item", "display_key": {"$type": "string"}},
                {"_id": 1, "display_key": 1},
            )
            async for doc in cursor:
                scanned += 1
                current_key = str(doc.get("display_key") or "").strip()
                match = LEGACY_KEY_RE.match(current_key)
                if not match:
                    continue
                new_key = f"ACTION-{match.group(1)}"
                conflict = await collection.find_one(
                    {
                        "entity_type": "action_item",
                        "display_key": new_key,
                        "_id": {"$ne": doc.get("_id")},
                    },
                    {"_id": 1},
                )
                if conflict is not None:
                    skipped_conflict += 1
                    continue
                if apply:
                    await collection.update_one(
                        {"_id": doc.get("_id")},
                        {"$set": {"display_key": new_key}},
                    )
                updated += 1
    finally:
        await close_mongo_connection()

    return {
        "ok": True,
        "mode": "apply" if apply else "dry_run",
        "collections_scanned": touched_collections,
        "action_items_scanned": scanned,
        "keys_to_update": updated,
        "conflicts_skipped": skipped_conflict,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply updates (default is dry-run)")
    args = parser.parse_args()
    result = asyncio.run(_run(apply=bool(args.apply)))
    print(result)


if __name__ == "__main__":
    main()

