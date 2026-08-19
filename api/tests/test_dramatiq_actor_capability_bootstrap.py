import importlib
import unittest
from unittest.mock import AsyncMock, patch


actors_module = importlib.import_module("app.workers.dramatiq_actors")


def _callable_actor(actor_obj):
    return getattr(actor_obj, "fn", actor_obj)


class DramatiqActorCapabilityBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_cadence_dm_actor_bootstraps_capabilities(self):
        with patch.object(
            actors_module,
            "_ensure_capability_bundles_ready",
        ) as ensure_caps_mock, patch.object(
            actors_module,
            "_ensure_dramatiq_mongo_ready",
            new=AsyncMock(),
        ), patch(
            "app.cadence.queued.handle_cadence_dm_queued",
            new=AsyncMock(),
        ) as queued_mock:
            await _callable_actor(actors_module.handle_cadence_dm_actor)(
                session_id="session-1",
                project_id="proj-1",
                workspace_id="T1",
                channel="D1",
                text="update",
                actor_slack_user_id="U1",
            )

        ensure_caps_mock.assert_called_once_with()
        queued_mock.assert_awaited_once()

    async def test_handle_modal_submission_actor_bootstraps_capabilities(self):
        with patch.object(
            actors_module,
            "_ensure_capability_bundles_ready",
        ) as ensure_caps_mock, patch.object(
            actors_module,
            "_ensure_dramatiq_mongo_ready",
            new=AsyncMock(),
        ), patch(
            "app.cadence.interaction_handler.handle_modal_submission_direct",
            new=AsyncMock(),
        ) as modal_mock:
            await _callable_actor(actors_module.handle_modal_submission_actor)(payload={"view": {}})

        ensure_caps_mock.assert_called_once_with()
        modal_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
