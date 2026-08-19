"""Work-item migration service for canonical entity cutover."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from app.core.database import get_brain_collection, get_database
from app.domain.work_items.types import classify_work_item_type as classify_canonical_work_item_type
from app.core.queue_backends.dramatiq_backend import enqueue as dramatiq_enqueue
from app.core.queue_runtime import QUEUE_WORKITEM_EVENTS
from app.core.redis_queue import get_redis_connection

_MIGRATION_ID = "canonical_work_item_cutover_v1"
_STATUS_COLLECTION = "work_item_migrations"
_AUDIT_COLLECTION = "work_item_migration_audit"
_STATUS_KEY = f"work_item_migration_status:v1:{_MIGRATION_ID}"
_LOCK_KEY = f"work_item_migration_lock:v1:{_MIGRATION_ID}"
_STATUS_TTL_SECONDS = 60 * 60 * 24 * 7
_LOCK_TTL_SECONDS = 60 * 60 * 2

_LEGACY_TO_CANONICAL_TERM_MAP = {
    "task_create": "workitem_create",
    "task_update_status": "workitem_update_status",
    "task_add_comment": "workitem_add_comment",
    "task_assign": "workitem_assign",
    "task_update_description": "workitem_update_description",
    "task_search": "workitem_search",
    "task_analyze": "workitem_analyze",
    "jira_create_task": "jira_create_work_item",
    "jira_assign_task": "jira_assign_work_item",
    "jira_search_tasks": "jira_search_work_items",
    "jira_analyze_tasks": "jira_analyze_work_items",
    "jira_get_task_details": "jira_get_work_item_details",
}


def _utcnow() -> datetime:
    return datetime.utcnow()


def _iso_now() -> str:
    return _utcnow().isoformat()


def classify_work_item_type(provider_type: Any) -> str:
    # Compatibility wrapper retained for existing imports/tests.
    return classify_canonical_work_item_type(provider_type)


def replace_legacy_task_terms(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: replace_legacy_task_terms(v) for k, v in value.items()}
    if isinstance(value, list):
        return [replace_legacy_task_terms(item) for item in value]
    if isinstance(value, str):
        normalized = value.strip()
        return _LEGACY_TO_CANONICAL_TERM_MAP.get(normalized, value)
    return value


def _status_with_progress(payload: Dict[str, Any]) -> Dict[str, Any]:
    doc = dict(payload or {})
    counts = dict(doc.get("counts") or {})
    checkpoint = dict(doc.get("checkpoint") or {})
    status_value = str(doc.get("status") or doc.get("state") or "pending").strip().lower() or "pending"
    doc["status"] = status_value
    doc["state"] = status_value
    doc["current_checkpoint"] = checkpoint
    doc["project_count_total"] = int(counts.get("projects_total") or 0)
    doc["project_count_done"] = int(counts.get("projects_completed") or 0)
    doc["records_total"] = int(counts.get("brain_docs_scanned") or 0)
    doc["records_migrated"] = int(counts.get("brain_docs_migrated") or 0)
    doc["records_failed"] = int(counts.get("records_failed") or 0)
    return doc


async def _read_status_doc() -> Optional[Dict[str, Any]]:
    db = get_database()
    if db is None:
        return None
    row = await db[_STATUS_COLLECTION].find_one({"_id": _MIGRATION_ID})
    return row if isinstance(row, dict) else None


async def _write_status_doc(payload: Dict[str, Any]) -> Dict[str, Any]:
    db = get_database()
    if db is None:
        return payload
    next_payload = _status_with_progress(payload)
    next_payload["_id"] = _MIGRATION_ID
    next_payload["migration_id"] = _MIGRATION_ID
    next_payload["updated_at"] = _iso_now()
    await db[_STATUS_COLLECTION].update_one(
        {"_id": _MIGRATION_ID},
        {"$set": next_payload},
        upsert=True,
    )
    redis_conn = get_redis_connection()
    if redis_conn is not None:
        try:
            import json

            redis_conn.setex(_STATUS_KEY, _STATUS_TTL_SECONDS, json.dumps(next_payload, default=str))
        except Exception:
            pass
    return next_payload


def _acquire_lock() -> bool:
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return True
    try:
        return bool(redis_conn.set(_LOCK_KEY, "1", nx=True, ex=_LOCK_TTL_SECONDS))
    except Exception:
        return False


def _release_lock() -> None:
    redis_conn = get_redis_connection()
    if redis_conn is None:
        return
    try:
        redis_conn.delete(_LOCK_KEY)
    except Exception:
        pass


def _status_template(*, trigger: str, dry_run: bool, project_limit: Optional[int]) -> Dict[str, Any]:
    return {
        "state": "pending",
        "status": "pending",
        "trigger": str(trigger or "manual").strip() or "manual",
        "dry_run": bool(dry_run),
        "project_limit": int(project_limit) if project_limit else None,
        "checkpoint": {
            "phase": "queued",
            "project_id": None,
            "project_index": 0,
        },
        "counts": {
            "projects_total": 0,
            "projects_completed": 0,
            "projects_failed": 0,
            "brain_docs_scanned": 0,
            "brain_docs_migrated": 0,
            "provider_type_backfilled": 0,
            "work_item_type_backfilled": 0,
            "project_metadata_docs_upserted": 0,
            "projects_control_plane_updated": 0,
            "action_item_refs_scanned": 0,
            "action_item_refs_repaired": 0,
            "neo4j_nodes_migrated": 0,
            "records_failed": 0,
        },
        "baseline_counts": {
            "projects_total": 0,
            "task_records_total": 0,
        },
        "last_error": None,
        "started_at": None,
        "completed_at": None,
    }


async def start_work_item_migration(
    *,
    trigger: str = "manual",
    dry_run: bool = False,
    project_limit: Optional[int] = None,
) -> Dict[str, Any]:
    existing = await _read_status_doc()
    if isinstance(existing, dict) and str(existing.get("state") or "").strip().lower() in {"pending", "running"}:
        return {
            "accepted": False,
            "reason": "already_running",
            "status": existing,
        }

    if not _acquire_lock():
        return {
            "accepted": False,
            "reason": "lock_unavailable",
            "status": existing,
        }

    status_payload = _status_template(trigger=trigger, dry_run=dry_run, project_limit=project_limit)
    await _write_status_doc(status_payload)
    enqueue_result = dramatiq_enqueue(
        queue_name=QUEUE_WORKITEM_EVENTS,
        actor_name="process_work_item_migration_job",
        kwargs={
            "trigger": str(trigger or "manual"),
            "dry_run": bool(dry_run),
            "project_limit": int(project_limit) if project_limit else None,
        },
    )
    if not bool(enqueue_result.get("accepted")):
        failed = dict(status_payload)
        failed["state"] = "failed"
        failed["last_error"] = str(enqueue_result.get("reason") or "enqueue_failed")
        failed["checkpoint"] = {
            "phase": "enqueue_failed",
            "project_id": None,
            "project_index": 0,
        }
        await _write_status_doc(failed)
        _release_lock()
        return {
            "accepted": False,
            "reason": "enqueue_failed",
            "status": failed,
        }

    pending = dict(status_payload)
    pending["job_id"] = str(enqueue_result.get("job_id") or "")
    pending["checkpoint"] = {
        "phase": "queued",
        "project_id": None,
        "project_index": 0,
    }
    await _write_status_doc(pending)
    return {
        "accepted": True,
        "reason": "queued",
        "job_id": pending["job_id"],
        "status": pending,
    }


async def get_work_item_migration_status() -> Dict[str, Any]:
    status_doc = await _read_status_doc()
    if isinstance(status_doc, dict):
        return _status_with_progress(status_doc)
    return _status_with_progress(_status_template(trigger="none", dry_run=False, project_limit=None))


async def generate_work_item_migration_verification_report(
    *,
    sample_limit: int = 25,
    persist: bool = True,
) -> Dict[str, Any]:
    db = get_database()
    if db is None:
        return {
            "generated_at": _iso_now(),
            "migration_id": _MIGRATION_ID,
            "error": "database_unavailable",
        }

    normalized_sample_limit = max(1, min(int(sample_limit or 25), 200))
    migration_status = await get_work_item_migration_status()
    projects = await db.projects.find({}, {"project_id": 1, "work_item_migration": 1}).to_list(length=None)

    totals = {
        "projects_total": 0,
        "projects_with_control_plane_marker": 0,
        "projects_with_metadata_docs": 0,
        "task_records_remaining": 0,
        "work_item_records_total": 0,
        "work_items_with_provider_type": 0,
        "work_items_with_work_item_type": 0,
        "action_item_refs_task_remaining": 0,
        "action_item_refs_work_item_total": 0,
        "action_item_refs_unresolved": 0,
        "neo4j_task_nodes_remaining": 0,
    }
    unresolved_samples: list[dict[str, str]] = []

    for project_doc in projects:
        custom_project_id = str((project_doc or {}).get("project_id") or "").strip()
        if not custom_project_id:
            continue
        totals["projects_total"] += 1

        migration_marker = (project_doc or {}).get("work_item_migration")
        if isinstance(migration_marker, dict) and str(migration_marker.get("migration_id") or "").strip() == _MIGRATION_ID:
            totals["projects_with_control_plane_marker"] += 1

        collection = get_brain_collection(custom_project_id)

        metadata_doc_count = int(
            await collection.count_documents(
                {
                    "entity_type": {"$in": ["work_item_config", "work_item_structure_profile", "work_item_migration_plan"]},
                }
            )
        )
        if metadata_doc_count >= 3:
            totals["projects_with_metadata_docs"] += 1

        totals["task_records_remaining"] += int(await collection.count_documents({"entity_type": "task"}))
        totals["work_item_records_total"] += int(await collection.count_documents({"entity_type": "work_item"}))
        totals["work_items_with_provider_type"] += int(
            await collection.count_documents(
                {"entity_type": "work_item", "provider_type": {"$exists": True, "$ne": ""}}
            )
        )
        totals["work_items_with_work_item_type"] += int(
            await collection.count_documents(
                {"entity_type": "work_item", "work_item_type": {"$exists": True, "$ne": ""}}
            )
        )
        totals["action_item_refs_task_remaining"] += int(
            await collection.count_documents({"entity_type": "action_item", "related_type": "task"})
        )

        work_item_refs = collection.find(
            {"entity_type": "action_item", "related_type": "work_item", "related_id": {"$exists": True, "$ne": ""}},
            {"related_id": 1, "display_key": 1, "action_item_id": 1},
        )
        unresolved_in_project = 0
        async for ref_doc in work_item_refs:
            totals["action_item_refs_work_item_total"] += 1
            related_id = str(ref_doc.get("related_id") or "").strip()
            linked_work_item = await collection.find_one(
                {
                    "entity_type": "work_item",
                    "$or": [
                        {"external_id": related_id},
                        {"task_key": related_id},
                        {"key": related_id},
                    ],
                },
                {"_id": 1},
            )
            if not isinstance(linked_work_item, dict):
                unresolved_in_project += 1
                if len(unresolved_samples) < normalized_sample_limit:
                    unresolved_samples.append(
                        {
                            "project_id": custom_project_id,
                            "action_item_id": str(ref_doc.get("action_item_id") or "").strip(),
                            "display_key": str(ref_doc.get("display_key") or "").strip(),
                            "related_id": related_id,
                        }
                    )
        totals["action_item_refs_unresolved"] += unresolved_in_project

    try:
        totals["neo4j_task_nodes_remaining"] = int(await _migrate_neo4j_task_entity_types(dry_run=True))
    except Exception:
        totals["neo4j_task_nodes_remaining"] = 0

    checks = [
        {
            "name": "no_task_records_remaining",
            "passed": totals["task_records_remaining"] == 0,
            "value": totals["task_records_remaining"],
        },
        {
            "name": "no_legacy_action_item_task_refs",
            "passed": totals["action_item_refs_task_remaining"] == 0,
            "value": totals["action_item_refs_task_remaining"],
        },
        {
            "name": "no_unresolved_work_item_refs",
            "passed": totals["action_item_refs_unresolved"] == 0,
            "value": totals["action_item_refs_unresolved"],
        },
        {
            "name": "control_plane_marker_coverage",
            "passed": totals["projects_with_control_plane_marker"] == totals["projects_total"],
            "value": {
                "projects_with_control_plane_marker": totals["projects_with_control_plane_marker"],
                "projects_total": totals["projects_total"],
            },
        },
        {
            "name": "project_metadata_docs_coverage",
            "passed": totals["projects_with_metadata_docs"] == totals["projects_total"],
            "value": {
                "projects_with_metadata_docs": totals["projects_with_metadata_docs"],
                "projects_total": totals["projects_total"],
            },
        },
        {
            "name": "neo4j_task_nodes_remaining",
            "passed": totals["neo4j_task_nodes_remaining"] == 0,
            "value": totals["neo4j_task_nodes_remaining"],
        },
    ]

    report = {
        "generated_at": _iso_now(),
        "migration_id": _MIGRATION_ID,
        "sample_limit": normalized_sample_limit,
        "migration_status": {
            "status": str(migration_status.get("status") or migration_status.get("state") or "pending"),
            "last_error": migration_status.get("last_error"),
            "current_checkpoint": migration_status.get("current_checkpoint") or migration_status.get("checkpoint"),
        },
        "totals": totals,
        "checks": checks,
        "samples": {
            "unresolved_action_item_refs": unresolved_samples,
        },
    }

    if persist:
        status_doc = await _read_status_doc()
        if isinstance(status_doc, dict):
            status_doc["verification_report"] = report
            status_doc["verification_updated_at"] = _iso_now()
            await _write_status_doc(status_doc)
    return report


async def _append_migration_audit(
    *,
    level: str,
    phase: str,
    custom_project_id: Optional[str],
    details: Dict[str, Any],
) -> None:
    db = get_database()
    if db is None:
        return
    try:
        await db[_AUDIT_COLLECTION].insert_one(
            {
                "migration_id": _MIGRATION_ID,
                "level": str(level or "info").strip().lower() or "info",
                "phase": str(phase or "unknown").strip().lower() or "unknown",
                "project_id": str(custom_project_id or "").strip() or None,
                "details": details or {},
                "created_at": _utcnow(),
            }
        )
    except Exception:
        return


async def _upsert_project_migration_metadata_docs(
    *,
    collection: Any,
    custom_project_id: str,
    dry_run: bool,
) -> int:
    now = _utcnow()
    docs = [
        (
            "work_item_config",
            "work-item-config:v1",
            {
                "migration_id": _MIGRATION_ID,
                "canonical_entity_type": "work_item",
                "updated_at": now,
            },
        ),
        (
            "work_item_structure_profile",
            "work-item-structure-profile:v1",
            {
                "migration_id": _MIGRATION_ID,
                "structure_mode": "auto",
                "detected_structure": "mixed",
                "detection_confidence": "low",
                "updated_at": now,
            },
        ),
        (
            "work_item_migration_plan",
            "work-item-migration-plan:v1",
            {
                "migration_id": _MIGRATION_ID,
                "status": "pending" if dry_run else "running",
                "updated_at": now,
            },
        ),
    ]
    changed = 0
    for entity_type, external_id, payload in docs:
        if dry_run:
            changed += 1
            continue
        result = await collection.update_one(
            {"entity_type": entity_type, "external_id": external_id},
            {
                "$set": {
                    "project_id": custom_project_id,
                    "entity_type": entity_type,
                    "external_id": external_id,
                    **payload,
                },
                "$setOnInsert": {
                    "created_at": now,
                    "source": "work_item_migration",
                },
            },
            upsert=True,
        )
        if int(result.modified_count or 0) > 0 or result.upserted_id is not None:
            changed += 1
    return changed


async def _migrate_project_brain_collection(
    *,
    custom_project_id: str,
    dry_run: bool,
) -> Dict[str, int]:
    collection = get_brain_collection(custom_project_id)
    migrated = {
        "project_metadata_docs_upserted": 0,
        "brain_docs_scanned": 0,
        "brain_docs_migrated": 0,
        "provider_type_backfilled": 0,
        "work_item_type_backfilled": 0,
        "action_item_refs_scanned": 0,
        "action_item_refs_repaired": 0,
        "records_failed": 0,
    }

    migrated["project_metadata_docs_upserted"] = await _upsert_project_migration_metadata_docs(
        collection=collection,
        custom_project_id=custom_project_id,
        dry_run=dry_run,
    )

    task_filter = {"entity_type": "task"}
    task_count = int(await collection.count_documents(task_filter))
    migrated["brain_docs_scanned"] += task_count
    if task_count > 0 and not dry_run:
        result = await collection.update_many(
            task_filter,
            {"$set": {"entity_type": "work_item", "updated_at": _utcnow()}},
        )
        migrated["brain_docs_migrated"] += int(result.modified_count or 0)
    else:
        migrated["brain_docs_migrated"] += task_count

    provider_type_missing_filter = {
        "entity_type": "work_item",
        "provider_type": {"$exists": False},
        "issue_type": {"$exists": True, "$ne": ""},
    }
    provider_missing_count = int(await collection.count_documents(provider_type_missing_filter))
    if provider_missing_count > 0 and not dry_run:
        provider_result = await collection.update_many(
            provider_type_missing_filter,
            [{"$set": {"provider_type": "$issue_type", "updated_at": _utcnow()}}],
        )
        migrated["provider_type_backfilled"] += int(provider_result.modified_count or 0)
    else:
        migrated["provider_type_backfilled"] += provider_missing_count

    work_item_type_missing_filter = {
        "entity_type": "work_item",
        "work_item_type": {"$exists": False},
    }
    missing_work_item_type = int(await collection.count_documents(work_item_type_missing_filter))
    if missing_work_item_type > 0:
        if not dry_run:
            type_cursor = collection.find(
                {
                    "entity_type": "work_item",
                    "work_item_type": {"$exists": False},
                },
                {"_id": 1, "provider_type": 1, "issue_type": 1},
            )
            async for row in type_cursor:
                provider_type = row.get("provider_type") or row.get("issue_type")
                next_type = classify_work_item_type(provider_type)
                await collection.update_one(
                    {"_id": row["_id"]},
                    {"$set": {"work_item_type": next_type, "updated_at": _utcnow()}},
                )
            migrated["work_item_type_backfilled"] += missing_work_item_type
        else:
            migrated["work_item_type_backfilled"] += missing_work_item_type

    action_item_ref_filter = {
        "entity_type": "action_item",
        "related_type": "work_item",
        "related_id": {"$exists": True, "$ne": ""},
    }
    action_ref_count = int(await collection.count_documents(action_item_ref_filter))
    migrated["action_item_refs_scanned"] = action_ref_count

    legacy_task_ref_filter = {
        "entity_type": "action_item",
        "related_type": "task",
    }
    legacy_ref_count = int(await collection.count_documents(legacy_task_ref_filter))
    if legacy_ref_count > 0:
        if not dry_run:
            repair_result = await collection.update_many(
                legacy_task_ref_filter,
                {"$set": {"related_type": "work_item", "updated_at": _utcnow()}},
            )
            migrated["action_item_refs_repaired"] += int(repair_result.modified_count or 0)
        else:
            migrated["action_item_refs_repaired"] += legacy_ref_count
        migrated["action_item_refs_scanned"] += legacy_ref_count

    unresolved = 0
    action_item_cursor = collection.find(
        action_item_ref_filter,
        {"related_id": 1},
    )
    async for action_doc in action_item_cursor:
        related_id = str(action_doc.get("related_id") or "").strip()
        if not related_id:
            continue
        linked_work_item = await collection.find_one(
            {
                "entity_type": "work_item",
                "$or": [
                    {"external_id": related_id},
                    {"task_key": related_id},
                    {"key": related_id},
                ],
            },
            {"_id": 1},
        )
        if not isinstance(linked_work_item, dict):
            unresolved += 1
    migrated["records_failed"] += unresolved
    return migrated


async def _migrate_project_control_plane(
    *,
    project_doc: Dict[str, Any],
    dry_run: bool,
) -> bool:
    if not isinstance(project_doc, dict) or "_id" not in project_doc:
        return False

    transformed = replace_legacy_task_terms(project_doc)
    replacement_changed = transformed != project_doc

    existing_marker = transformed.get("work_item_migration")
    marker_status = "dry_run" if dry_run else "migrated"
    marker_changed = (
        not isinstance(existing_marker, dict)
        or str(existing_marker.get("migration_id") or "").strip() != _MIGRATION_ID
        or str(existing_marker.get("version") or "").strip() != "v1"
        or (not dry_run and str(existing_marker.get("status") or "").strip().lower() != "migrated")
    )
    if not replacement_changed and not marker_changed:
        return False

    now = _utcnow()
    transformed["work_item_migration"] = {
        "migration_id": _MIGRATION_ID,
        "version": "v1",
        "status": marker_status,
        "updated_at": now,
    }
    transformed["updated_at"] = now
    if dry_run:
        return True

    db = get_database()
    if db is None:
        return False
    await db.projects.replace_one({"_id": project_doc["_id"]}, transformed)
    return True


async def _migrate_neo4j_task_entity_types(*, dry_run: bool) -> int:
    try:
        from app.graph.neo4j_client import neo4j_client
    except Exception:
        return -1

    connected = await neo4j_client.connect()
    if not connected or neo4j_client.driver is None:
        return -1

    migrated = 0
    try:
        async with neo4j_client.driver.session() as session:
            if dry_run:
                result = await session.run(
                    "MATCH (n) WHERE n.entity_type = $legacy RETURN count(n) AS count",
                    legacy="task",
                )
                row = await result.single()
                migrated = int((row or {}).get("count") or 0)
            else:
                result = await session.run(
                    "MATCH (n) WHERE n.entity_type = $legacy "
                    "SET n.entity_type = $canonical "
                    "RETURN count(n) AS count",
                    legacy="task",
                    canonical="work_item",
                )
                row = await result.single()
                migrated = int((row or {}).get("count") or 0)
    except Exception:
        migrated = -1
    finally:
        await neo4j_client.close()
    return migrated


async def run_work_item_migration_job(
    *,
    trigger: str = "manual",
    dry_run: bool = False,
    project_limit: Optional[int] = None,
) -> Dict[str, Any]:
    status_doc = _status_template(trigger=trigger, dry_run=dry_run, project_limit=project_limit)
    status_doc["state"] = "running"
    status_doc["status"] = "running"
    status_doc["started_at"] = _iso_now()
    status_doc["checkpoint"]["phase"] = "preflight"
    await _write_status_doc(status_doc)

    db = get_database()
    if db is None:
        status_doc["state"] = "failed"
        status_doc["last_error"] = "database_unavailable"
        status_doc["completed_at"] = _iso_now()
        status_doc["checkpoint"] = {
            "phase": "failed",
            "project_id": None,
            "project_index": 0,
        }
        await _append_migration_audit(
            level="error",
            phase="preflight",
            custom_project_id=None,
            details={"reason": "database_unavailable"},
        )
        await _write_status_doc(status_doc)
        _release_lock()
        return status_doc

    try:
        projects = await db.projects.find({}).to_list(length=None)
        if project_limit and int(project_limit) > 0:
            projects = projects[: int(project_limit)]

        status_doc["counts"]["projects_total"] = len(projects)
        status_doc["baseline_counts"]["projects_total"] = len(projects)
        status_doc["checkpoint"] = {
            "phase": "preflight",
            "project_id": None,
            "project_index": 0,
        }
        task_total = 0
        for project_doc in projects:
            custom_project_id = str(project_doc.get("project_id") or "").strip()
            if not custom_project_id:
                continue
            collection = get_brain_collection(custom_project_id)
            task_total += int(await collection.count_documents({"entity_type": "task"}))
        status_doc["baseline_counts"]["task_records_total"] = int(task_total)
        await _write_status_doc(status_doc)
        await _append_migration_audit(
            level="info",
            phase="preflight",
            custom_project_id=None,
            details={
                "projects_total": len(projects),
                "task_records_total": int(task_total),
                "dry_run": bool(dry_run),
            },
        )

        for index, project_doc in enumerate(projects, start=1):
            custom_project_id = str(project_doc.get("project_id") or "").strip()
            status_doc["checkpoint"] = {
                "phase": "project_migration",
                "project_id": custom_project_id or None,
                "project_index": index,
            }
            await _write_status_doc(status_doc)

            if not custom_project_id:
                status_doc["counts"]["projects_failed"] += 1
                status_doc["counts"]["records_failed"] += 1
                continue

            try:
                project_counts = await _migrate_project_brain_collection(
                    custom_project_id=custom_project_id,
                    dry_run=dry_run,
                )
                for key, value in project_counts.items():
                    status_doc["counts"][key] = int(status_doc["counts"].get(key, 0) or 0) + int(value or 0)
                control_plane_updated = await _migrate_project_control_plane(
                    project_doc=project_doc,
                    dry_run=dry_run,
                )
                if control_plane_updated:
                    status_doc["counts"]["projects_control_plane_updated"] += 1
                await _append_migration_audit(
                    level="info",
                    phase="project_migration",
                    custom_project_id=custom_project_id,
                    details={
                        "project_index": index,
                        "counts": project_counts,
                        "control_plane_updated": bool(control_plane_updated),
                    },
                )
                status_doc["counts"]["projects_completed"] += 1
                await _write_status_doc(status_doc)
            except Exception as project_exc:
                status_doc["counts"]["projects_failed"] += 1
                status_doc["counts"]["records_failed"] += 1
                status_doc["last_error"] = f"project:{custom_project_id}:{project_exc}"
                await _append_migration_audit(
                    level="error",
                    phase="project_migration",
                    custom_project_id=custom_project_id,
                    details={"project_index": index, "error": str(project_exc)},
                )
                await _write_status_doc(status_doc)

        status_doc["checkpoint"] = {
            "phase": "neo4j_migration",
            "project_id": None,
            "project_index": int(status_doc["counts"].get("projects_total") or 0),
        }
        await _write_status_doc(status_doc)
        try:
            status_doc["counts"]["neo4j_nodes_migrated"] = int(
                await _migrate_neo4j_task_entity_types(dry_run=dry_run)
            )
            if int(status_doc["counts"].get("neo4j_nodes_migrated") or 0) < 0:
                status_doc["counts"]["records_failed"] += 1
                if not status_doc.get("last_error"):
                    status_doc["last_error"] = "neo4j_unavailable"
                await _append_migration_audit(
                    level="error",
                    phase="neo4j_migration",
                    custom_project_id=None,
                    details={"error": "neo4j_unavailable"},
                )
        except Exception as neo4j_exc:
            status_doc["last_error"] = str(neo4j_exc)
            status_doc["counts"]["records_failed"] += 1
            await _append_migration_audit(
                level="error",
                phase="neo4j_migration",
                custom_project_id=None,
                details={"error": str(neo4j_exc)},
            )
        await _write_status_doc(status_doc)

        status_doc["completed_at"] = _iso_now()
        status_doc["checkpoint"] = {
            "phase": "finalize",
            "project_id": None,
            "project_index": int(status_doc["counts"].get("projects_total") or 0),
        }
        if int(status_doc["counts"].get("projects_failed") or 0) > 0 or int(
            status_doc["counts"].get("records_failed") or 0
        ) > 0:
            status_doc["state"] = "failed"
            status_doc["status"] = "failed"
            if not status_doc.get("last_error"):
                status_doc["last_error"] = "one_or_more_projects_failed"
        else:
            status_doc["state"] = "completed"
            status_doc["status"] = "completed"
        await _append_migration_audit(
            level="info",
            phase="finalize",
            custom_project_id=None,
            details={
                "status": status_doc.get("status"),
                "counts": status_doc.get("counts"),
            },
        )
        await _write_status_doc(status_doc)
        return status_doc
    except Exception as exc:
        status_doc["state"] = "failed"
        status_doc["status"] = "failed"
        status_doc["last_error"] = str(exc)
        status_doc["completed_at"] = _iso_now()
        status_doc["checkpoint"] = {
            "phase": "failed",
            "project_id": None,
            "project_index": int(status_doc["counts"].get("projects_completed") or 0),
        }
        await _append_migration_audit(
            level="error",
            phase="failed",
            custom_project_id=None,
            details={"error": str(exc)},
        )
        await _write_status_doc(status_doc)
        return status_doc
    finally:
        _release_lock()
