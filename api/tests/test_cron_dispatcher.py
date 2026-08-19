import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.routes import cron


class CronDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_endpoint_returns_dispatcher_skip_when_enabled(self):
        with patch.object(cron.settings, "cron_secret", "secret"), patch.object(
            cron.settings, "scheduler_use_dispatcher", True
        ):
            result = await cron.trigger_cadence_reminders(authorization="Bearer secret")

        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "dispatcher_mode")
        self.assertEqual(result.get("endpoint"), "cadence-reminders")

    async def test_dispatcher_tick_runs_fast_and_daily_lanes(self):
        fake_db = object()
        fake_engine = object()
        run_tick = AsyncMock(return_value={"processed": 0, "success": 0, "failed": 0, "skipped": 0})
        with patch.object(cron.settings, "cron_secret", "secret"), patch.object(
            cron.settings, "scheduler_dispatcher_fast_lane_budget", 70
        ), patch.object(
            cron.settings, "scheduler_dispatcher_daily_lane_budget", 30
        ), patch.object(
            cron, "get_database", return_value=fake_db
        ), patch.object(
            cron, "ensure_global_schedules", new=AsyncMock(return_value=1)
        ), patch.object(
            cron, "ensure_cadence_schedules_for_projects", new=AsyncMock(return_value=2)
        ), patch.object(
            cron, "ensure_team_poll_schedules_for_active_projects", new=AsyncMock(return_value=3)
        ), patch.object(
            cron, "ensure_recommendation_schedules_for_projects", new=AsyncMock(return_value=4)
        ), patch.object(
            cron, "ensure_action_item_schedules_for_projects", new=AsyncMock(return_value=5)
        ), patch.object(
            cron, "ensure_project_summary_cleanup_schedules_for_projects", new=AsyncMock(return_value=6)
        ), patch.object(
            cron, "SchedulerEngine", return_value=fake_engine
        ), patch.object(
            cron, "_run_tick", new=run_tick
        ), patch.object(
            cron, "_should_run_daily_lane", return_value=True
        ), patch.object(
            cron, "_should_seed_schedules", return_value=True
        ), patch.object(
            cron, "_try_acquire_dispatcher_tick_lock", return_value=(True, None)
        ), patch.object(
            cron, "_recommendation_full_job_types", return_value=["recommendation_full_0900"]
        ):
            result = await cron.trigger_scheduler_dispatcher_tick(authorization="Bearer secret")

        self.assertEqual(result.get("status"), "success")
        self.assertTrue(result.get("run_daily_lane"))
        self.assertIn("cadence_expiry", result.get("fast_lane_results", {}))
        self.assertIn("cadence_reminder", result.get("daily_lane_results", {}))
        self.assertIn("recommendation_full_0900", result.get("daily_lane_results", {}))
        self.assertIn("sessions_cleanup", result.get("maintenance_lane_results", {}))
        self.assertEqual(
            run_tick.await_count,
            len(cron.PRODUCT_FAST_LANE_JOB_TYPES)
            + len(cron.DAILY_LANE_BASE_JOB_TYPES)
            + 1
            + len(cron.MAINTENANCE_LANE_JOB_TYPES),
        )

    def test_should_run_daily_lane_uses_interval_minutes(self):
        with patch.object(cron.settings, "scheduler_dispatcher_daily_interval_minutes", 10):
            self.assertTrue(cron._should_run_daily_lane(datetime(2026, 3, 16, 12, 30, tzinfo=timezone.utc)))
            self.assertFalse(cron._should_run_daily_lane(datetime(2026, 3, 16, 12, 31, tzinfo=timezone.utc)))

    async def test_dispatcher_tick_skips_when_lock_busy(self):
        with patch.object(cron.settings, "cron_secret", "secret"), patch.object(
            cron, "_try_acquire_dispatcher_tick_lock", return_value=(False, None)
        ):
            result = await cron.trigger_scheduler_dispatcher_tick(authorization="Bearer secret")

        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "tick_lock_active")
        self.assertEqual(result.get("endpoint"), "tick")

    async def test_dispatcher_tick_throttles_seeding_when_not_due(self):
        fake_db = object()
        fake_engine = object()
        ensure_global = AsyncMock(return_value=1)
        ensure_cadence = AsyncMock(return_value=2)
        ensure_team_poll = AsyncMock(return_value=3)
        ensure_reco = AsyncMock(return_value=4)
        ensure_action_items = AsyncMock(return_value=5)
        run_tick = AsyncMock(return_value={"processed": 0, "success": 0, "failed": 0, "skipped": 0})
        with patch.object(cron.settings, "cron_secret", "secret"), patch.object(
            cron, "get_database", return_value=fake_db
        ), patch.object(
            cron, "ensure_global_schedules", new=ensure_global
        ), patch.object(
            cron, "ensure_cadence_schedules_for_projects", new=ensure_cadence
        ), patch.object(
            cron, "ensure_team_poll_schedules_for_active_projects", new=ensure_team_poll
        ), patch.object(
            cron, "ensure_recommendation_schedules_for_projects", new=ensure_reco
        ), patch.object(
            cron, "ensure_action_item_schedules_for_projects", new=ensure_action_items
        ), patch.object(
            cron, "ensure_project_summary_cleanup_schedules_for_projects", new=AsyncMock(return_value=6)
        ), patch.object(
            cron, "SchedulerEngine", return_value=fake_engine
        ), patch.object(
            cron, "_run_tick", new=run_tick
        ), patch.object(
            cron, "_should_run_daily_lane", return_value=False
        ), patch.object(
            cron, "_should_seed_schedules", return_value=False
        ), patch.object(
            cron, "_try_acquire_dispatcher_tick_lock", return_value=(True, None)
        ):
            result = await cron.trigger_scheduler_dispatcher_tick(authorization="Bearer secret")

        self.assertEqual(
            result.get("seeded_schedules"),
            {
                "cadence": 0,
                "team_poll": 0,
                "recommendation": 0,
                "action_items": 0,
                "project_summary_cleanup": 0,
            },
        )
        ensure_global.assert_not_awaited()
        ensure_cadence.assert_not_awaited()
        ensure_team_poll.assert_not_awaited()
        ensure_reco.assert_not_awaited()
        ensure_action_items.assert_not_awaited()

    def test_effective_limit_prefers_job_override(self):
        limits = {"cadence_expiry": 12, "recommendation_full": 5}
        self.assertEqual(
            cron._effective_limit_for_job(job_type="cadence_expiry", lane_default=70, job_limits=limits),
            12,
        )
        self.assertEqual(
            cron._effective_limit_for_job(job_type="recommendation_full_0900", lane_default=30, job_limits=limits),
            5,
        )
        self.assertEqual(
            cron._effective_limit_for_job(job_type="sessions_cleanup", lane_default=70, job_limits=limits),
            70,
        )

    def test_inprocess_lock_fallback_when_redis_unavailable(self):
        token = None
        with patch.object(cron, "get_redis_connection", return_value=None), patch.object(
            cron.settings, "scheduler_dispatcher_tick_lock_ttl_seconds", 55
        ):
            acquired_first, token = cron._try_acquire_dispatcher_tick_lock()
            acquired_second, _ = cron._try_acquire_dispatcher_tick_lock()

        self.assertTrue(acquired_first)
        self.assertFalse(acquired_second)
        self.assertIsNotNone(token)
        self.assertTrue(str(token).startswith("mem:"))

        cron._release_dispatcher_tick_lock(token)

        with patch.object(cron, "get_redis_connection", return_value=None), patch.object(
            cron.settings, "scheduler_dispatcher_tick_lock_ttl_seconds", 55
        ):
            acquired_third, _ = cron._try_acquire_dispatcher_tick_lock()
        self.assertTrue(acquired_third)


if __name__ == "__main__":
    unittest.main()
