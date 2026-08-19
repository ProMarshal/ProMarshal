"""Backfill cadence summary date field to project-local day.

Usage (PowerShell):
  cd api
  .\venv\Scripts\python .\scripts\backfill_cadence_summary_dates.py
  .\venv\Scripts\python .\scripts\backfill_cadence_summary_dates.py --apply
  .\venv\Scripts\python .\scripts\backfill_cadence_summary_dates.py --apply --project-id proj-1234-5678
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.database import close_mongo_connection, connect_to_mongo, get_database
from app.core.timezone import get_zoneinfo_or_utc


ENTITY_TYPE = "cadence_daily_summary"


def _to_json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]
    return value


def _as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _project_local_date(value: datetime, project_timezone: str) -> str:
    tz = get_zoneinfo_or_utc(project_timezone, context="backfill_cadence_summary_dates")
    return _as_utc_datetime(value).astimezone(tz).strftime("%Y-%m-%d")


async def _run(args: argparse.Namespace) -> int:
    await connect_to_mongo(runtime_owner="backfill_cadence_summary_dates")
    db = get_database()
    if db is None:
        print(json.dumps({"ok": False, "error": "database_unavailable"}, indent=2))
        return 2

    project_query: Dict[str, Any] = {}
    if args.project_id:
        project_query["project_id"] = str(args.project_id).strip()

    projects = await db.projects.find(
        project_query,
        {"project_id": 1, "timezone": 1},
    ).to_list(length=100000)

    inspected = 0
    eligible = 0
    updated = 0
    skipped_missing_time = 0
    skipped_already = 0
    per_project: List[Dict[str, Any]] = []

    for project in projects:
        project_id = str(project.get("project_id") or "").strip()
        if not project_id:
            continue

        timezone_name = str(project.get("timezone") or "UTC").strip() or "UTC"
        collection = db[project_id]
        cursor = collection.find(
            {"entity_type": ENTITY_TYPE},
            {"_id": 1, "date": 1, "generated_at": 1, "date_basis": 1, "project_timezone": 1},
        )
        docs = await cursor.to_list(length=100000)
        project_inspected = 0
        project_eligible = 0
        project_updated = 0

        for doc in docs:
            inspected += 1
            project_inspected += 1
            generated_at = doc.get("generated_at")
            if not isinstance(generated_at, datetime):
                skipped_missing_time += 1
                continue

            target_date = _project_local_date(generated_at, timezone_name)
            current_date = str(doc.get("date") or "").strip()
            current_basis = str(doc.get("date_basis") or "").strip().lower()
            current_tz = str(doc.get("project_timezone") or "").strip()

            needs_update = (
                current_date != target_date
                or current_basis != "project_local"
                or current_tz != timezone_name
            )
            if not needs_update:
                skipped_already += 1
                continue

            eligible += 1
            project_eligible += 1
            if args.apply:
                await collection.update_one(
                    {"_id": doc.get("_id")},
                    {
                        "$set": {
                            "date": target_date,
                            "project_timezone": timezone_name,
                            "date_basis": "project_local",
                        }
                    },
                )
                updated += 1
                project_updated += 1

        per_project.append(
            {
                "project_id": project_id,
                "timezone": timezone_name,
                "inspected": project_inspected,
                "eligible": project_eligible,
                "updated": project_updated,
            }
        )

    payload = {
        "ok": True,
        "mode": "apply" if args.apply else "dry_run",
        "project_filter": str(args.project_id or ""),
        "inspected": inspected,
        "eligible": eligible,
        "updated": updated,
        "skipped_missing_time": skipped_missing_time,
        "skipped_already_current": skipped_already,
        "projects": per_project,
    }
    print(json.dumps(_to_json_safe(payload), indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill cadence summary date to project-local day.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply updates. Default mode is dry-run.",
    )
    parser.add_argument(
        "--project-id",
        default="",
        help="Optional project_id filter (custom id, for example proj-1234-5678).",
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

