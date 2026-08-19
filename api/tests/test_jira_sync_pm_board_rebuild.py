import importlib
import unittest
from unittest.mock import AsyncMock, patch


integrations_router_module = importlib.import_module("app.integrations.router")


class JiraSyncPmBoardRebuildTests(unittest.IsolatedAsyncioTestCase):
    async def test_force_rebuild_clears_cache_snapshot_marks_dirty_and_enqueues(self):
        fake_brain = AsyncMock()
        fake_brain.delete_one = AsyncMock(return_value={"acknowledged": True})

        with patch.object(
            integrations_router_module,
            "invalidate_pm_board_summary_cache",
            return_value=None,
        ) as invalidate_mock, patch.object(
            integrations_router_module,
            "mark_pm_board_summary_dirty",
            return_value=None,
        ) as dirty_mock, patch.object(
            integrations_router_module,
            "get_brain_collection",
            return_value=fake_brain,
        ), patch(
            "app.projects.router.trigger_pm_board_summary_recompute",
            return_value={"accepted": True, "reason": "queued"},
        ) as trigger_mock:
            await integrations_router_module._force_pm_board_rebuild_after_jira_sync(
                project_object_id="69bee8596fe6571c0813bf6a",
                custom_project_id="proj-5693-2556",
            )

        invalidate_mock.assert_called_once_with("proj-5693-2556")
        fake_brain.delete_one.assert_awaited_once_with(
            {
                "entity_type": "project_insights_snapshot",
                "external_id": "project-insights:v1",
            }
        )
        dirty_mock.assert_called_once_with(
            "proj-5693-2556",
            reason="sync_now_force_rebuild",
            source="jira_sync",
        )
        trigger_mock.assert_called_once_with(
            project_id="69bee8596fe6571c0813bf6a",
            custom_project_id="proj-5693-2556",
            trigger="jira_sync",
        )

    async def test_sync_now_refreshes_jira_create_metadata_cache(self):
        class _FakeCursor:
            async def to_list(self, length=None):
                return []

        class _FakeBrainCollection:
            def find(self, *_args, **_kwargs):
                return _FakeCursor()

        class _FakeProjectsCollection:
            def __init__(self, project_doc):
                self._project = project_doc

            async def find_one(self, query):
                if query.get("project_id") == self._project.get("project_id"):
                    return self._project
                return None

            async def update_one(self, *_args, **_kwargs):
                return {"acknowledged": True}

        project_doc = {
            "_id": "69bee8596fe6571c0813bf6a",
            "project_id": "proj-5693-2556",
            "integrations": {
                "jira": {
                    "status": "connected",
                    "default_project_key": "PRM",
                    "access_token": "enc_access",
                    "refresh_token": "enc_refresh",
                    "cloud_id": "cloud-1",
                    "site_url": "https://example.atlassian.net",
                }
            },
            "members": [],
        }
        fake_db = type("FakeDB", (), {"projects": _FakeProjectsCollection(project_doc)})()

        fake_adapter = AsyncMock()
        fake_adapter._fetch_and_cache_allowed_issue_types = AsyncMock(return_value=["Task", "Subtask"])

        with patch.object(
            integrations_router_module,
            "get_database",
            return_value=fake_db,
        ), patch.object(
            integrations_router_module.encryption_service,
            "decrypt",
            side_effect=lambda value: "token" if value == "enc_access" else "refresh",
        ), patch.object(
            integrations_router_module.jira_oauth_service,
            "search_issues",
            new=AsyncMock(return_value=[]),
        ), patch.object(
            integrations_router_module,
            "_upsert_jira_tasks_to_brain_and_mark_dirty",
            new=AsyncMock(return_value=0),
        ), patch.object(
            integrations_router_module,
            "get_brain_collection",
            return_value=_FakeBrainCollection(),
        ), patch.object(
            integrations_router_module,
            "_force_pm_board_rebuild_after_jira_sync",
            new=AsyncMock(return_value=None),
        ), patch.object(
            integrations_router_module,
            "JiraAdapter",
            return_value=fake_adapter,
        ), patch(
            "app.projects.capability_detector.WorkflowCapabilityDetector.detect_capabilities",
            return_value={
                "has_sprints": False,
                "has_epics": False,
                "grouping_hierarchy": ["status"],
            },
        ):
            result = await integrations_router_module.sync_jira_tasks("proj-5693-2556")

        fake_adapter._fetch_and_cache_allowed_issue_types.assert_awaited_once_with(
            jira_project_key="PRM"
        )
        self.assertTrue(bool(result.get("jira_create_metadata_refreshed")))


if __name__ == "__main__":
    unittest.main()
