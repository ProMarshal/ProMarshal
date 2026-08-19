#!/usr/bin/env python
"""
Backfill Jira parent-rule metadata in project-scoped work-item provider docs.

What it does:
- For each Jira-connected project, fetch Jira create metadata for the selected project key.
- Persist both:
  - allowed_create_types
  - type_rules (effective)
  into the existing metadata doc:
    entity_type=work_item_provider_metadata
    provider=jira
    metadata_kind=jira_allowed_create_types

This script is idempotent and safe to run repeatedly.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import close_mongo_connection, connect_to_mongo, get_database  # noqa: E402
from app.tasks.providers.jira_adapter import JiraAdapter  # noqa: E402
from app.integrations.jira.oauth import get_fresh_jira_access_token  # noqa: E402


def _normalize(value: Any) -> str:
    return str(value or "").strip()


def _db_name_from_uri(uri: str) -> str:
    candidate = str(uri or "").rsplit("/", 1)[-1]
    return candidate.split("?", 1)[0] if candidate else "unknown"


async def _target_projects(project_id: str) -> List[Dict[str, Any]]:
    db = get_database()
    query: Dict[str, Any] = {"integrations.jira": {"$exists": True}}
    normalized = _normalize(project_id)
    if normalized:
        query["project_id"] = normalized
    return await db.projects.find(query).to_list(length=500)


async def backfill(*, project_id: str, dry_run: bool) -> Dict[str, int]:
    stats: Dict[str, int] = {
        "projects_scanned": 0,
        "projects_skipped_missing_project_key": 0,
        "projects_backfilled": 0,
        "projects_failed": 0,
        "projects_with_type_rules": 0,
    }

    db = get_database()
    projects = await _target_projects(project_id)
    for project in projects:
        stats["projects_scanned"] += 1
        pid = _normalize(project.get("project_id"))
        project_key = _normalize(
            (((project.get("integrations") or {}).get("jira") or {}).get("default_project_key"))
        ).upper()
        if not pid or not project_key:
            stats["projects_skipped_missing_project_key"] += 1
            print(f"SKIP project_id={pid or '<missing>'} reason=missing_default_project_key")
            continue

        if dry_run:
            print(f"DRY  project_id={pid} project_key={project_key}")
            continue

        try:
            token = await get_fresh_jira_access_token(db=db, project=project)
            adapter = JiraAdapter(project=project, access_token_override=token)
            allowed_types = await adapter._fetch_and_cache_allowed_issue_types(  # noqa: SLF001
                jira_project_key=project_key
            )
            stats["projects_backfilled"] += 1
            print(
                f"OK   project_id={pid} project_key={project_key} "
                f"allowed_create_types={len(allowed_types)}"
            )
        except Exception as exc:
            stats["projects_failed"] += 1
            error_type = type(exc).__name__
            error_text = str(exc).strip() or repr(exc)
            print(
                f"FAIL project_id={pid} project_key={project_key} "
                f"error_type={error_type} error={error_text}"
            )
            tb = traceback.format_exc(limit=4).strip()
            if tb:
                print(f"TRACE project_id={pid} {tb}")

    # Verification snapshot
    from app.core.config import settings  # noqa: E402
    from app.core.database import get_brain_collection  # noqa: E402

    projects_verify = await _target_projects(project_id)
    for project in projects_verify:
        pid = _normalize(project.get("project_id"))
        project_key = _normalize(
            (((project.get("integrations") or {}).get("jira") or {}).get("default_project_key"))
        ).upper()
        if not pid or not project_key:
            continue
        doc = await get_brain_collection(pid).find_one(
            {
                "entity_type": "work_item_provider_metadata",
                "provider": "jira",
                "metadata_kind": "jira_allowed_create_types",
                "project_key": project_key,
            },
            {"type_rules": 1},
        )
        type_rules = (doc or {}).get("type_rules") if isinstance(doc, dict) else None
        if isinstance(type_rules, dict) and type_rules:
            stats["projects_with_type_rules"] += 1

    print(
        "Backfill verification context: "
        f"mongodb={_db_name_from_uri(settings.mongodb_uri)} brain={settings.brain_db_name}"
    )
    return stats


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill Jira type_rules metadata for work-item create capabilities."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform Jira metadata refresh + persistence. Without this, script runs in dry-run mode.",
    )
    parser.add_argument(
        "--project-id",
        default="",
        help="Optional project_id filter. If omitted, all Jira-connected projects are scanned.",
    )
    return parser


async def _run() -> int:
    args = _build_parser().parse_args()
    dry_run = not bool(args.execute)
    print(
        "Jira parent-rule metadata backfill start: "
        f"mode={'dry_run' if dry_run else 'execute'} "
        f"project_id={_normalize(args.project_id) or 'all'}"
    )
    await connect_to_mongo()
    try:
        stats = await backfill(project_id=args.project_id, dry_run=dry_run)
    finally:
        await close_mongo_connection()

    print("Jira parent-rule metadata backfill summary:")
    for key in sorted(stats.keys()):
        print(f"  {key}: {stats[key]}")
    if not dry_run and int(stats.get("projects_failed") or 0) > 0:
        print("Jira parent-rule metadata backfill result: FAILED")
        return 1
    print("Jira parent-rule metadata backfill result: SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
