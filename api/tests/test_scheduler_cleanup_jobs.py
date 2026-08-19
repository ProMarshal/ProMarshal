import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.core.config import settings as app_settings
from app.scheduler import service as scheduler_service
from app.scheduler import executors as scheduler_executors


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def to_list(self, length=None):
        if length is None:
            return list(self._rows)
        return list(self._rows)[: int(length)]


class _FakeProjectsCollection:
    def __init__(self, rows):
        self._rows = rows

    def find(self, *_args, **_kwargs):
        return _FakeCursor(self._rows)


class _FakeRepo:
    def __init__(self, db):
        self.db = db
        self.upserts = []
        self.existing = {}

    async def ensure_indexes(self):
        return None

    async def get_schedule(self, *, job_type, project_id):
        return self.existing.get((job_type, project_id))

    async def upsert_schedule(self, *, job_type, project_id, schedule_spec, next_run_at, enabled):
        self.upserts.append(
            {
                "job_type": job_type,
                "project_id": project_id,
                "schedule_spec": schedule_spec,
                "next_run_at": next_run_at,
                "enabled": enabled,
            }
        )
        return {"schedule_id": "sch-1", "job_type": job_type}

    async def delete_schedule(self, *, job_type, project_id):
        return 0


class _FakeDB:
    def __init__(self, project_rows=None):
        self.projects = _FakeProjectsCollection(project_rows or [])


class SchedulerCleanupJobsTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_global_schedules_seeds_cleanup_jobs(self):
        fake_db = _FakeDB()
        repo = _FakeRepo(fake_db)
        with patch.object(scheduler_service, "SchedulerRepository", lambda _db: repo):
            await scheduler_service.ensure_global_schedules(fake_db)

        job_types = {row["job_type"] for row in repo.upserts}
        self.assertIn("schedule_runs_cleanup", job_types)
        self.assertIn("channel_index_cleanup", job_types)

    async def test_ensure_project_summary_cleanup_schedules_for_projects(self):
        fake_db = _FakeDB(
            [
                {"project_id": "proj-1111-1111"},
                {"project_id": "proj-2222-2222"},
            ]
        )
        repo = _FakeRepo(fake_db)
        with patch.object(scheduler_service, "SchedulerRepository", lambda _db: repo):
            changed = await scheduler_service.ensure_project_summary_cleanup_schedules_for_projects(fake_db)

        self.assertEqual(changed, 2)
        self.assertEqual(len(repo.upserts), 2)
        self.assertTrue(all(row["job_type"] == "project_summary_cleanup" for row in repo.upserts))
        self.assertTrue(all(row["schedule_spec"].get("minutes") == 10080 for row in repo.upserts))

    async def test_execute_schedule_runs_cleanup_uses_repo_prune(self):
        fake_repo = AsyncMock()
        fake_repo.prune_schedule_runs_before = AsyncMock(
            return_value={"matched": 10, "selected": 5, "deleted": 5}
        )
        with patch("app.scheduler.repository.SchedulerRepository", return_value=fake_repo), patch.object(
            scheduler_executors, "datetime"
        ) as dt_mock:
            dt_mock.utcnow.return_value = datetime(2026, 3, 22, 0, 0, 0)
            result = await scheduler_executors.execute_schedule_runs_cleanup(object(), {})

        self.assertEqual(result.get("deleted"), 5)
        fake_repo.prune_schedule_runs_before.assert_awaited_once()

    async def test_execute_channel_index_cleanup_phase1_null_scope(self):
        fake_repo = AsyncMock()
        fake_repo.prune_channel_index_before = AsyncMock(
            return_value={"matched": 4, "selected": 2, "deleted": 2}
        )
        with patch("app.scheduler.repository.SchedulerRepository", return_value=fake_repo), patch.object(
            scheduler_executors, "datetime"
        ) as dt_mock:
            dt_mock.utcnow.return_value = datetime(2026, 3, 22, 0, 0, 0)
            result = await scheduler_executors.execute_channel_index_cleanup(object(), {})

        self.assertEqual(result.get("deleted"), 2)
        self.assertEqual(result.get("phase"), "phase1_null_active_project_only")
        fake_repo.prune_channel_index_before.assert_awaited_once()

    async def test_execute_project_summary_cleanup_skips_when_disabled(self):
        with patch.object(app_settings, "cleanup_summary_retention_enabled", False):
            result = await scheduler_executors.execute_project_summary_cleanup(object(), {"project_id": "proj-1"})
        self.assertTrue(result.get("skipped"))

    async def test_execute_project_summary_cleanup_deletes_selected_docs(self):
        now = datetime(2026, 3, 22, 0, 0, 0)
        old = now - timedelta(days=200)

        class _Collection:
            def __init__(self):
                self.delete_many = AsyncMock(side_effect=[AsyncMock(deleted_count=1), AsyncMock(deleted_count=1)])

            def find(self, query, projection):
                entity_type = query.get("entity_type")
                if entity_type == "team_poll_result":
                    return _FakeCursor([{"_id": "tp-1", "generated_at": old}])
                if entity_type == "cadence_daily_summary":
                    return _FakeCursor([{"_id": "cd-1", "generated_at": old}])
                return _FakeCursor([])

        collection = _Collection()
        with patch("app.core.database.get_brain_collection", return_value=collection), patch.object(
            app_settings, "cleanup_summary_retention_enabled", True
        ), patch.object(
            app_settings, "cleanup_job_batch_limit", 500
        ), patch.object(
            app_settings, "team_poll_result_retention_days", 30
        ), patch.object(
            app_settings, "cadence_daily_summary_retention_days", 90
        ), patch.object(
            scheduler_executors, "datetime"
        ) as dt_mock:
            dt_mock.utcnow.return_value = now
            result = await scheduler_executors.execute_project_summary_cleanup(
                object(),
                {"project_id": "proj-1234-5678"},
            )

        self.assertEqual(result.get("team_poll_result_selected"), 1)
        self.assertEqual(result.get("cadence_daily_summary_selected"), 1)
        self.assertEqual(collection.delete_many.await_count, 2)


if __name__ == "__main__":
    unittest.main()
