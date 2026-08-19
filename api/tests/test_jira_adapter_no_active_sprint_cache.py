import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.tasks.providers.jira_adapter import JiraAdapter
from app.tasks.schemas import SprintDirective


class _FakeRedis:
    def __init__(self, *, cached: bool = False):
        self.cached = cached
        self.set_calls = []
        self.delete_calls = []

    def get(self, _key):
        return b"1" if self.cached else None

    def set(self, key, value, ex=None):
        self.set_calls.append((key, value, ex))
        self.cached = True
        return True

    def delete(self, key):
        self.delete_calls.append(key)
        self.cached = False
        return 1


class JiraAdapterNoActiveSprintCacheTests(unittest.TestCase):
    def _build_adapter(self) -> JiraAdapter:
        project = {
            "project_id": "proj-1234-5678",
            "integrations": {
                "jira": {
                    "cloud_id": "cloud-1",
                    "site_url": "https://example.atlassian.net",
                    "access_token": "",
                    "refresh_token": None,
                    "default_board_id": 35,
                    "default_board_type": "scrum",
                }
            },
        }
        return JiraAdapter(project, access_token_override="token")

    def test_cached_no_active_sprint_skips_jira_lookup(self):
        adapter = self._build_adapter()
        redis_client = _FakeRedis(cached=True)

        with patch(
            "app.tasks.providers.jira_adapter.get_redis_connection",
            return_value=redis_client,
        ), patch(
            "app.tasks.providers.jira_adapter.jira_oauth_service.get_active_sprint_for_board",
            new_callable=AsyncMock,
        ) as active_sprint_mock:
            result = asyncio.run(
                adapter._apply_sprint_directive(
                    issue_key="PRM-100",
                    directive=SprintDirective.ACTIVE_SPRINT,
                    trigger="status_update",
                )
            )

        self.assertIsNotNone(result)
        self.assertFalse(bool(result.applied))
        self.assertEqual(result.reason, "no_active_sprint")
        active_sprint_mock.assert_not_awaited()

    def test_no_active_sprint_response_is_cached(self):
        adapter = self._build_adapter()
        redis_client = _FakeRedis(cached=False)

        with patch(
            "app.tasks.providers.jira_adapter.get_redis_connection",
            return_value=redis_client,
        ), patch(
            "app.tasks.providers.jira_adapter.jira_oauth_service.get_active_sprint_for_board",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = asyncio.run(
                adapter._apply_sprint_directive(
                    issue_key="PRM-101",
                    directive=SprintDirective.ACTIVE_SPRINT,
                    trigger="status_update",
                )
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.reason, "no_active_sprint")
        self.assertGreaterEqual(len(redis_client.set_calls), 1)
        _, _, ttl_seconds = redis_client.set_calls[0]
        self.assertIsNotNone(ttl_seconds)
        self.assertGreaterEqual(int(ttl_seconds), 10)


if __name__ == "__main__":
    unittest.main()
