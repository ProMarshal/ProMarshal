import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.team_poll.interaction_handler import handle_team_poll_dm


class Phase4CadenceTeamPollParityPackTests(unittest.IsolatedAsyncioTestCase):
    async def test_team_poll_owner_skip_text_uses_owner_skip_success_contract(self):
        session = {
            "session_id": "sid-1",
            "project_id": "proj-1",
            "poll_id": "poll-1",
            "owner_user_id": "U1",
            "user_id": "U1",
            "status": "active",
        }
        db = AsyncMock()
        db.projects.find_one = AsyncMock(return_value={"project_id": "proj-1"})
        slack_client = AsyncMock()

        with (
            patch("app.team_poll.interaction_handler.mark_owner_skipped", new=AsyncMock()) as mark_skip,
            patch("app.team_poll.interaction_handler.compose_team_poll_response", new=AsyncMock(return_value="ack")) as compose,
            patch("app.team_poll.interaction_handler.get_session", new=AsyncMock(return_value=session)),
            patch("app.team_poll.interaction_handler.finalize_poll_if_ready", new=AsyncMock()) as finalize,
        ):
            handled = await handle_team_poll_dm(
                db,
                session=session,
                text="skip",
                slack_client=slack_client,
                channel="D123",
            )

        self.assertTrue(handled)
        mark_skip.assert_awaited_once_with(db, session_id="sid-1", project_id="proj-1")
        compose.assert_awaited()
        compose_call = compose.await_args_list[0]
        self.assertEqual(compose_call.kwargs.get("action"), "owner_skip_success")
        self.assertEqual(compose_call.kwargs.get("fallback_text"), "Your response was skipped for this poll.")
        slack_client.chat_postMessage.assert_awaited_once()
        finalize.assert_awaited_once()

    def test_integration_router_team_poll_terminal_routing_precedes_resolution(self):
        router_path = Path(__file__).resolve().parents[1] / "app" / "integrations" / "router.py"
        content = router_path.read_text(encoding="utf-8")
        team_poll_idx = content.find("pending_pre_resolution = await route_pending_dm_interactions(")
        project_resolution_idx = content.find("resolution = await resolve_slack_project(")
        self.assertGreaterEqual(team_poll_idx, 0)
        self.assertGreaterEqual(project_resolution_idx, 0)
        self.assertLess(team_poll_idx, project_resolution_idx)

    def test_integration_router_cadence_terminal_routing_precedes_scope_policy(self):
        router_path = Path(__file__).resolve().parents[1] / "app" / "integrations" / "router.py"
        content = router_path.read_text(encoding="utf-8")
        cadence_idx = content.find("if event_type == \"message\" and is_dm_channel:")
        scope_idx = content.find("promarshal_global_scope_policy_enabled")
        self.assertGreaterEqual(cadence_idx, 0)
        self.assertGreaterEqual(scope_idx, 0)
        self.assertLess(cadence_idx, scope_idx)


if __name__ == "__main__":
    unittest.main()
