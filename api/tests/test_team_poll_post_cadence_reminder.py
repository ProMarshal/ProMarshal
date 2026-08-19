import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.team_poll.post_cadence_reminder import maybe_send_post_cadence_pending_poll_reminder


def _dt(minutes: int) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)


class TeamPollPostCadenceReminderTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_reminder_for_unresolved_pending_poll(self):
        cadence_session = {
            "workspace_id": "T1",
            "external_user_id": "U1",
            "outcome": {"response_status": "responded"},
        }
        interaction = {
            "created_at": _dt(-5),
            "expires_at": _dt(30),
            "context_payload": {"session_id": "tp1", "project_id": "proj-1", "poll_id": "poll-1"},
        }
        poll_session = {
            "status": "awaiting_response",
            "question_text": "Have you submitted your update?",
            "member_name": "Prabhu",
            "project_name": "Demo",
            "owner_user_id": "u-owner",
            "user_id": "u-member",
            "poll_id": "poll-1",
        }
        slack_client = AsyncMock()
        redis_conn = MagicMock()
        redis_conn.set.return_value = True
        with patch(
            "app.team_poll.post_cadence_reminder.list_pending_interactions_by_external_identity",
            new=AsyncMock(return_value=[interaction]),
        ), patch(
            "app.team_poll.post_cadence_reminder.get_team_poll_session",
            new=AsyncMock(return_value=poll_session),
        ), patch(
            "app.team_poll.post_cadence_reminder.get_redis_connection",
            return_value=redis_conn,
        ):
            ok = await maybe_send_post_cadence_pending_poll_reminder(
                db=object(),
                cadence_session=cadence_session,
                slack_user_id="U1",
                slack_client=slack_client,
            )
        self.assertTrue(ok)
        slack_client.send_message.assert_awaited_once()

    async def test_skips_when_cadence_not_responded(self):
        cadence_session = {
            "workspace_id": "T1",
            "external_user_id": "U1",
            "outcome": {"response_status": "no_response_expired"},
        }
        slack_client = AsyncMock()
        with patch(
            "app.team_poll.post_cadence_reminder.list_pending_interactions_by_external_identity",
            new=AsyncMock(return_value=[]),
        ):
            ok = await maybe_send_post_cadence_pending_poll_reminder(
                db=object(),
                cadence_session=cadence_session,
                slack_user_id="U1",
                slack_client=slack_client,
            )
        self.assertFalse(ok)
        slack_client.send_message.assert_not_awaited()

    async def test_skips_when_pending_poll_missing(self):
        cadence_session = {
            "workspace_id": "T1",
            "external_user_id": "U1",
            "outcome": {"response_status": "responded"},
        }
        slack_client = AsyncMock()
        with patch(
            "app.team_poll.post_cadence_reminder.list_pending_interactions_by_external_identity",
            new=AsyncMock(return_value=[]),
        ):
            ok = await maybe_send_post_cadence_pending_poll_reminder(
                db=object(),
                cadence_session=cadence_session,
                slack_user_id="U1",
                slack_client=slack_client,
            )
        self.assertFalse(ok)
        slack_client.send_message.assert_not_awaited()

    async def test_cooldown_gate_blocks_duplicate_send(self):
        cadence_session = {
            "workspace_id": "T1",
            "external_user_id": "U1",
            "outcome": {"response_status": "responded"},
        }
        interaction = {
            "created_at": _dt(-5),
            "expires_at": _dt(30),
            "context_payload": {"session_id": "tp1", "project_id": "proj-1", "poll_id": "poll-1"},
        }
        poll_session = {
            "status": "awaiting_response",
            "question_text": "Have you submitted your update?",
            "member_name": "Prabhu",
            "project_name": "Demo",
            "owner_user_id": "u-owner",
            "user_id": "u-member",
            "poll_id": "poll-1",
        }
        slack_client = AsyncMock()
        redis_conn = MagicMock()
        redis_conn.set.return_value = False
        with patch(
            "app.team_poll.post_cadence_reminder.list_pending_interactions_by_external_identity",
            new=AsyncMock(return_value=[interaction]),
        ), patch(
            "app.team_poll.post_cadence_reminder.get_team_poll_session",
            new=AsyncMock(return_value=poll_session),
        ), patch(
            "app.team_poll.post_cadence_reminder.get_redis_connection",
            return_value=redis_conn,
        ):
            ok = await maybe_send_post_cadence_pending_poll_reminder(
                db=object(),
                cadence_session=cadence_session,
                slack_user_id="U1",
                slack_client=slack_client,
            )
        self.assertFalse(ok)
        slack_client.send_message.assert_not_awaited()

    async def test_redis_unavailable_is_fail_open(self):
        cadence_session = {
            "workspace_id": "T1",
            "external_user_id": "U1",
            "outcome": {"response_status": "responded"},
        }
        interaction = {
            "created_at": _dt(-5),
            "expires_at": _dt(30),
            "context_payload": {"session_id": "tp1", "project_id": "proj-1", "poll_id": "poll-1"},
        }
        poll_session = {
            "status": "awaiting_response",
            "question_text": "Have you submitted your update?",
            "member_name": "Prabhu",
            "project_name": "Demo",
            "owner_user_id": "u-owner",
            "user_id": "u-member",
            "poll_id": "poll-1",
        }
        slack_client = AsyncMock()
        redis_conn = MagicMock()
        redis_conn.set.side_effect = RuntimeError("redis down")
        with patch(
            "app.team_poll.post_cadence_reminder.list_pending_interactions_by_external_identity",
            new=AsyncMock(return_value=[interaction]),
        ), patch(
            "app.team_poll.post_cadence_reminder.get_team_poll_session",
            new=AsyncMock(return_value=poll_session),
        ), patch(
            "app.team_poll.post_cadence_reminder.get_redis_connection",
            return_value=redis_conn,
        ):
            ok = await maybe_send_post_cadence_pending_poll_reminder(
                db=object(),
                cadence_session=cadence_session,
                slack_user_id="U1",
                slack_client=slack_client,
            )
        self.assertTrue(ok)
        slack_client.send_message.assert_awaited_once()

    async def test_multiple_pending_polls_selects_oldest(self):
        cadence_session = {
            "workspace_id": "T1",
            "external_user_id": "U1",
            "outcome": {"response_status": "responded"},
        }
        interactions = [
            {
                "created_at": _dt(-1),
                "expires_at": _dt(30),
                "context_payload": {"session_id": "tp-new", "project_id": "proj-1", "poll_id": "poll-z"},
            },
            {
                "created_at": _dt(-10),
                "expires_at": _dt(30),
                "context_payload": {"session_id": "tp-old", "project_id": "proj-1", "poll_id": "poll-a"},
            },
        ]
        sessions_by_id = {
            "tp-new": {
                "status": "awaiting_response",
                "question_text": "New question?",
                "member_name": "Prabhu",
                "project_name": "Demo",
                "owner_user_id": "u-owner",
                "user_id": "u-member",
                "poll_id": "poll-z",
            },
            "tp-old": {
                "status": "awaiting_response",
                "question_text": "Old question?",
                "member_name": "Prabhu",
                "project_name": "Demo",
                "owner_user_id": "u-owner",
                "user_id": "u-member",
                "poll_id": "poll-a",
            },
        }

        async def _session_side_effect(_db, *, session_id: str, project_id: str = None):
            return sessions_by_id.get(session_id)

        slack_client = AsyncMock()
        redis_conn = MagicMock()
        redis_conn.set.return_value = True
        with patch(
            "app.team_poll.post_cadence_reminder.list_pending_interactions_by_external_identity",
            new=AsyncMock(return_value=interactions),
        ), patch(
            "app.team_poll.post_cadence_reminder.get_team_poll_session",
            new=AsyncMock(side_effect=_session_side_effect),
        ), patch(
            "app.team_poll.post_cadence_reminder.get_redis_connection",
            return_value=redis_conn,
        ):
            ok = await maybe_send_post_cadence_pending_poll_reminder(
                db=object(),
                cadence_session=cadence_session,
                slack_user_id="U1",
                slack_client=slack_client,
            )
        self.assertTrue(ok)
        sent_text = str(slack_client.send_message.await_args.kwargs.get("text") or "")
        self.assertIn("Old question", sent_text)

    async def test_skips_when_remaining_poll_ttl_expired(self):
        cadence_session = {
            "workspace_id": "T1",
            "external_user_id": "U1",
            "outcome": {"response_status": "responded"},
        }
        interaction = {
            "created_at": _dt(-5),
            "expires_at": _dt(-1),
            "context_payload": {"session_id": "tp1", "project_id": "proj-1", "poll_id": "poll-1"},
        }
        poll_session = {
            "status": "awaiting_response",
            "question_text": "Have you submitted your update?",
            "member_name": "Prabhu",
            "project_name": "Demo",
            "owner_user_id": "u-owner",
            "user_id": "u-member",
            "poll_id": "poll-1",
        }
        slack_client = AsyncMock()
        redis_conn = MagicMock()
        redis_conn.set.return_value = True
        with patch(
            "app.team_poll.post_cadence_reminder.list_pending_interactions_by_external_identity",
            new=AsyncMock(return_value=[interaction]),
        ), patch(
            "app.team_poll.post_cadence_reminder.get_team_poll_session",
            new=AsyncMock(return_value=poll_session),
        ), patch(
            "app.team_poll.post_cadence_reminder.get_redis_connection",
            return_value=redis_conn,
        ):
            ok = await maybe_send_post_cadence_pending_poll_reminder(
                db=object(),
                cadence_session=cadence_session,
                slack_user_id="U1",
                slack_client=slack_client,
            )
        self.assertFalse(ok)
        slack_client.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
