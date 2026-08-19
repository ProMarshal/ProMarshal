import asyncio
from unittest.mock import AsyncMock, patch

from app.projects.work_item_migration import (
    _migrate_project_control_plane,
    _status_with_progress,
    classify_work_item_type,
    generate_work_item_migration_verification_report,
    run_work_item_migration_job,
    start_work_item_migration,
    replace_legacy_task_terms,
)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, length=None):
        return list(self._rows)


class _FakeProjectsCollection:
    def __init__(self, rows):
        self._rows = rows

    def find(self, *_args, **_kwargs):
        return _FakeCursor(self._rows)


class _FakeDb:
    def __init__(self, project_rows):
        self.projects = _FakeProjectsCollection(project_rows)


def test_classify_work_item_type_maps_known_values() -> None:
    assert classify_work_item_type("Epic") == "portfolio"
    assert classify_work_item_type("bug") == "defect"
    assert classify_work_item_type("Sub-task") == "subtask"
    assert classify_work_item_type("Story") == "standard"


def test_classify_work_item_type_defaults_to_custom() -> None:
    assert classify_work_item_type("") == "custom"
    assert classify_work_item_type("custom innovation type") == "custom"


def test_replace_legacy_task_terms_rewrites_nested_values() -> None:
    payload = {
        "capabilities": [
            "task_create",
            "task_update_status",
            "task_add_comment",
            "unchanged_value",
        ],
        "tool_names": {
            "create": "jira_create_task",
            "assign": "jira_assign_task",
            "search": "jira_search_tasks",
            "analyze": "jira_analyze_tasks",
            "details": "jira_get_task_details",
        },
        "nested": [{"value": "task_assign"}],
    }

    transformed = replace_legacy_task_terms(payload)

    assert transformed["capabilities"] == [
        "workitem_create",
        "workitem_update_status",
        "workitem_add_comment",
        "unchanged_value",
    ]
    assert transformed["tool_names"] == {
        "create": "jira_create_work_item",
        "assign": "jira_assign_work_item",
        "search": "jira_search_work_items",
        "analyze": "jira_analyze_work_items",
        "details": "jira_get_work_item_details",
    }
    assert transformed["nested"] == [{"value": "workitem_assign"}]


def test_status_with_progress_populates_contract_aliases() -> None:
    payload = {
        "state": "running",
        "checkpoint": {"phase": "project_migration", "project_id": "proj-1", "project_index": 3},
        "counts": {
            "projects_total": 10,
            "projects_completed": 3,
            "brain_docs_scanned": 100,
            "brain_docs_migrated": 40,
            "records_failed": 2,
        },
    }

    result = _status_with_progress(payload)

    assert result["status"] == "running"
    assert result["current_checkpoint"]["phase"] == "project_migration"
    assert result["project_count_total"] == 10
    assert result["project_count_done"] == 3
    assert result["records_total"] == 100
    assert result["records_migrated"] == 40
    assert result["records_failed"] == 2


def test_migrate_project_control_plane_dry_run_returns_true_when_legacy_terms_exist() -> None:
    project_doc = {
        "_id": "proj-1",
        "project_id": "proj-1",
        "capabilities": ["task_create", "task_assign"],
    }

    changed = asyncio.run(
        _migrate_project_control_plane(project_doc=project_doc, dry_run=True)
    )

    assert changed is True


def test_migrate_project_control_plane_dry_run_is_idempotent_when_already_migrated() -> None:
    project_doc = {
        "_id": "proj-1",
        "project_id": "proj-1",
        "capabilities": ["workitem_create", "workitem_assign"],
        "work_item_migration": {
            "migration_id": "canonical_work_item_cutover_v1",
            "version": "v1",
            "status": "migrated",
            "updated_at": "2026-03-30T00:00:00",
        },
    }

    changed = asyncio.run(
        _migrate_project_control_plane(project_doc=project_doc, dry_run=True)
    )

    assert changed is False


def test_start_work_item_migration_rejects_when_already_running() -> None:
    existing_status = {"state": "running", "status": "running", "checkpoint": {"phase": "project_migration"}}
    with patch("app.projects.work_item_migration._read_status_doc", new=AsyncMock(return_value=existing_status)):
        result = asyncio.run(start_work_item_migration(trigger="manual", dry_run=True, project_limit=None))

    assert result["accepted"] is False
    assert result["reason"] == "already_running"
    assert result["status"] == existing_status


def test_start_work_item_migration_fails_when_lock_unavailable() -> None:
    with (
        patch("app.projects.work_item_migration._read_status_doc", new=AsyncMock(return_value=None)),
        patch("app.projects.work_item_migration._acquire_lock", return_value=False),
    ):
        result = asyncio.run(start_work_item_migration(trigger="manual", dry_run=True, project_limit=None))

    assert result["accepted"] is False
    assert result["reason"] == "lock_unavailable"


