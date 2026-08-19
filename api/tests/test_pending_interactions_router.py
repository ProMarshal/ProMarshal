import unittest
from unittest.mock import AsyncMock, patch

from app.integrations.pending_interactions_router import route_pending_dm_interactions


class PendingInteractionsRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_team_poll_active_session_routes_terminal(self):
        with patch(
            "app.integrations.pending_interactions_router.list_pending_interactions_by_external_identity",
            new=AsyncMock(
                return_value=[
                    {
                        "interaction_id": "pi-1",
                        "context_payload": {"session_id": "s1", "project_id": "proj-1"},
                    }
                ]
            ),
        ), patch(
            "app.integrations.pending_interactions_router.get_team_poll_session",
            new=AsyncMock(return_value={"session_id": "s1", "project_id": "proj-1"}),
        ), patch(
            "app.integrations.pending_interactions_router.handle_team_poll_dm",
            new=AsyncMock(return_value=True),
        ):
            result = await route_pending_dm_interactions(
                db=object(),
                team_id="T1",
                channel="D1",
                slack_user_id="U1",
                text="my response",
                thread_ts=None,
                get_workspace_slack_client=AsyncMock(return_value=AsyncMock()),
                project_resolution=None,
                enable_team_poll=True,
                enable_action_item=False,
            )

        self.assertTrue(result.get("handled"))
        self.assertEqual(result.get("route"), "team_poll_active_session")

    async def test_action_item_pending_routes_terminal(self):
        resolution = type(
            "Resolution",
            (),
            {
                "project": {"project_id": "proj-1", "user_id": "u-owner"},
                "member": {"user_id": "u-1", "role": "member"},
            },
        )()
        with patch(
            "app.integrations.pending_interactions_router.handle_pending_action_item_clarification",
            new=AsyncMock(return_value={"handled": True, "response_text": "ok"}),
        ):
            result = await route_pending_dm_interactions(
                db=object(),
                team_id="T1",
                channel="D1",
                slack_user_id="U1",
                text="assign to prabhu",
                thread_ts=None,
                get_workspace_slack_client=AsyncMock(return_value=None),
                project_resolution=resolution,
                project_slack_client=AsyncMock(),
                enable_team_poll=False,
                enable_action_item=True,
            )

        self.assertTrue(result.get("handled"))
        self.assertEqual(result.get("route"), "action_item_pending_clarification")

    async def test_team_poll_stale_pending_session_is_cleaned(self):
        with patch(
            "app.integrations.pending_interactions_router.list_pending_interactions_by_external_identity",
            new=AsyncMock(
                return_value=[
                    {
                        "interaction_id": "pi-1",
                        "context_payload": {"session_id": "missing", "project_id": "proj-1"},
                    }
                ]
            ),
        ), patch(
            "app.integrations.pending_interactions_router.get_team_poll_session",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.integrations.pending_interactions_router.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ) as resolve_pending:
            result = await route_pending_dm_interactions(
                db=object(),
                team_id="T1",
                channel="D1",
                slack_user_id="U1",
                text="hello",
                thread_ts=None,
                get_workspace_slack_client=AsyncMock(return_value=None),
                project_resolution=None,
                enable_team_poll=True,
                enable_action_item=False,
            )

        self.assertFalse(result.get("handled"))
        resolve_pending.assert_awaited_once()

    async def test_shared_router_precedence_team_poll_before_action_item(self):
        resolution = type(
            "Resolution",
            (),
            {
                "project": {"project_id": "proj-1", "user_id": "u-owner"},
                "member": {"user_id": "u-1", "role": "member"},
            },
        )()
        with patch(
            "app.integrations.pending_interactions_router.list_pending_interactions_by_external_identity",
            new=AsyncMock(
                return_value=[
                    {
                        "interaction_id": "pi-1",
                        "context_payload": {"session_id": "s1", "project_id": "proj-1"},
                    }
                ]
            ),
        ), patch(
            "app.integrations.pending_interactions_router.get_team_poll_session",
            new=AsyncMock(return_value={"session_id": "s1", "project_id": "proj-1"}),
        ), patch(
            "app.integrations.pending_interactions_router.handle_team_poll_dm",
            new=AsyncMock(return_value=True),
        ), patch(
            "app.integrations.pending_interactions_router.handle_pending_action_item_clarification",
            new=AsyncMock(return_value={"handled": True, "response_text": "should_not_run"}),
        ) as action_handler:
            result = await route_pending_dm_interactions(
                db=object(),
                team_id="T1",
                channel="D1",
                slack_user_id="U1",
                text="my response",
                thread_ts=None,
                get_workspace_slack_client=AsyncMock(return_value=AsyncMock()),
                project_resolution=resolution,
                project_slack_client=AsyncMock(),
                enable_team_poll=True,
                enable_action_item=True,
            )
        self.assertTrue(result.get("handled"))
        self.assertEqual(result.get("route"), "team_poll_active_session")
        action_handler.assert_not_awaited()

    async def test_team_poll_non_relevant_reply_passes_to_cortex(self):
        with patch(
            "app.integrations.pending_interactions_router.list_pending_interactions_by_external_identity",
            new=AsyncMock(
                return_value=[
                    {
                        "interaction_id": "pi-1",
                        "context_payload": {"session_id": "s1", "project_id": "proj-1"},
                    }
                ]
            ),
        ), patch(
            "app.integrations.pending_interactions_router.get_team_poll_session",
            new=AsyncMock(return_value={"session_id": "s1", "project_id": "proj-1"}),
        ), patch(
            "app.integrations.pending_interactions_router.handle_team_poll_dm",
            new=AsyncMock(return_value=False),
        ):
            result = await route_pending_dm_interactions(
                db=object(),
                team_id="T1",
                channel="D1",
                slack_user_id="U1",
                text="assign PRM-80 to sridevi",
                thread_ts=None,
                get_workspace_slack_client=AsyncMock(return_value=AsyncMock()),
                project_resolution=None,
                enable_team_poll=True,
                enable_action_item=False,
            )

        self.assertFalse(result.get("handled"))

    async def test_action_item_handler_exception_fails_open_to_fallback(self):
        resolution = type(
            "Resolution",
            (),
            {
                "project": {"project_id": "proj-1", "user_id": "u-owner"},
                "member": {"user_id": "u-1", "role": "member"},
            },
        )()
        with patch(
            "app.integrations.pending_interactions_router.handle_pending_action_item_clarification",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await route_pending_dm_interactions(
                db=object(),
                team_id="T1",
                channel="D1",
                slack_user_id="U1",
                text="assign to someone",
                thread_ts=None,
                get_workspace_slack_client=AsyncMock(return_value=None),
                project_resolution=resolution,
                project_slack_client=AsyncMock(),
                enable_team_poll=False,
                enable_action_item=True,
            )
        self.assertFalse(result.get("handled"))

    async def test_work_item_parent_pending_routes_terminal(self):
        resolution = type(
            "Resolution",
            (),
            {
                "project": {"project_id": "proj-1", "user_id": "u-owner"},
                "member": {"user_id": "u-1", "role": "member"},
            },
        )()
        with patch(
            "app.integrations.pending_interactions_router.handle_pending_work_item_parent_clarification",
            new=AsyncMock(return_value={"handled": True, "response_text": "Created under PRM-44"}),
        ):
            result = await route_pending_dm_interactions(
                db=object(),
                team_id="T1",
                channel="D1",
                slack_user_id="U1",
                text="yes",
                thread_ts=None,
                get_workspace_slack_client=AsyncMock(return_value=None),
                project_resolution=resolution,
                project_slack_client=AsyncMock(),
                enable_team_poll=False,
                enable_work_item_parent=True,
                enable_action_item=False,
            )

        self.assertTrue(result.get("handled"))
        self.assertEqual(result.get("route"), "work_item_parent_pending_clarification")

    async def test_work_item_parent_pending_resume_passes_to_cortex(self):
        resolution = type(
            "Resolution",
            (),
            {
                "project": {"project_id": "proj-1", "user_id": "u-owner"},
                "member": {"user_id": "u-1", "role": "member"},
            },
        )()
        with patch(
            "app.integrations.pending_interactions_router.handle_pending_work_item_parent_clarification",
            new=AsyncMock(
                return_value={
                    "handled": False,
                    "resume_to_cortex": True,
                    "resume_message_text": 'create a subtask "demo subtask" under PRM-44 and assign to me',
                    "resume_target_refs": ["PRM-44"],
                }
            ),
        ):
            result = await route_pending_dm_interactions(
                db=object(),
                team_id="T1",
                channel="D1",
                slack_user_id="U1",
                text="1",
                thread_ts=None,
                get_workspace_slack_client=AsyncMock(return_value=None),
                project_resolution=resolution,
                project_slack_client=AsyncMock(),
                enable_team_poll=False,
                enable_work_item_parent=True,
                enable_action_item=False,
            )

        self.assertFalse(result.get("handled"))
        self.assertTrue(bool(result.get("resume_to_cortex")))
        self.assertEqual(
            str(result.get("resume_message_text") or "").strip(),
            'create a subtask "demo subtask" under PRM-44 and assign to me',
        )
        self.assertEqual(result.get("resume_target_refs"), ["PRM-44"])
        self.assertEqual(result.get("route"), "work_item_parent_resume_to_cortex")

    async def test_provider_type_pending_routes_terminal(self):
        resolution = type(
            "Resolution",
            (),
            {
                "project": {"project_id": "proj-1", "user_id": "u-owner"},
                "member": {"user_id": "u-1", "role": "member"},
            },
        )()
        with patch(
            "app.integrations.pending_interactions_router.handle_pending_provider_type_clarification",
            new=AsyncMock(return_value={"handled": True, "response_text": "Use Bug instead?"}),
        ):
            result = await route_pending_dm_interactions(
                db=object(),
                team_id="T1",
                channel="D1",
                slack_user_id="U1",
                text="yes",
                thread_ts=None,
                get_workspace_slack_client=AsyncMock(return_value=None),
                project_resolution=resolution,
                project_slack_client=AsyncMock(),
                enable_team_poll=False,
                enable_work_item_parent=False,
                enable_provider_type=True,
                enable_action_item=False,
            )

        self.assertTrue(result.get("handled"))
        self.assertEqual(result.get("route"), "provider_type_pending_clarification")

    async def test_provider_type_pending_resume_passes_to_cortex(self):
        resolution = type(
            "Resolution",
            (),
            {
                "project": {"project_id": "proj-1", "user_id": "u-owner"},
                "member": {"user_id": "u-1", "role": "member"},
            },
        )()
        with patch(
            "app.integrations.pending_interactions_router.handle_pending_provider_type_clarification",
            new=AsyncMock(
                return_value={
                    "handled": False,
                    "resume_to_cortex": True,
                    "resume_message_text": 'create a Bug "UI Testing" and assign to me',
                    "resume_target_refs": [],
                }
            ),
        ):
            result = await route_pending_dm_interactions(
                db=object(),
                team_id="T1",
                channel="D1",
                slack_user_id="U1",
                text="yes",
                thread_ts=None,
                get_workspace_slack_client=AsyncMock(return_value=None),
                project_resolution=resolution,
                project_slack_client=AsyncMock(),
                enable_team_poll=False,
                enable_work_item_parent=False,
                enable_provider_type=True,
                enable_action_item=False,
            )

        self.assertFalse(result.get("handled"))
        self.assertTrue(bool(result.get("resume_to_cortex")))
        self.assertEqual(result.get("route"), "provider_type_resume_to_cortex")


if __name__ == "__main__":
    unittest.main()
