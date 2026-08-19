import importlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


queued_module = importlib.import_module("app.cadence.queued")


class _FakeProjectsCollection:
    def __init__(self, encrypted_token: str) -> None:
        self._encrypted_token = encrypted_token

    async def find_one(self, *_args, **_kwargs):
        return {
            "integrations": {
                "slack": {
                    "access_token": self._encrypted_token,
                }
            }
        }


class _FakeDb:
    def __init__(self, encrypted_token: str) -> None:
        self.projects = _FakeProjectsCollection(encrypted_token)


class CadenceQueuedSlackClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_dm_uses_encrypted_token_for_slack_client(self):
        session_id = "session-1"
        encrypted_token = "encrypted-token"
        fake_db = _FakeDb(encrypted_token=encrypted_token)
        fake_session = {"session_id": session_id, "project_id": "proj-1"}
        fake_slack_client = MagicMock()

        with patch.object(queued_module, "get_database", return_value=fake_db), patch.object(
            queued_module,
            "get_session",
            new=AsyncMock(return_value=fake_session),
        ), patch.object(
            queued_module.slack_bot_service,
            "get_slack_client",
            return_value=fake_slack_client,
        ) as get_slack_client_mock, patch.object(
            queued_module,
            "handle_cadence_dm",
            new=AsyncMock(return_value=True),
        ) as handle_cadence_dm_mock, patch.object(
            queued_module.settings,
            "cadence_dm_lease_enabled",
            False,
        ):
            handled = await queued_module.handle_cadence_dm_queued(
                session_id=session_id,
                project_id="proj-1",
                workspace_id="T1",
                channel="D1",
                text="still in progress",
                actor_slack_user_id="U1",
            )

        self.assertTrue(handled)
        get_slack_client_mock.assert_called_once_with(encrypted_token)
        handle_cadence_dm_mock.assert_awaited_once()

    async def test_queued_dm_returns_false_when_slack_client_build_fails(self):
        session_id = "session-2"
        encrypted_token = "encrypted-token"
        fake_db = _FakeDb(encrypted_token=encrypted_token)
        fake_session = {"session_id": session_id, "project_id": "proj-2"}

        with patch.object(queued_module, "get_database", return_value=fake_db), patch.object(
            queued_module,
            "get_session",
            new=AsyncMock(return_value=fake_session),
        ), patch.object(
            queued_module.slack_bot_service,
            "get_slack_client",
            side_effect=ValueError("invalid token"),
        ), patch.object(
            queued_module,
            "handle_cadence_dm",
            new=AsyncMock(return_value=True),
        ) as handle_cadence_dm_mock, patch.object(
            queued_module.settings,
            "cadence_dm_lease_enabled",
            False,
        ):
            handled = await queued_module.handle_cadence_dm_queued(
                session_id=session_id,
                project_id="proj-2",
                workspace_id="T2",
                channel="D2",
                text="update",
                actor_slack_user_id="U2",
            )

        self.assertFalse(handled)
        handle_cadence_dm_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