def test_start_work_item_migration_fails_when_enqueue_fails() -> None:
    with (
        patch("app.projects.work_item_migration._read_status_doc", new=AsyncMock(return_value=None)),
        patch("app.projects.work_item_migration._acquire_lock", return_value=True),
        patch("app.projects.work_item_migration._write_status_doc", new=AsyncMock()),
        patch("app.projects.work_item_migration._release_lock") as release_lock,
        patch(
            "app.projects.work_item_migration.dramatiq_enqueue",
            return_value={"accepted": False, "reason": "actor_not_registered"},
        ),
    ):
        result = asyncio.run(start_work_item_migration(trigger="manual", dry_run=True, project_limit=10))

    assert result["accepted"] is False
    assert result["reason"] == "enqueue_failed"
    assert result["status"]["status"] == "failed"
    assert result["status"]["last_error"] == "actor_not_registered"
    release_lock.assert_called_once()


def test_start_work_item_migration_accepts_when_queued() -> None:
    with (
        patch("app.projects.work_item_migration._read_status_doc", new=AsyncMock(return_value=None)),
        patch("app.projects.work_item_migration._acquire_lock", return_value=True),
        patch("app.projects.work_item_migration._write_status_doc", new=AsyncMock()),
        patch(
            "app.projects.work_item_migration.dramatiq_enqueue",
            return_value={"accepted": True, "reason": "queued", "job_id": "job-123"},
        ),
    ):
        result = asyncio.run(start_work_item_migration(trigger="manual", dry_run=False, project_limit=5))

    assert result["accepted"] is True
    assert result["reason"] == "queued"
    assert result["job_id"] == "job-123"
    assert result["status"]["job_id"] == "job-123"
    assert result["status"]["checkpoint"]["phase"] == "queued"


def test_generate_verification_report_returns_error_when_db_unavailable() -> None:
    with patch("app.projects.work_item_migration.get_database", return_value=None):
        report = asyncio.run(
            generate_work_item_migration_verification_report(sample_limit=10, persist=False)
        )

    assert report["error"] == "database_unavailable"
    assert report["migration_id"] == "canonical_work_item_cutover_v1"


def test_generate_verification_report_empty_projects_passes_checks() -> None:
    with (
        patch("app.projects.work_item_migration.get_database", return_value=_FakeDb(project_rows=[])),
        patch(
            "app.projects.work_item_migration.get_work_item_migration_status",
            new=AsyncMock(return_value={"status": "completed", "checkpoint": {"phase": "finalize"}}),
        ),
        patch("app.projects.work_item_migration._migrate_neo4j_task_entity_types", new=AsyncMock(return_value=0)),
    ):
        report = asyncio.run(
            generate_work_item_migration_verification_report(sample_limit=10, persist=False)
        )

    assert report["migration_status"]["status"] == "completed"
    assert report["totals"]["projects_total"] == 0
    assert report["totals"]["neo4j_task_nodes_remaining"] == 0
    assert all(bool(check.get("passed")) for check in report["checks"])


def test_generate_verification_report_flags_neo4j_remaining_nodes() -> None:
    with (
        patch("app.projects.work_item_migration.get_database", return_value=_FakeDb(project_rows=[])),
        patch(
            "app.projects.work_item_migration.get_work_item_migration_status",
            new=AsyncMock(return_value={"status": "completed", "checkpoint": {"phase": "finalize"}}),
        ),
        patch("app.projects.work_item_migration._migrate_neo4j_task_entity_types", new=AsyncMock(return_value=5)),
    ):
        report = asyncio.run(
            generate_work_item_migration_verification_report(sample_limit=10, persist=False)
        )

    neo4j_check = next(
        check for check in report["checks"]
        if str(check.get("name")) == "neo4j_task_nodes_remaining"
    )
    assert neo4j_check["passed"] is False
    assert neo4j_check["value"] == 5


def test_generate_verification_report_flags_neo4j_unavailable() -> None:
    with (
        patch("app.projects.work_item_migration.get_database", return_value=_FakeDb(project_rows=[])),
        patch(
            "app.projects.work_item_migration.get_work_item_migration_status",
            new=AsyncMock(return_value={"status": "completed", "checkpoint": {"phase": "finalize"}}),
        ),
        patch("app.projects.work_item_migration._migrate_neo4j_task_entity_types", new=AsyncMock(return_value=-1)),
    ):
        report = asyncio.run(
            generate_work_item_migration_verification_report(sample_limit=10, persist=False)
        )

    neo4j_check = next(
        check for check in report["checks"]
        if str(check.get("name")) == "neo4j_task_nodes_remaining"
    )
    assert neo4j_check["passed"] is False
    assert neo4j_check["value"] == -1


