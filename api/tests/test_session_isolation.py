import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.cadence import formatters as cadence_formatters
from app.cadence import interaction_handler
from app.cadence import session_store
from app.sessions.repository import SessionRepository


class _NoFallbackDb:
    def __getitem__(self, key):
        raise AssertionError(f"Legacy collection access should not happen in project_only mode: {key}")


class SessionIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_mismatch_rejected_for_tampered_payload(self):
        session = {"project_id": "proj-A", "user_id": "user-1"}
        db = MagicMock()
        with (
            patch.object(
                interaction_handler.settings,
                "session_strict_project_binding_enabled",
                True,
            ),
            patch.object(
                interaction_handler.settings,
                "session_actor_authorization_enabled",
                False,
            ),
        ):
            allowed = await interaction_handler._enforce_interaction_isolation(
                db=db,
                session=session,
                session_id="sid-1",
                action_label="modal_submit",
                actor_slack_user_id="U123",
                payload_project_id="proj-B",
            )
        self.assertFalse(allowed)

    async def test_actor_mismatch_rejected(self):
        session = {"project_id": "proj-A", "user_id": "user-1"}
        db = MagicMock()
        with (
            patch.object(
                interaction_handler.settings,
                "session_strict_project_binding_enabled",
                False,
            ),
            patch.object(
                interaction_handler.settings,
                "session_actor_authorization_enabled",
                True,
            ),
            patch(
                "app.cadence.interaction_handler._is_actor_authorized_for_session",
                new=AsyncMock(return_value=(False, "actor_mismatch")),
            ),
        ):
            allowed = await interaction_handler._enforce_interaction_isolation(
                db=db,
                session=session,
                session_id="sid-1",
                action_label="open_update_modal",
                actor_slack_user_id="U999",
                payload_project_id="proj-A",
            )
        self.assertFalse(allowed)

    async def test_legacy_payload_fallback_uses_session_index_resolution(self):
        session = {"project_id": "proj-A", "user_id": "user-1"}
        db = MagicMock()
        resolver = AsyncMock(return_value="proj-A")
        with (
            patch.object(
                interaction_handler.settings,
                "session_strict_project_binding_enabled",
                True,
            ),
            patch.object(
                interaction_handler.settings,
                "session_actor_authorization_enabled",
                False,
            ),
            patch(
                "app.cadence.interaction_handler._resolve_project_id_from_session_index",
                new=resolver,
            ),
        ):
            allowed = await interaction_handler._enforce_interaction_isolation(
                db=db,
                session=session,
                session_id="sid-legacy",
                action_label="modal_submit",
                actor_slack_user_id="U123",
                payload_project_id="",
            )
        self.assertTrue(allowed)
        resolver.assert_awaited_once_with("sid-legacy")

    async def test_session_repository_active_query_is_project_scoped(self):
        repo = SessionRepository(db=MagicMock())
        index_collection = SimpleNamespace(find_one=AsyncMock(return_value=None))
        with patch.object(repo, "_session_index_collection", return_value=index_collection):
            doc = await repo.find_active_session(
                entity_type="cadence_session",
                source="cadence",
                user_id="user-1",
                project_id="proj-1",
            )
        self.assertIsNone(doc)
        called_query = index_collection.find_one.await_args.args[0]
        self.assertEqual(called_query["user_id"], "user-1")
        self.assertEqual(called_query["project_id"], "proj-1")
        self.assertEqual(called_query["source"], "cadence")
        self.assertEqual(called_query["entity_type"], "cadence_session")

    async def test_has_active_session_does_not_query_legacy_collection(self):
        fake_repo = SimpleNamespace(find_active_session=AsyncMock(return_value=None))
        with (
            patch(
                "app.cadence.session_store._repo",
                return_value=fake_repo,
            ),
        ):
            has_active = await session_store.has_active_session(
                _NoFallbackDb(),
                user_id="user-1",
                project_id="proj-1",
                scope="project",
            )
        self.assertFalse(has_active)


class SessionIsolationFormattingTests(unittest.TestCase):
    def test_interaction_payload_metadata_contains_project_id(self):
        task = {
            "title": "Task",
            "external_id": "TASK-1",
            "status": "todo",
            "priority": "high",
        }
        msg = cadence_formatters.format_paid_tier_message(
            task=task,
            task_index=0,
            total_tasks=1,
            session_id="sid-1",
            project_id="proj-1234-5678",
        )
        button = msg["blocks"][-1]["elements"][0]
        payload = json.loads(button["value"])
        self.assertEqual(payload.get("project_id"), "proj-1234-5678")

        modal = cadence_formatters.format_update_task_modal(
            session_id="sid-1",
            task_index=0,
            task=task,
            total_tasks=1,
            project_id="proj-1234-5678",
        )
        metadata = json.loads(modal["private_metadata"])
        self.assertEqual(metadata.get("project_id"), "proj-1234-5678")

    def test_dm_routing_no_cross_project_helper_reference(self):
        router_path = Path(__file__).resolve().parents[1] / "app" / "integrations" / "router.py"
        content = router_path.read_text(encoding="utf-8")
        self.assertIn("find_active_sessions_by_identity(", content)
        self.assertNotIn("find_active_session_any_project", content)

    def test_dm_routing_precedes_scope_policy_block(self):
        router_path = Path(__file__).resolve().parents[1] / "app" / "integrations" / "router.py"
        content = router_path.read_text(encoding="utf-8")
        cadence_idx = content.find("if event_type == \"message\" and is_dm_channel:")
        scope_idx = content.find("promarshal_global_scope_policy_enabled")
        self.assertGreaterEqual(cadence_idx, 0)
        self.assertGreaterEqual(scope_idx, 0)
        self.assertLess(cadence_idx, scope_idx)


if __name__ == "__main__":
    unittest.main()
