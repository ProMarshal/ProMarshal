import importlib
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

project_router_module = importlib.import_module("app.projects.router")


class PMBoardSummaryCharterStatusCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_compute_summary_uses_shared_planner_cache_hit(self):
        cached_status = {"current_stage": "goal", "stages": {"goal": "in_progress"}}
        planner_status_mock = AsyncMock(return_value={"current_stage": "scope"})

        with ExitStack() as stack:
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_overdue_tasks", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_tasks_due_today", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_tasks_due_tomorrow", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_bottlenecks", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_tasks_due_24h", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_tomorrow_availability", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_unassigned_urgent", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_silent_tasks", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_stale_reviews", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_members", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_orphan_assignees", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_burndown_data", new=AsyncMock(return_value=None)))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_velocity_trend", new=AsyncMock(return_value=None)))
            stack.enter_context(patch("app.projects.router._count_open_action_items", new=AsyncMock(return_value=0)))
            stack.enter_context(patch("app.projects.router._compute_project_health_live", new=AsyncMock(return_value={})))
            stack.enter_context(patch("app.projects.router.build_recommendations_from_health", return_value=[]))
            stack.enter_context(patch("app.projects.router._apply_recommendations_to_health", side_effect=lambda health, _: health))
            stack.enter_context(patch("app.projects.router.get_cached_planner_status", return_value=cached_status))
            set_cached_mock = stack.enter_context(patch("app.projects.router.set_cached_planner_status"))
            stack.enter_context(patch("app.planner.service.planner_service.get_planner_status", planner_status_mock))

            payload = await project_router_module._compute_pm_board_summary_payload(
                project_id="mongo-project-id",
                custom_project_id="proj-1111-2222",
                project={"project_id": "proj-1111-2222"},
            )

        self.assertEqual(payload.get("charter_status"), cached_status)
        planner_status_mock.assert_not_awaited()
        set_cached_mock.assert_not_called()

    async def test_compute_summary_writes_shared_planner_cache_on_miss(self):
        live_status = {"current_stage": "scope", "stages": {"goal": "finalized", "scope": "in_progress"}}
        planner_status_mock = AsyncMock(return_value=live_status)

        with ExitStack() as stack:
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_overdue_tasks", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_tasks_due_today", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_tasks_due_tomorrow", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_bottlenecks", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_tasks_due_24h", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_tomorrow_availability", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_unassigned_urgent", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_silent_tasks", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_stale_reviews", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_members", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_orphan_assignees", new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_burndown_data", new=AsyncMock(return_value=None)))
            stack.enter_context(patch.object(project_router_module.ProjectService, "get_velocity_trend", new=AsyncMock(return_value=None)))
            stack.enter_context(patch("app.projects.router._count_open_action_items", new=AsyncMock(return_value=0)))
            stack.enter_context(patch("app.projects.router._compute_project_health_live", new=AsyncMock(return_value={})))
            stack.enter_context(patch("app.projects.router.build_recommendations_from_health", return_value=[]))
            stack.enter_context(patch("app.projects.router._apply_recommendations_to_health", side_effect=lambda health, _: health))
            stack.enter_context(patch("app.projects.router.get_cached_planner_status", return_value=None))
            set_cached_mock = stack.enter_context(patch("app.projects.router.set_cached_planner_status"))
            stack.enter_context(patch("app.planner.service.planner_service.get_planner_status", planner_status_mock))

            payload = await project_router_module._compute_pm_board_summary_payload(
                project_id="mongo-project-id",
                custom_project_id="proj-1111-2222",
                project={"project_id": "proj-1111-2222"},
            )

        self.assertEqual(payload.get("charter_status"), live_status)
        planner_status_mock.assert_awaited_once_with("proj-1111-2222")
        set_cached_mock.assert_called_once_with("proj-1111-2222", live_status)


if __name__ == "__main__":
    unittest.main()

