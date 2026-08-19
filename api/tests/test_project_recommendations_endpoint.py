import unittest
import importlib
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from app.projects.schemas import RecommendationFeedbackRequest, RecommendationThresholdsUpdateRequest

project_router_module = importlib.import_module("app.projects.router")


class ProjectRecommendationsEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_404_when_project_not_found(self):
        with patch.object(
            project_router_module.ProjectService,
            "get_by_id",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await project_router_module.get_project_recommendations(project_id="missing-project")
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_returns_400_for_invalid_bucket(self):
        with patch.object(
            project_router_module.ProjectService,
            "get_by_id",
            new=AsyncMock(return_value={"project_id": "proj-1234-5678"}),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await project_router_module.get_project_recommendations(
                    project_id="mongo-id",
                    bucket="invalid_bucket",
                )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_returns_limited_recommendations(self):
        fake_project = {
            "project_id": "proj-1234-5678",
            "timezone": "Asia/Kolkata",
            "workflow_capabilities": {"has_sprints": False},
        }
        fake_health = {"work_items": [{"task_key": "A"}, {"task_key": "B"}]}
        fake_recommendations = [
            {
                "id": "reco-A",
                "taskKey": "A",
                "title": "Task A",
                "reason": "Overdue",
                "recommendation": "Recover today",
                "severity": "critical",
                "bucket": "active",
                "category": "work_item",
                "confidence": 0.8,
                "why": ["Overdue"],
            },
            {
                "id": "reco-B",
                "taskKey": "B",
                "title": "Task B",
                "reason": "No update",
                "recommendation": "Post update",
                "severity": "at_risk",
                "bucket": "up_next",
                "category": "work_item",
                "confidence": 0.65,
                "why": ["No update"],
            },
        ]

        with patch.object(
            project_router_module.ProjectService,
            "get_by_id",
            new=AsyncMock(return_value=fake_project),
        ), patch(
            "app.projects.router._compute_project_health_live",
            new=AsyncMock(return_value=fake_health),
        ), patch(
            "app.projects.router.build_recommendations_from_health",
            return_value=fake_recommendations,
        ) as build_mock, patch(
            "app.projects.router.get_cached_api_project_recommendations",
            return_value=None,
        ), patch(
            "app.projects.router.record_recommendation_event",
            new=AsyncMock(return_value=True),
        ) as telemetry_mock:
            payload = await project_router_module.get_project_recommendations(
                project_id="mongo-id",
                limit=1,
                bucket=None,
                include_supporting=True,
            )

        self.assertEqual(payload["project_id"], "mongo-id")
        self.assertEqual(payload["custom_project_id"], "proj-1234-5678")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(len(payload["recommendations"]), 1)
        self.assertEqual(payload["recommendations"][0]["id"], "reco-A")
        telemetry_mock.assert_awaited_once()
        call_health_payload = build_mock.call_args.args[0]
        self.assertEqual(call_health_payload.get("project_timezone"), "Asia/Kolkata")
        self.assertEqual(call_health_payload.get("workflow_capabilities"), {"has_sprints": False})

    async def test_filters_by_bucket(self):
        fake_project = {"project_id": "proj-1234-5678"}
        fake_health = {"work_items": [{"task_key": "A"}, {"task_key": "B"}]}
        fake_recommendations = [
            {
                "id": "reco-A",
                "taskKey": "A",
                "title": "Task A",
                "reason": "Overdue",
                "recommendation": "Recover today",
                "severity": "critical",
                "bucket": "active",
                "category": "work_item",
                "confidence": 0.8,
                "why": ["Overdue"],
            },
            {
                "id": "reco-B",
                "taskKey": "B",
                "title": "Task B",
                "reason": "No update",
                "recommendation": "Post update",
                "severity": "at_risk",
                "bucket": "up_next",
                "category": "work_item",
                "confidence": 0.65,
                "why": ["No update"],
            },
        ]

        with patch.object(
            project_router_module.ProjectService,
            "get_by_id",
            new=AsyncMock(return_value=fake_project),
        ), patch(
            "app.projects.router._compute_project_health_live",
            new=AsyncMock(return_value=fake_health),
        ), patch(
            "app.projects.router.build_recommendations_from_health",
            return_value=fake_recommendations,
        ), patch(
            "app.projects.router.record_recommendation_event",
            new=AsyncMock(return_value=True),
        ) as telemetry_mock:
            payload = await project_router_module.get_project_recommendations(
                project_id="mongo-id",
                bucket="up_next",
                limit=50,
                include_supporting=True,
            )

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["recommendations"][0]["bucket"], "up_next")
        telemetry_mock.assert_awaited_once()

    async def test_feedback_endpoint_returns_404_when_project_not_found(self):
        with patch.object(
            project_router_module.ProjectService,
            "get_by_id",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await project_router_module.post_recommendation_feedback(
                    project_id="missing",
                    payload=RecommendationFeedbackRequest(task_key="PROJ-1", action="accepted"),
                )
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_feedback_endpoint_persists_event(self):
        with patch.object(
            project_router_module.ProjectService,
            "get_by_id",
            new=AsyncMock(return_value={"project_id": "proj-1234-5678"}),
        ), patch(
            "app.projects.router.record_recommendation_event",
            new=AsyncMock(return_value=True),
        ) as record_mock:
            payload = await project_router_module.post_recommendation_feedback(
                project_id="mongo-id",
                payload=RecommendationFeedbackRequest(
                    task_key="PROJ-1",
                    action="dismissed",
                    recommendation_id="reco-1",
                    source="pm_board",
                    actor_user_id="user-1",
                ),
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "dismissed")
        record_mock.assert_awaited_once()

    async def test_feedback_endpoint_returns_500_when_write_fails(self):
        with patch.object(
            project_router_module.ProjectService,
            "get_by_id",
            new=AsyncMock(return_value={"project_id": "proj-1234-5678"}),
        ), patch(
            "app.projects.router.record_recommendation_event",
            new=AsyncMock(return_value=False),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await project_router_module.post_recommendation_feedback(
                    project_id="mongo-id",
                    payload=RecommendationFeedbackRequest(task_key="PROJ-1", action="completed"),
                )
        self.assertEqual(ctx.exception.status_code, 500)

    async def test_recommendations_uses_short_ttl_cache_on_hit(self):
        fake_project = {"project_id": "proj-1234-5678"}
        cached_payload = {
            "recommendations": [
                {
                    "id": "reco-A",
                    "taskKey": "A",
                    "recommendation": "Recover today",
                    "severity": "critical",
                    "bucket": "active",
                    "category": "work_item",
                }
            ]
        }
        with patch.object(
            project_router_module.ProjectService,
            "get_by_id",
            new=AsyncMock(return_value=fake_project),
        ), patch(
            "app.projects.router.get_cached_api_project_recommendations",
            return_value=cached_payload,
        ), patch(
            "app.projects.router._compute_project_health_live",
            new=AsyncMock(),
        ) as health_mock, patch(
            "app.projects.router.build_recommendations_from_health",
            return_value=[],
        ) as build_mock, patch(
            "app.projects.router.record_recommendation_event",
            new=AsyncMock(return_value=True),
        ):
            payload = await project_router_module.get_project_recommendations(
                project_id="mongo-id",
                limit=50,
                bucket=None,
                include_supporting=True,
            )
        self.assertEqual(payload["count"], 1)
        health_mock.assert_not_awaited()
        build_mock.assert_not_called()

    async def test_recommendations_sets_short_ttl_cache_on_miss(self):
        fake_project = {"project_id": "proj-1234-5678"}
        fake_health = {"work_items": [{"task_key": "A"}]}
        fake_recommendations = [
            {
                "id": "reco-A",
                "taskKey": "A",
                "recommendation": "Recover today",
                "severity": "critical",
                "bucket": "active",
                "category": "work_item",
            }
        ]
        with patch.object(
            project_router_module.ProjectService,
            "get_by_id",
            new=AsyncMock(return_value=fake_project),
        ), patch(
            "app.projects.router.get_cached_api_project_recommendations",
            return_value=None,
        ), patch(
            "app.projects.router._compute_project_health_live",
            new=AsyncMock(return_value=fake_health),
        ), patch(
            "app.projects.router.build_recommendations_from_health",
            return_value=fake_recommendations,
        ), patch(
            "app.projects.router.set_cached_api_project_recommendations",
            return_value=None,
        ) as set_cache_mock, patch(
            "app.projects.router.record_recommendation_event",
            new=AsyncMock(return_value=True),
        ):
            payload = await project_router_module.get_project_recommendations(
                project_id="mongo-id",
                limit=50,
                bucket=None,
                include_supporting=True,
            )
        self.assertEqual(payload["count"], 1)
        set_cache_mock.assert_called_once()

    async def test_get_recommendation_thresholds_returns_404_when_project_missing(self):
        with patch.object(
            project_router_module.ProjectService,
            "get_by_id",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await project_router_module.get_project_recommendation_thresholds("missing-project")
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_get_recommendation_thresholds_returns_resolved_payload(self):
        fake_project = {
            "project_id": "proj-1234-5678",
            "recommendation_thresholds": {"overload_threshold": 82},
        }
        with patch.object(
            project_router_module.ProjectService,
            "get_by_id",
            new=AsyncMock(return_value=fake_project),
        ):
            payload = await project_router_module.get_project_recommendation_thresholds("mongo-id")
        self.assertEqual(payload["project_id"], "mongo-id")
        self.assertEqual(payload["custom_project_id"], "proj-1234-5678")
        self.assertIn("thresholds", payload)
        self.assertIn("sources", payload)
        self.assertEqual(payload["sources"]["project_override"]["overload_threshold"], 82)

    async def test_update_recommendation_thresholds_returns_400_for_empty_payload(self):
        fake_project = {"project_id": "proj-1234-5678"}
        with patch.object(
            project_router_module.ProjectService,
            "get_by_id",
            new=AsyncMock(return_value=fake_project),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await project_router_module.update_project_recommendation_thresholds(
                    "mongo-id",
                    RecommendationThresholdsUpdateRequest(),
                )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_update_recommendation_thresholds_persists_override_and_invalidates_caches(self):
        existing_project = {
            "project_id": "proj-1234-5678",
            "recommendation_thresholds": {"due_soon_hours": 48},
        }
        updated_project = {
            "project_id": "proj-1234-5678",
            "recommendation_thresholds": {"due_soon_hours": 48, "overload_threshold": 79.0},
        }
        with patch.object(
            project_router_module.ProjectService,
            "get_by_id",
            new=AsyncMock(return_value=existing_project),
        ), patch.object(
            project_router_module.ProjectService,
            "update",
            new=AsyncMock(return_value=updated_project),
        ) as update_mock, patch(
            "app.projects.router.invalidate_cached_api_project_recommendations",
        ) as invalidate_api_mock, patch(
            "app.projects.router.invalidate_pm_board_summary_cache",
        ) as invalidate_summary_mock:
            payload = await project_router_module.update_project_recommendation_thresholds(
                "mongo-id",
                RecommendationThresholdsUpdateRequest(overload_threshold=79.0),
            )

        update_mock.assert_awaited_once()
        update_args = update_mock.await_args.args
        self.assertEqual(update_args[0], "mongo-id")
        self.assertEqual(
            update_args[1]["recommendation_thresholds"],
            {"due_soon_hours": 48, "overload_threshold": 79.0},
        )
        invalidate_api_mock.assert_called_once_with("proj-1234-5678")
        invalidate_summary_mock.assert_called_once_with("proj-1234-5678")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["sources"]["project_override"]["overload_threshold"], 79.0)

    async def test_update_recommendation_thresholds_can_clear_overrides(self):
        existing_project = {
            "project_id": "proj-1234-5678",
            "recommendation_thresholds": {"overload_threshold": 81},
        }
        updated_project = {
            "project_id": "proj-1234-5678",
            "recommendation_thresholds": {},
        }
        with patch.object(
            project_router_module.ProjectService,
            "get_by_id",
            new=AsyncMock(return_value=existing_project),
        ), patch.object(
            project_router_module.ProjectService,
            "update",
            new=AsyncMock(return_value=updated_project),
        ) as update_mock, patch(
            "app.projects.router.invalidate_cached_api_project_recommendations",
        ), patch(
            "app.projects.router.invalidate_pm_board_summary_cache",
        ):
            payload = await project_router_module.update_project_recommendation_thresholds(
                "mongo-id",
                RecommendationThresholdsUpdateRequest(clear_overrides=True),
            )
        self.assertEqual(update_mock.await_args.args[1]["recommendation_thresholds"], {})
        self.assertEqual(payload["sources"]["project_override"], {})


if __name__ == "__main__":
    unittest.main()
