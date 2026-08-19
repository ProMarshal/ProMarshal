import unittest
from unittest.mock import AsyncMock, patch
from fastapi import Response

from app.planner.router import (
    chat_with_agent,
    extract_stage_content,
    get_planner_bootstrap,
    get_planner_status,
    get_stage_entities,
)
from app.planner.schemas import ChatMessage


class _FakeProjects:
    def __init__(self, doc):
        self._doc = doc

    async def find_one(self, query):
        return self._doc


class _FakeDB:
    def __init__(self, doc):
        self.projects = _FakeProjects(doc)


class PlannerRouterCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_returns_cache_hit(self):
        with (
            patch("app.planner.router.get_database", return_value=_FakeDB({"project_id": "proj-1111-2222"})),
            patch("app.planner.router.planner_cache_enabled", return_value=True),
            patch("app.planner.router.get_cached_planner_status", return_value={"current_stage": "goal"}) as mock_get,
            patch("app.planner.router.planner_service.get_planner_status", new_callable=AsyncMock) as mock_service,
        ):
            result = await get_planner_status("proj-1111-2222")
        mock_get.assert_called_once_with("proj-1111-2222")
        mock_service.assert_not_awaited()
        self.assertEqual(result.get("current_stage"), "goal")

    async def test_entities_miss_sets_cache(self):
        payload = {"items": ["a"], "status": "draft"}
        with (
            patch("app.planner.router.validate_topic_stage", new_callable=AsyncMock),
            patch("app.planner.router.planner_cache_enabled", return_value=True),
            patch("app.planner.router.get_cached_stage_entities", return_value=None),
            patch("app.planner.router.set_cached_stage_entities") as mock_set,
            patch("app.planner.router.planner_service.get_stage_entities", new_callable=AsyncMock, return_value=payload),
        ):
            result = await get_stage_entities("proj-1111-2222", "goal")
        mock_set.assert_called_once_with("proj-1111-2222", "goal", payload)
        self.assertEqual(result, payload)

    async def test_chat_invalidates_stage_cache(self):
        with (
            patch("app.planner.router.validate_topic_stage", new_callable=AsyncMock),
            patch("app.planner.router.planner_service.chat", new_callable=AsyncMock, return_value="ok"),
            patch("app.planner.router._invalidate_planner_read_caches") as mock_invalidate,
        ):
            message = ChatMessage(content="hello", stage="goal")
            response = await chat_with_agent("proj-1111-2222", message)
        mock_invalidate.assert_called_once_with("proj-1111-2222", stage="goal")
        self.assertEqual(response.content, "ok")

    async def test_extract_invalidates_stage_cache(self):
        with (
            patch("app.planner.router.validate_topic_stage", new_callable=AsyncMock),
            patch(
                "app.planner.router.planner_service.extract_stage_content",
                new_callable=AsyncMock,
                return_value=["Point A"],
            ),
            patch("app.planner.router._invalidate_planner_read_caches") as mock_invalidate,
        ):
            response = await extract_stage_content("proj-1111-2222", "goal")
        mock_invalidate.assert_called_once_with("proj-1111-2222", stage="goal")
        self.assertEqual(response.get("content"), ["Point A"])

    async def test_bootstrap_returns_cached_payload_hit(self):
        cached_payload = {"project_id": "proj-1111-2222", "entities_by_stage": {"goal": {"items": ["a"], "status": "draft"}}}
        with (
            patch("app.planner.router.get_database", return_value=_FakeDB({"project_id": "proj-1111-2222"})),
            patch("app.planner.router.get_cached_planner_bootstrap_payload", return_value=cached_payload),
        ):
            result = await get_planner_bootstrap("proj-1111-2222", Response())
        self.assertEqual(result, cached_payload)

    async def test_bootstrap_sync_when_async_disabled(self):
        full_payload = {"project_id": "proj-1111-2222", "entities_by_stage": {"goal": {"items": ["a"], "status": "draft"}}}
        with (
            patch("app.planner.router.get_database", return_value=_FakeDB({"project_id": "proj-1111-2222"})),
            patch("app.planner.router.get_cached_planner_bootstrap_payload", return_value=None),
            patch("app.planner.router._planner_bootstrap_async_enabled", return_value=False),
            patch("app.planner.router._compose_planner_bootstrap", new_callable=AsyncMock, return_value=full_payload) as mock_compose,
            patch("app.planner.router.set_cached_planner_bootstrap_payload") as mock_set,
        ):
            result = await get_planner_bootstrap("proj-1111-2222", Response())
        mock_compose.assert_awaited_once()
        mock_set.assert_called_once_with("proj-1111-2222", full_payload)
        self.assertEqual(result, full_payload)

    async def test_bootstrap_async_returns_202_with_partial_payload(self):
        partial_payload = {"project_id": "proj-1111-2222", "entities_by_stage": {"goal": {"items": ["a"], "status": "draft"}}}
        with (
            patch("app.planner.router.get_database", return_value=_FakeDB({"project_id": "proj-1111-2222"})),
            patch("app.planner.router.get_cached_planner_bootstrap_payload", return_value=None),
            patch("app.planner.router._planner_bootstrap_async_enabled", return_value=True),
            patch("app.planner.router._compose_planner_bootstrap_partial", new_callable=AsyncMock, return_value=partial_payload),
            patch("app.planner.router._enqueue_planner_bootstrap_job", return_value={"accepted": True, "job_id": "job-1"}),
        ):
            response = Response()
            result = await get_planner_bootstrap("proj-1111-2222", response)
        self.assertIsInstance(result, dict)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(result.get("status"), "processing")

    async def test_bootstrap_async_partial_failure_still_returns_202(self):
        with (
            patch("app.planner.router.get_database", return_value=_FakeDB({"project_id": "proj-1111-2222"})),
            patch("app.planner.router.get_cached_planner_bootstrap_payload", return_value=None),
            patch("app.planner.router._planner_bootstrap_async_enabled", return_value=True),
            patch("app.planner.router._compose_planner_bootstrap_partial", new_callable=AsyncMock, side_effect=RuntimeError("boom")),
            patch("app.planner.router._enqueue_planner_bootstrap_job", return_value={"accepted": True, "job_id": "job-2"}),
        ):
            response = Response()
            result = await get_planner_bootstrap("proj-1111-2222", response)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(result.get("status"), "processing")
        self.assertIn("bootstrap", result)


if __name__ == "__main__":
    unittest.main()
