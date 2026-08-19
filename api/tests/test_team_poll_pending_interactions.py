import unittest
from unittest.mock import AsyncMock, patch

from app.team_poll import session_store


class TeamPollPendingInteractionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_poll_sessions_creates_pending_interaction_for_member(self):
        repo = AsyncMock()
        repo.create_session = AsyncMock(return_value=True)
        members = [
            {
                "user_id": "u-1",
                "email": "u1@example.com",
                "name": "User One",
                "status": "active",
                "integration_ids": {"slack_user_id": "U111"},
            }
        ]
        owner_member = {
            "user_id": "u-owner",
            "email": "owner@example.com",
            "name": "Owner",
            "status": "active",
            "integration_ids": {"slack_user_id": "UOWNER"},
        }
        with patch.object(session_store, "_repo", return_value=repo), patch(
            "app.cadence.orchestrator.find_active_sessions_by_identity",
            new=AsyncMock(return_value=[]),
        ), patch.object(
            session_store,
            "create_pending_interaction",
            new=AsyncMock(return_value={"interaction_id": "pi-1"}),
        ) as create_pending:
            result = await session_store.create_poll_sessions(
                db=object(),
                project_id="proj-1234-5678",
                project_name="Proj",
                owner_member=owner_member,
                members=members,
                question_text="Is everyone ready?",
                workspace_id="T1",
            )

        self.assertEqual(result.get("created_count"), 1)
        create_pending.assert_awaited_once()
        kwargs = create_pending.await_args.kwargs
        self.assertEqual(kwargs.get("interaction_type"), "team_poll")
        self.assertEqual(kwargs.get("project_id"), "proj-1234-5678")
        self.assertEqual(kwargs.get("external_user_id"), "U111")

    async def test_mark_session_responded_cleans_pending_interaction(self):
        repo = AsyncMock()
        repo.update_session_by_id = AsyncMock(return_value=True)
        with patch.object(session_store, "_repo", return_value=repo), patch.object(
            session_store,
            "delete_pending_interactions_by_context",
            new=AsyncMock(return_value=1),
        ) as delete_pending:
            ok = await session_store.mark_session_responded(
                db=object(),
                session_id="s1",
                response_text="yes",
                project_id="proj-1",
            )
        self.assertTrue(ok)
        delete_pending.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

