import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.routes.work_item_migration import (
    WorkItemMigrationStartRequest,
    work_item_migration_report,
    work_item_migration_start,
    work_item_migration_status,
)


class WorkItemMigrationRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_route_returns_service_payload(self):
        expected = {"status": "running", "project_count_done": 2}
        with patch(
            "app.routes.work_item_migration.get_work_item_migration_status",
            new=AsyncMock(return_value=expected),
        ) as status_mock:
            result = await work_item_migration_status(_internal_ok=None)

        status_mock.assert_awaited_once()
        self.assertEqual(result, expected)

    async def test_start_route_passes_payload_to_service(self):
        payload = WorkItemMigrationStartRequest(trigger="manual", dry_run=True, project_limit=20)
        expected = {"accepted": True, "reason": "queued"}
        with patch(
            "app.routes.work_item_migration.start_work_item_migration",
            new=AsyncMock(return_value=expected),
        ) as start_mock:
            result = await work_item_migration_start(payload=payload, _internal_ok=None)

        start_mock.assert_awaited_once_with(
            trigger="manual",
            dry_run=True,
            project_limit=20,
        )
        self.assertEqual(result, expected)

    async def test_report_route_passes_query_flags_to_service(self):
        expected = {"generated_at": "2026-03-30T00:00:00", "checks": []}
        with patch(
            "app.routes.work_item_migration.generate_work_item_migration_verification_report",
            new=AsyncMock(return_value=expected),
        ) as report_mock:
            result = await work_item_migration_report(sample_limit=40, persist=False, _internal_ok=None)

        report_mock.assert_awaited_once_with(sample_limit=40, persist=False)
        self.assertEqual(result, expected)

    def test_start_request_rejects_project_limit_below_min(self):
        with self.assertRaises(ValidationError):
            WorkItemMigrationStartRequest(project_limit=0)

    def test_start_request_rejects_project_limit_above_max(self):
        with self.assertRaises(ValidationError):
            WorkItemMigrationStartRequest(project_limit=10001)


if __name__ == "__main__":
    unittest.main()

