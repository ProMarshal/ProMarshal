import importlib
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from fastapi import Response

project_router_module = importlib.import_module("app.projects.router")


class PMBoardSummaryRecommendationsContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_pm_board_summary_includes_recommendations(self):
        fake_health = {
            "health_type": "task",
            "summary": {"on_track": 0, "at_risk": 1, "critical": 1, "total_tasks": 2},
            "work_item_summary": {"total": 2, "completed": 0, "on_track": 0, "at_risk": 1, "critical": 1, "open_total": 2},
            "work_items": [
                {
                    "task_key": "PROJ-1",
                    "title": "Critical task",
                    "health_status": "critical",
                    "health_reason": "Overdue",
                    "health_action": "Recover today",
                },
                {
                    "task_key": "PROJ-2",
                    "title": "At risk task",
                    "health_status": "at_risk",
                    "health_reason": "No update",
                    "health_action": "Post update",
                },
            ],
        }

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    project_router_module.ProjectService,
                    "get_by_id",
                    new=AsyncMock(
                        return_value={
                            "project_id": "proj-1234-5678",
                            "timezone": "Asia/Kolkata",
                            "workflow_capabilities": {"has_sprints": True},
                        }
                    ),
                )
            )
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
            stack.enter_context(
                patch(
                    "app.projects.router._compute_project_health_live",
                    new=AsyncMock(return_value=fake_health),
                )
            )
            stack.enter_context(patch("app.projects.router.set_cached_pm_board_summary", return_value=None))
            build_mock = stack.enter_context(
                patch(
                    "app.projects.router.build_recommendations_from_health",
                    return_value=[
                        {"id": "reco-PROJ-1", "taskKey": "PROJ-1", "recommendation": "Recover today", "severity": "critical", "bucket": "active", "category": "work_item", "title": "Critical task", "reason": "Overdue"},
                        {"id": "reco-PROJ-2", "taskKey": "PROJ-2", "recommendation": "Post update", "severity": "at_risk", "bucket": "active", "category": "work_item", "title": "At risk task", "reason": "No update"},
                    ],
                )
            )
            stack.enter_context(
                patch(
                    "app.projects.router._apply_recommendations_to_health",
                    side_effect=lambda health, _: health,
                )
            )
            stack.enter_context(
                patch(
                    "app.projects.router._enqueue_pm_board_summary_job",
                    return_value={"accepted": False, "reason": "queue_unavailable"},
                )
            )
            telemetry_mock = stack.enter_context(
                patch(
                    "app.projects.router.record_recommendation_event",
                    new=AsyncMock(return_value=True),
                )
            )
            stack.enter_context(patch("app.projects.router.get_cached_pm_board_summary", return_value=None))
            stack.enter_context(
                patch(
                    "app.planner.service.planner_service.get_planner_status",
                    new=AsyncMock(return_value=None),
                )
            )

            payload = await project_router_module.get_pm_board_summary(
                project_id="mongo-project-id",
                response=Response(),
                trigger="manual_refresh",
            )

        self.assertIn("recommendations", payload)
        self.assertEqual(len(payload["recommendations"]), 2)
        self.assertEqual(payload["recommendations"][0]["taskKey"], "PROJ-1")
        self.assertEqual(payload["recommendations"][1]["taskKey"], "PROJ-2")
        work_items = payload["health"]["work_items"]
        action_by_key = {item["task_key"]: item.get("health_action") for item in work_items}
        self.assertEqual(action_by_key["PROJ-1"], "Recover today")
        self.assertEqual(action_by_key["PROJ-2"], "Post update")
        self.assertEqual(telemetry_mock.await_count, 2)
        call_health_payload = build_mock.call_args.args[0]
        self.assertEqual(call_health_payload.get("project_timezone"), "Asia/Kolkata")
        self.assertEqual(call_health_payload.get("workflow_capabilities"), {"has_sprints": True})


if __name__ == "__main__":
    unittest.main()
