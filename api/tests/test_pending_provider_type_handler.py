import unittest
from unittest.mock import AsyncMock, patch

from app.tasks.pending_provider_type_handler import handle_pending_provider_type_clarification


class PendingProviderTypeHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_yes_uses_suggested_value_and_resumes(self):
        pending = {
            "interaction_id": "pi-1",
            "context_payload": {
                "tool_name": "jira_create_work_item",
                "args": {"title": "UI Testing", "assignee": "me", "provider_type": "Task"},
                "requested_value": "Task",
                "suggested_value": "Bug",
                "allowed_values": ["Bug"],
            },
        }
        with patch(
            "app.tasks.pending_provider_type_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.tasks.pending_provider_type_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ) as resolve_pending:
            result = await handle_pending_provider_type_clarification(
                project={"project_id": "proj-1"},
                actor_user_id="u-1",
                text="yes",
            )

        self.assertFalse(result.get("handled"))
        self.assertTrue(bool(result.get("resume_to_cortex")))
        self.assertIn("create a Bug", str(result.get("resume_message_text") or ""))
        resolve_pending.assert_awaited_once()

    async def test_explicit_allowed_value_resumes(self):
        pending = {
            "interaction_id": "pi-2",
            "context_payload": {
                "tool_name": "jira_create_work_item",
                "args": {"title": "UI Testing", "provider_type": "Task"},
                "requested_value": "Task",
                "suggested_value": "Bug",
                "allowed_values": ["Bug", "Story"],
            },
        }
        with patch(
            "app.tasks.pending_provider_type_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.tasks.pending_provider_type_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ):
            result = await handle_pending_provider_type_clarification(
                project={"project_id": "proj-1"},
                actor_user_id="u-1",
                text="Story",
            )

        self.assertFalse(result.get("handled"))
        self.assertTrue(bool(result.get("resume_to_cortex")))
        self.assertIn("create a Story", str(result.get("resume_message_text") or ""))

    async def test_cancel_resolves_pending_and_stops(self):
        pending = {
            "interaction_id": "pi-3",
            "context_payload": {
                "args": {"title": "UI Testing", "provider_type": "Task"},
                "allowed_values": ["Bug"],
            },
        }
        with patch(
            "app.tasks.pending_provider_type_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.tasks.pending_provider_type_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ) as resolve_pending:
            result = await handle_pending_provider_type_clarification(
                project={"project_id": "proj-1"},
                actor_user_id="u-1",
                text="cancel",
            )

        self.assertTrue(result.get("handled"))
        self.assertIn("Cancelled", str(result.get("response_text") or ""))
        resolve_pending.assert_awaited_once()

    async def test_unknown_reply_returns_guidance(self):
        pending = {
            "interaction_id": "pi-4",
            "context_payload": {
                "args": {"title": "UI Testing", "provider_type": "Task"},
                "requested_value": "Task",
                "suggested_value": "Bug",
                "allowed_values": ["Bug"],
            },
        }
        with patch(
            "app.tasks.pending_provider_type_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ):
            result = await handle_pending_provider_type_clarification(
                project={"project_id": "proj-1"},
                actor_user_id="u-1",
                text="maybe",
            )

        self.assertTrue(result.get("handled"))
        self.assertIn("isn't allowed", str(result.get("response_text") or ""))


if __name__ == "__main__":
    unittest.main()

