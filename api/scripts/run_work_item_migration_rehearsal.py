#!/usr/bin/env python
"""
Run canonical work-item migration rehearsal directly against Mongo.

Defaults to dry-run. Use --execute to apply changes.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import close_mongo_connection, connect_to_mongo  # noqa: E402
from app.projects.work_item_migration import (  # noqa: E402
    generate_work_item_migration_verification_report,
    run_work_item_migration_job,
)

_DRY_RUN_ALLOWED_FAILED_CHECKS = {
    "no_task_records_remaining",
    "no_legacy_action_item_task_refs",
    "control_plane_marker_coverage",
    "project_metadata_docs_coverage",
    "neo4j_task_nodes_remaining",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run canonical work-item migration rehearsal."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform write migration. Default is dry-run mode.",
    )
    parser.add_argument(
        "--project-limit",
        type=int,
        default=None,
        help="Optional project limit for local rehearsal.",
    )
    parser.add_argument(
        "--trigger",
        type=str,
        default="local_rehearsal",
        help="Migration trigger label for status/audit records.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=25,
        help="Verification report unresolved sample limit.",
    )
    return parser


def _print_summary(result: Dict[str, Any]) -> None:
    status = str(result.get("status") or result.get("state") or "unknown")
    counts = dict(result.get("counts") or {})
    print("Work-item migration rehearsal summary:")
    print(f"  status: {status}")
    print(f"  trigger: {result.get('trigger')}")
    print(f"  dry_run: {result.get('dry_run')}")
    print(f"  started_at: {result.get('started_at')}")
    print(f"  completed_at: {result.get('completed_at')}")
    print(f"  checkpoint: {result.get('current_checkpoint') or result.get('checkpoint')}")
    print(f"  last_error: {result.get('last_error')}")
    print("  counts:")
    for key in sorted(counts.keys()):
        print(f"    - {key}: {counts[key]}")


def _print_verification(report: Dict[str, Any]) -> None:
    print("Verification report:")
    print(f"  generated_at: {report.get('generated_at')}")
    migration_status = dict(report.get("migration_status") or {})
    print(f"  migration_status: {migration_status.get('status')}")
    totals = dict(report.get("totals") or {})
    print("  totals:")
    for key in sorted(totals.keys()):
        print(f"    - {key}: {totals[key]}")
    checks = list(report.get("checks") or [])
    print("  checks:")
    for check in checks:
        print(f"    - {check.get('name')}: {'PASS' if check.get('passed') else 'FAIL'}")


def _evaluate_rehearsal_exit_code(*, result: Dict[str, Any], report: Dict[str, Any], dry_run: bool) -> int:
    status = str(result.get("status") or result.get("state") or "").strip().lower()
    if not dry_run and status != "completed":
        return 1
    if dry_run and status not in {"completed", "failed"}:
        return 1

    if str(report.get("error") or "").strip():
        return 1

    if dry_run:
        dry_run_last_error = str(result.get("last_error") or "").strip().lower()
        if dry_run_last_error and dry_run_last_error not in {"one_or_more_projects_failed"}:
            return 1

    failed_checks = [item for item in list(report.get("checks") or []) if not bool(item.get("passed"))]
    if not failed_checks:
        return 0

    if not dry_run:
        return 1

    totals = dict(report.get("totals") or {})
    task_records_remaining = int(totals.get("task_records_remaining") or 0)

    unexpected = [
        item for item in failed_checks
        if (
            str(item.get("name") or "").strip() not in _DRY_RUN_ALLOWED_FAILED_CHECKS
            and not (
                str(item.get("name") or "").strip() == "no_unresolved_work_item_refs"
                and task_records_remaining > 0
            )
        )
    ]
    return 1 if unexpected else 0


async def _run() -> int:
    args = _build_parser().parse_args()
    dry_run = not bool(args.execute)
    print(
        "Work-item migration rehearsal start: "
        f"mode={'dry_run' if dry_run else 'execute'} "
        f"project_limit={args.project_limit if args.project_limit else 'all'} "
        f"trigger={args.trigger}"
    )

    await connect_to_mongo()
    try:
        result = await run_work_item_migration_job(
            trigger=str(args.trigger or "local_rehearsal"),
            dry_run=dry_run,
            project_limit=args.project_limit,
        )
        report = await generate_work_item_migration_verification_report(
            sample_limit=int(args.sample_limit or 25),
            persist=True,
        )
    finally:
        await close_mongo_connection()

    _print_summary(result)
    _print_verification(report)
    return _evaluate_rehearsal_exit_code(result=result, report=report, dry_run=dry_run)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
