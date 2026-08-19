#!/usr/bin/env python
"""
Migrate shared `cortex_sessions` payloads into project-scoped Brain collections.

This script keeps `session_index` as the global lookup plane and backfills
project-scoped session payload documents (`entity_type` = cadence/cortex session).
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import (  # noqa: E402
    close_mongo_connection,
    connect_to_mongo,
    ensure_project_session_indexes,
    get_database,
    get_project_session_collection,
    get_session_index_collection,
)


CADENCE_ENTITY_TYPE = "cadence_session"
CORTEX_ENTITY_TYPE = "cortex_session"
SESSION_ENTITY_TYPES = {CADENCE_ENTITY_TYPE, CORTEX_ENTITY_TYPE}
LEGACY_COLLECTION = "cortex_sessions"


def _normalize(value: Any) -> str:
    return str(value or "").strip()


def _infer_entity_type(doc: Dict[str, Any]) -> str:
    raw = _normalize(doc.get("entity_type"))
    if raw in SESSION_ENTITY_TYPES:
        return raw
    source = _normalize(doc.get("source")).lower()
    if source == "cadence":
        return CADENCE_ENTITY_TYPE
    return CORTEX_ENTITY_TYPE


def _resolve_session_identifiers(doc: Dict[str, Any]) -> Tuple[str, str, str]:
    source = _normalize(doc.get("source"))
    session_id = _normalize(doc.get("session_id"))
    session_key = _normalize(doc.get("session_key"))
    if not session_id and session_key:
        session_id = session_key
    if not session_key and session_id:
        session_key = session_id
    return source, session_id, session_key


def _project_payload_query(
    *,
    entity_type: str,
    source: str,
    session_id: str,
    session_key: str,
) -> Dict[str, Any]:
    query: Dict[str, Any] = {"entity_type": entity_type, "source": source}
    if session_id:
        query["session_id"] = session_id
    else:
        query["session_key"] = session_key
    return query


def _index_query(*, source: str, session_id: str, session_key: str) -> Dict[str, Any]:
    query: Dict[str, Any] = {"source": source}
    if session_id:
        query["session_id"] = session_id
    else:
        query["session_key"] = session_key
    return query


def _matches_source_scope(doc: Dict[str, Any], source_scope: str) -> bool:
    source = _normalize(doc.get("source")).lower()
    if source_scope == "all":
        return True
    if source_scope == "cadence":
        return source == "cadence"
    if source_scope == "cortex":
        return source != "cadence"
    return True


async def migrate_sessions(
    *,
    dry_run: bool,
    source_scope: str,
    project_id: Optional[str],
    limit: int,
    sample_size: int,
) -> Dict[str, int]:
    db = get_database()
    if db is None:
        raise RuntimeError("Database is not initialized")

    legacy_collection = db[LEGACY_COLLECTION]
    session_index = get_session_index_collection()

    query: Dict[str, Any] = {}
    normalized_project_id = _normalize(project_id)
    if normalized_project_id:
        query["project_id"] = normalized_project_id

    cursor = legacy_collection.find(query).sort([("_id", 1)])
    if limit > 0:
        cursor = cursor.limit(limit)

    stats: Dict[str, int] = {
        "scanned": 0,
        "would_migrate": 0,
        "migrated": 0,
        "skipped_missing_project": 0,
        "skipped_missing_source": 0,
        "skipped_missing_identity": 0,
        "skipped_scope_mismatch": 0,
        "duplicate_source_session_id": 0,
        "duplicate_source_session_key": 0,
        "parity_ok": 0,
        "parity_failed": 0,
    }

    seen_source_session_id: set[Tuple[str, str]] = set()
    seen_source_session_key: set[Tuple[str, str]] = set()
    parity_samples: List[Dict[str, str]] = []
    sample_seen = 0

    async for raw_doc in cursor:
        stats["scanned"] += 1
        doc = dict(raw_doc or {})
        project_value = _normalize(doc.get("project_id"))
        if not project_value:
            stats["skipped_missing_project"] += 1
            continue

        if not _matches_source_scope(doc, source_scope):
            stats["skipped_scope_mismatch"] += 1
            continue

        source, session_id, session_key = _resolve_session_identifiers(doc)
        if not source:
            stats["skipped_missing_source"] += 1
            continue
        if not session_id and not session_key:
            stats["skipped_missing_identity"] += 1
            continue

        if session_id:
            pair = (source, session_id)
            if pair in seen_source_session_id:
                stats["duplicate_source_session_id"] += 1
            else:
                seen_source_session_id.add(pair)
        if session_key:
            pair = (source, session_key)
            if pair in seen_source_session_key:
                stats["duplicate_source_session_key"] += 1
            else:
                seen_source_session_key.add(pair)

        entity_type = _infer_entity_type(doc)
        now = datetime.utcnow()
        payload = dict(doc)
        payload.pop("_id", None)
        payload["entity_type"] = entity_type
        payload["project_id"] = project_value
        if session_id:
            payload["session_id"] = session_id
        if session_key:
            payload["session_key"] = session_key
        payload.setdefault("created_at", now)
        payload["updated_at"] = payload.get("updated_at") or now

        if dry_run:
            stats["would_migrate"] += 1
            continue

        await ensure_project_session_indexes(project_value)
        project_collection = get_project_session_collection(project_value)
        project_query = _project_payload_query(
            entity_type=entity_type,
            source=source,
            session_id=session_id,
            session_key=session_key,
        )
        await project_collection.update_one(
            project_query,
            {"$set": payload, "$setOnInsert": {"migrated_at": now}},
            upsert=True,
        )

        index_query = _index_query(source=source, session_id=session_id, session_key=session_key)
        index_doc = {
            "entity_type": entity_type,
            "source": source,
            "session_id": session_id or None,
            "session_key": session_key or None,
            "project_id": project_value,
            "user_id": _normalize(payload.get("user_id")) or None,
            "status": _normalize(payload.get("status")) or None,
            "expires_at": payload.get("expires_at"),
            "created_at": payload.get("created_at") or now,
            "updated_at": now,
        }
        await session_index.update_one(
            index_query,
            {"$set": index_doc, "$setOnInsert": {"first_seen_at": now}},
            upsert=True,
        )
        stats["migrated"] += 1

        sample_seen += 1
        sample_row = {
            "entity_type": entity_type,
            "project_id": project_value,
            "source": source,
            "session_id": session_id,
            "session_key": session_key,
        }
        if len(parity_samples) < sample_size:
            parity_samples.append(sample_row)
        else:
            idx = random.randint(1, sample_seen)
            if idx <= sample_size:
                parity_samples[idx - 1] = sample_row

    if not dry_run and parity_samples:
        for sample in parity_samples:
            project_collection = get_project_session_collection(sample["project_id"])
            project_query = _project_payload_query(
                entity_type=sample["entity_type"],
                source=sample["source"],
                session_id=sample["session_id"],
                session_key=sample["session_key"],
            )
            project_doc = await project_collection.find_one(
                project_query,
                {
                    "entity_type": 1,
                    "project_id": 1,
                    "source": 1,
                    "session_id": 1,
                    "session_key": 1,
                },
            )
            index_doc = await session_index.find_one(
                _index_query(
                    source=sample["source"],
                    session_id=sample["session_id"],
                    session_key=sample["session_key"],
                ),
                {
                    "entity_type": 1,
                    "project_id": 1,
                    "source": 1,
                    "session_id": 1,
                    "session_key": 1,
                },
            )
            if not project_doc or not index_doc:
                stats["parity_failed"] += 1
                continue

            project_ok = (
                _normalize(project_doc.get("entity_type")) == sample["entity_type"]
                and _normalize(project_doc.get("project_id")) == sample["project_id"]
                and _normalize(project_doc.get("source")) == sample["source"]
                and _normalize(project_doc.get("session_id")) == sample["session_id"]
                and _normalize(project_doc.get("session_key")) == sample["session_key"]
            )
            index_ok = (
                _normalize(index_doc.get("entity_type")) == sample["entity_type"]
                and _normalize(index_doc.get("project_id")) == sample["project_id"]
                and _normalize(index_doc.get("source")) == sample["source"]
                and _normalize(index_doc.get("session_id")) == sample["session_id"]
                and _normalize(index_doc.get("session_key")) == sample["session_key"]
            )

            if project_ok and index_ok:
                stats["parity_ok"] += 1
            else:
                stats["parity_failed"] += 1

    return stats


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate shared `cortex_sessions` docs into project-scoped Brain collections "
            "and backfill `session_index`."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform writes. Without this flag, the script runs in dry-run mode.",
    )
    parser.add_argument(
        "--source-scope",
        choices=["all", "cadence", "cortex"],
        default="all",
        help="Limit migration to cadence or cortex session sources.",
    )
    parser.add_argument(
        "--project-id",
        default="",
        help="Optional project_id filter.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max number of legacy docs to scan. 0 means no limit.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=25,
        help="Parity sample size after migration writes.",
    )
    return parser


async def _run() -> int:
    args = _build_parser().parse_args()
    dry_run = not bool(args.execute)
    sample_size = max(1, int(args.sample_size or 25))
    limit = max(0, int(args.limit or 0))

    print(
        "Session migration start: "
        f"mode={'dry_run' if dry_run else 'execute'} "
        f"source_scope={args.source_scope} "
        f"project_id={_normalize(args.project_id) or 'all'} "
        f"limit={limit or 'none'} "
        f"sample_size={sample_size} "
        f"session_storage_mode={getattr(settings, 'session_storage_mode', 'shared')}"
    )

    await connect_to_mongo()
    try:
        stats = await migrate_sessions(
            dry_run=dry_run,
            source_scope=args.source_scope,
            project_id=args.project_id,
            limit=limit,
            sample_size=sample_size,
        )
    finally:
        await close_mongo_connection()

    print("Session migration summary:")
    for key in sorted(stats.keys()):
        print(f"  {key}: {stats[key]}")

    if stats.get("parity_failed", 0) > 0:
        print("Session migration result: FAILED parity checks")
        return 2
    print("Session migration result: SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
