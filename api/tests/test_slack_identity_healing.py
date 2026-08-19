import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.integrations import router
from app.integrations.slack import member_sync, project_resolver


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    async def to_list(self, length=0):
        if length and length > 0:
            return list(self._rows[:length])
        return list(self._rows)


class _FakeProjectsCollection:
    def __init__(self, rows):
        self._rows = list(rows)

    def find(self, _query):
        return _FakeCursor(self._rows)


class _FakeDB:
    def __init__(self, rows):
        self.projects = _FakeProjectsCollection(rows)


class SlackResolverIdentityHealingTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_slack_project_falls_back_to_email_and_caches_mapping(self):
        project = {
            "_id": "mongo-1",
            "project_id": "proj-1",
            "project_name": "EduCare",
            "members": [
                {
                    "status": "active",
                    "email": "owner@example.com",
                    "integration_ids": {"slack_user_id": "U-OLD"},
                }
            ],
            "integrations": {
                "slack": {
                    "team_id": "T1",
                    "status": "connected",
                    "selected_channels": [{"channel_id": "C1"}],
                }
            },
        }
        member = project["members"][0]
        db = _FakeDB([project])

        with patch(
            "app.integrations.slack.project_resolver._find_projects_with_active_member",
            new=AsyncMock(side_effect=[[], [(project, member)]]),
        ), patch(
            "app.integrations.slack.project_resolver._get_slack_user_email",
            new=AsyncMock(return_value=("owner@example.com", True)),
        ), patch(
            "app.integrations.slack.project_resolver._cache_member_slack_user_id",
            new=AsyncMock(),
        ) as cache_mock:
            resolution = await project_resolver.resolve_slack_project(
                db=db,
                team_id="T1",
                slack_user_id="U-NEW",
                channel_id="C1",
                slack_access_token="xoxb-token",
            )

        self.assertTrue(resolution.ok)
        self.assertEqual(resolution.reason, "resolved")
        self.assertEqual(resolution.identity_resolution_path, "slack_id_fallback_email")
        cache_mock.assert_awaited_once()

    async def test_list_member_projects_falls_back_to_email_when_slack_id_mismatch(self):
        project = {
            "_id": "mongo-1",
            "project_id": "proj-1",
            "project_name": "EduCare",
            "members": [
                {
                    "status": "active",
                    "email": "owner@example.com",
                    "integration_ids": {"slack_user_id": "U-OLD"},
                }
            ],
            "integrations": {"slack": {"team_id": "T1", "status": "connected"}},
        }
        member = project["members"][0]
        db = _FakeDB([project])

        with patch(
            "app.integrations.slack.project_resolver._find_projects_with_active_member",
            new=AsyncMock(side_effect=[[], [(project, member)]]),
        ), patch(
            "app.integrations.slack.project_resolver._get_slack_user_email",
            new=AsyncMock(return_value=("owner@example.com", True)),
        ), patch(
            "app.integrations.slack.project_resolver._cache_member_slack_user_id",
            new=AsyncMock(),
        ), patch(
            "app.integrations.slack.project_resolver.read_slack_active_project_from_user_context",
            new=AsyncMock(return_value=None),
        ):
            listing = await project_resolver.list_slack_member_projects(
                db=db,
                team_id="T1",
                slack_user_id="U-NEW",
                slack_access_token="xoxb-token",
            )

        self.assertTrue(listing.ok)
        self.assertEqual(listing.identity_resolution_path, "slack_id_fallback_email")
        self.assertEqual(len(listing.projects), 1)


class SlackConnectDisconnectIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_clears_member_level_slack_identity_fields(self):
        project_id = "65f1c2d92b7f6e4c12345678"
        existing_project = {
            "project_id": "proj-1234-5678",
            "integrations": {"slack": {"team_id": "T1"}},
        }
        update_result = SimpleNamespace(modified_count=1)
        projects = SimpleNamespace(
            find_one=AsyncMock(return_value=existing_project),
            update_one=AsyncMock(return_value=update_result),
        )
        db = SimpleNamespace(projects=projects)

        with patch("app.integrations.router.get_database", return_value=db), patch(
            "app.integrations.router.invalidate_pm_board_summary_cache_by_object_id",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.integrations.router.clear_active_project_rows",
            new=AsyncMock(return_value=1),
        ):
            response = await router.disconnect_slack(
                project_id=project_id,
                current_user={"user_id": "owner@example.com"},
            )

        self.assertEqual(response.get("message"), "Slack disconnected successfully")
        update_doc = projects.update_one.await_args.args[1]
        unset_doc = update_doc.get("$unset", {})
        self.assertIn("members.$[].integration_ids.slack_user_id", unset_doc)
        self.assertIn("members.$[].integration_ids.slack_welcome_sent_at", unset_doc)
        self.assertIn("members.$[].integration_ids.slack_welcome_sent_team_id", unset_doc)

    async def test_post_connect_sync_runs_with_overwrite_enabled(self):
        with patch(
            "app.integrations.router.sync_project_slack_member_ids_detailed",
            new=AsyncMock(return_value={"status": "ok", "counts": {}}),
        ) as bulk_sync, patch(
            "app.integrations.router.send_project_welcome_to_all_active_members_detailed",
            new=AsyncMock(return_value={"status": "ok", "counts": {}}),
        ):
            await router._run_slack_post_connect_tasks_in_background(
                db=object(),
                project_id="proj-1234-5678",
            )

        self.assertTrue(bulk_sync.await_count == 1)
        self.assertTrue(bulk_sync.await_args.kwargs.get("overwrite"))


class SlackMemberSyncHealingTests(unittest.IsolatedAsyncioTestCase):
    async def test_bulk_sync_revalidates_members_even_if_slack_id_exists(self):
        project = {
            "_id": "mongo-1",
            "project_id": "proj-1234-5678",
            "members": [
                {
                    "status": "active",
                    "email": "mapped@example.com",
                    "integration_ids": {"slack_user_id": "U-MAPPED"},
                },
                {
                    "status": "active",
                    "email": "new@example.com",
                    "integration_ids": {},
                },
            ],
            "integrations": {
                "slack": {
                    "status": "connected",
                    "access_token": "encrypted-token",
                }
            },
        }

        db = SimpleNamespace(
            projects=SimpleNamespace(
                update_one=AsyncMock(return_value=SimpleNamespace(acknowledged=True)),
            )
        )

        with patch(
            "app.integrations.slack.member_sync._load_project_for_sync",
            new=AsyncMock(return_value=project),
        ), patch(
            "app.integrations.slack.member_sync.encryption_service.decrypt",
            return_value="xoxb-token",
        ), patch(
            "app.integrations.slack.member_sync.SlackClient",
            return_value=SimpleNamespace(),
        ), patch(
            "app.integrations.slack.member_sync._sync_member_with_project",
            new=AsyncMock(
                side_effect=[
                    {"status": "already_mapped"},
                    {"status": "mapped"},
                ]
            ),
        ) as sync_mock:
            result = await member_sync.sync_project_slack_member_ids_detailed(
                db=db,
                project_id="proj-1234-5678",
                overwrite=False,
            )

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("members_attempted"), 2)
        self.assertEqual(sync_mock.await_count, 2)


if __name__ == "__main__":
    unittest.main()