def test_run_work_item_migration_job_fails_when_database_unavailable() -> None:
    with (
        patch("app.projects.work_item_migration.get_database", return_value=None),
        patch("app.projects.work_item_migration._write_status_doc", new=AsyncMock()),
        patch("app.projects.work_item_migration._append_migration_audit", new=AsyncMock()),
        patch("app.projects.work_item_migration._release_lock") as release_lock,
    ):
        result = asyncio.run(
            run_work_item_migration_job(trigger="manual", dry_run=True, project_limit=None)
        )

    assert result["status"] == "failed"
    assert result["last_error"] == "database_unavailable"
    assert result["checkpoint"]["phase"] == "failed"
    release_lock.assert_called_once()


def test_run_work_item_migration_job_completes_for_empty_project_scope() -> None:
    status_writes = []

    async def _capture_status(payload):
        status_writes.append(dict(payload))
        return payload

    with (
        patch("app.projects.work_item_migration.get_database", return_value=_FakeDb(project_rows=[])),
        patch("app.projects.work_item_migration._write_status_doc", new=AsyncMock(side_effect=_capture_status)),
        patch("app.projects.work_item_migration._append_migration_audit", new=AsyncMock()),
        patch("app.projects.work_item_migration._migrate_neo4j_task_entity_types", new=AsyncMock(return_value=0)),
        patch("app.projects.work_item_migration._release_lock") as release_lock,
    ):
        result = asyncio.run(
            run_work_item_migration_job(trigger="manual", dry_run=True, project_limit=None)
        )

    phases = [str((row.get("checkpoint") or {}).get("phase") or "") for row in status_writes]
    assert "preflight" in phases
    assert "neo4j_migration" in phases
    assert "finalize" in phases
    assert result["status"] == "completed"
    assert result["counts"]["projects_total"] == 0
    assert result["counts"]["projects_completed"] == 0
    assert result["counts"]["records_failed"] == 0
    release_lock.assert_called_once()


def test_run_work_item_migration_job_marks_failed_when_project_migration_errors() -> None:
    fake_db = _FakeDb(project_rows=[{"_id": "p1", "project_id": "proj-1"}])

    class _FakeBrainCollection:
        async def count_documents(self, *_args, **_kwargs):
            return 0

    with (
        patch("app.projects.work_item_migration.get_database", return_value=fake_db),
        patch("app.projects.work_item_migration.get_brain_collection", return_value=_FakeBrainCollection()),
        patch(
            "app.projects.work_item_migration._migrate_project_brain_collection",
            new=AsyncMock(side_effect=RuntimeError("project_migration_failed")),
        ),
        patch("app.projects.work_item_migration._write_status_doc", new=AsyncMock()),
        patch("app.projects.work_item_migration._append_migration_audit", new=AsyncMock()),
        patch("app.projects.work_item_migration._migrate_neo4j_task_entity_types", new=AsyncMock(return_value=0)),
        patch("app.projects.work_item_migration._release_lock") as release_lock,
    ):
        result = asyncio.run(
            run_work_item_migration_job(trigger="manual", dry_run=True, project_limit=None)
        )

    assert result["status"] == "failed"
    assert result["counts"]["projects_total"] == 1
    assert result["counts"]["projects_failed"] == 1
    assert result["counts"]["projects_completed"] == 0
    assert result["counts"]["records_failed"] >= 1
    assert "project:proj-1:project_migration_failed" in str(result.get("last_error") or "")
    release_lock.assert_called_once()


def test_run_work_item_migration_job_marks_failed_when_neo4j_unavailable() -> None:
    with (
        patch("app.projects.work_item_migration.get_database", return_value=_FakeDb(project_rows=[])),
        patch("app.projects.work_item_migration._write_status_doc", new=AsyncMock()),
        patch("app.projects.work_item_migration._append_migration_audit", new=AsyncMock()),
        patch("app.projects.work_item_migration._migrate_neo4j_task_entity_types", new=AsyncMock(return_value=-1)),
        patch("app.projects.work_item_migration._release_lock") as release_lock,
    ):
        result = asyncio.run(
            run_work_item_migration_job(trigger="manual", dry_run=True, project_limit=None)
        )

    assert result["status"] == "failed"
    assert result["counts"]["records_failed"] >= 1
    assert result["last_error"] == "neo4j_unavailable"
    release_lock.assert_called_once()
