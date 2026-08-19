import unittest
from unittest.mock import AsyncMock, patch

from app.scheduler import executors as scheduler_executors


class _FakeProjects:
    def __init__(self, project_doc):
        self._project_doc = project_doc

    async def find_one(self, *_args, **_kwargs):
        return self._project_doc


class _FakeDB:
    def __init__(self, project_doc=None):
        self.projects = _FakeProjects(project_doc)


class SchedulerProjectNotFoundSkipsTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_recommendation_full_skips_when_project_not_found(self):
        fake_db = _FakeDB(project_doc=None)
        orchestrator_call = AsyncMock(return_value={"ok": True})

        with patch(
            "app.projects.recommendations.recommendation_orchestrator.generate_project_recommendations",
            new=orchestrator_call,
        ):
            result = await scheduler_executors.execute_recommendation_full(
                fake_db,
                {"project_id": "proj-0000-0000"},
            )

        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "project_not_found")
        orchestrator_call.assert_not_awaited()

    async def test_execute_recommendation_incremental_skips_when_project_not_found(self):
        fake_db = _FakeDB(project_doc=None)
        orchestrator_call = AsyncMock(return_value={"ok": True})

        with patch(
            "app.projects.recommendations.recommendation_orchestrator.recompute_dirty_recommendations",
            new=orchestrator_call,
        ):
            result = await scheduler_executors.execute_recommendation_incremental(
                fake_db,
                {"project_id": "proj-0000-0000"},
            )

        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "project_not_found")
        orchestrator_call.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
