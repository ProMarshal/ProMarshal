import unittest
from unittest.mock import AsyncMock, patch

from app.action_items import reminders
from app.action_items import slack_interaction_handler as ai_interactions
from app.integrations.slack import assignment_notifications
from app.integrations.slack import interactions_router


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, length=None):
        return list(self._docs[: length or len(self._docs)])


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs

    def find(self, *_args, **_kwargs):
        return _FakeCursor(self._docs)


class _FakeBrainCollectionWithDB:
    def __init__(self, database):
        self.database = database


class _FakeProjects:
    def __init__(self, project):
        self._project = project

    async def find_one(self, query):
        if query.get("project_id") == self._project.get("project_id"):
            return dict(self._project)
        return None


class _FakeDB:
    def __init__(self, project):
        self.projects = _FakeProjects(project)


class ActionItemDigestInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_action_item_digest_sends_interactive_blocks(self):
        project = {
            "project_id": "proj-1234-5678",
            "integrations": {"slack": {"access_token": "encrypted-token"}},
            "members": [
                {"user_id": "u-1", "name": "User One", "status": "active"},
            ],
        }
        fake_db = _FakeDB(project)
        fake_collection = _FakeCollection(
            [
                {
                    "entity_type": "action_item",
                    "status": "open",
                    "action_item_id": "ai-1",
                    "display_key": "ACTION-1",
                    "title": "Send update to ops",
                    "owner_user_id": "u-1",
                    "due_type": "today",
                }
            ]
        )

        fake_slack_client = AsyncMock()
        fake_slack_client.send_message = AsyncMock(return_value=True)

        with patch.object(reminders.settings, "action_items_enabled", True), patch.object(
            reminders.settings,
            "action_items_digest_enabled",
            True,
        ), patch(
            "app.action_items.reminders.get_brain_collection",
            return_value=fake_collection,
        ), patch(
            "app.core.security.encryption_service.decrypt",
            return_value="xoxb-token",
        ), patch(
            "app.integrations.slack.client.SlackClient",
            return_value=fake_slack_client,
        ), patch(
            "app.cadence.slack_identity.resolve_slack_user_id_for_member",
            new=AsyncMock(return_value="U111"),
        ):
            result = await reminders.execute_action_item_digest(
                db=fake_db,
                schedule={"project_id": "proj-1234-5678"},
            )

        self.assertEqual(result.get("delivered"), 1)
        fake_slack_client.send_message.assert_awaited_once()
        args = fake_slack_client.send_message.await_args.args
        kwargs = fake_slack_client.send_message.await_args.kwargs
        self.assertEqual(args[0], "U111")
        blocks = kwargs.get("blocks") or []
        action_ids = []
        for block in blocks:
            if block.get("type") != "actions":
                continue
            for element in block.get("elements") or []:
                action_ids.append(str(element.get("action_id") or ""))
        section_texts = [
            str(((block.get("text") or {}).get("text") or ""))
            for block in blocks
            if block.get("type") == "section"
        ]
        self.assertTrue(
            any("Project:" in text and "proj-1234-5678" in text for text in section_texts)
        )
        self.assertIn(ai_interactions.ACTION_ITEM_DIGEST_DONE_ACTION_ID, action_ids)
        self.assertIn(ai_interactions.ACTION_ITEM_DIGEST_CANCEL_ACTION_ID, action_ids)

    async def test_slack_interactions_router_dispatches_action_item_digest_action(self):
        payload = {
            "type": "block_actions",
            "actions": [{"action_id": ai_interactions.ACTION_ITEM_DIGEST_DONE_ACTION_ID, "value": "{}"}],
        }
        with patch(
            "app.integrations.slack.interactions_router.handle_action_item_digest_block_action",
            new=AsyncMock(),
        ) as handler_mock:
            response = await interactions_router.process_slack_interaction_payload(payload)

        self.assertEqual(response.status_code, 200)
        handler_mock.assert_awaited_once()

    async def test_action_item_digest_handler_closes_item_and_posts_confirmation(self):
        payload = {
            "type": "block_actions",
            "actions": [
                {
                    "action_id": ai_interactions.ACTION_ITEM_DIGEST_DONE_ACTION_ID,
                    "value": '{"project_id":"proj-1234-5678","action_item_id":"ai-1"}',
                }
            ],
            "user": {"id": "U111"},
            "team": {"id": "T111"},
            "channel": {"id": "D111"},
            "response_url": "https://example.invalid/response",
        }
        project = {"project_id": "proj-1234-5678"}
        member = {"user_id": "u-1"}
        with patch(
            "app.action_items.slack_interaction_handler.resolve_project_member_for_slack_actor",
            new=AsyncMock(return_value=(project, member, None)),
        ), patch(
            "app.action_items.slack_interaction_handler.ActionItemService.close",
            new=AsyncMock(return_value={"display_key": "ACTION-1"}),
        ) as close_mock, patch(
            "app.action_items.slack_interaction_handler._post_ephemeral_response",
            new=AsyncMock(),
        ) as post_mock:
            await ai_interactions.handle_action_item_digest_block_action(payload)

        close_mock.assert_awaited_once()
        post_mock.assert_awaited_once()
        self.assertIn("Updated ACTION-1 as done", post_mock.await_args.args[1])

    async def test_slack_interactions_router_dispatches_work_item_assignment_action(self):
        payload = {
            "type": "block_actions",
            "actions": [{"action_id": assignment_notifications.WORK_ITEM_ASSIGNMENT_OPEN_MODAL_ACTION_ID, "value": "{}"}],
        }
        with patch(
            "app.integrations.slack.interactions_router.handle_work_item_assignment_block_action",
            new=AsyncMock(return_value=True),
        ) as handler_mock:
            response = await interactions_router.process_slack_interaction_payload(payload)

        self.assertEqual(response.status_code, 200)
        handler_mock.assert_awaited_once()

    async def test_slack_interactions_router_dispatches_work_item_assignment_modal_submit(self):
        payload = {
            "type": "view_submission",
            "view": {"callback_id": assignment_notifications.WORK_ITEM_ASSIGNMENT_UPDATE_CALLBACK_ID},
        }
        with patch(
            "app.integrations.slack.interactions_router.handle_work_item_assignment_modal_submission",
            new=AsyncMock(return_value=interactions_router.JSONResponse(content={"ok": True})),
        ) as handler_mock:
            response = await interactions_router.process_slack_interaction_payload(payload)

        self.assertEqual(response.status_code, 200)
        handler_mock.assert_awaited_once()

    async def test_work_item_assignment_block_action_posts_feedback_when_transitions_unavailable(self):
        payload = {
            "type": "block_actions",
            "actions": [
                {
                    "action_id": assignment_notifications.WORK_ITEM_ASSIGNMENT_OPEN_MODAL_ACTION_ID,
                    "value": '{"project_id":"proj-1234-5678","task_id":"ABC-1"}',
                }
            ],
            "user": {"id": "U111"},
            "team": {"id": "T111"},
            "trigger_id": "trigger-1",
            "response_url": "https://example.invalid/response",
        }
        project = {"project_id": "proj-1234-5678", "integrations": {"jira": {}, "slack": {"access_token": "x"}}}
        member = {"user_id": "u-1"}
        slack_client = AsyncMock()
        task = {"external_id": "ABC-1", "provider": "jira", "status": "todo"}

        with patch(
            "app.integrations.slack.assignment_notifications.get_brain_collection",
            return_value=_FakeBrainCollectionWithDB(database=object()),
        ), patch(
            "app.integrations.slack.assignment_notifications.resolve_project_member_for_slack_actor",
            new=AsyncMock(return_value=(project, member, slack_client)),
        ), patch(
            "app.integrations.slack.assignment_notifications.load_existing_work_item",
            new=AsyncMock(return_value=task),
        ), patch(
            "app.integrations.slack.assignment_notifications.fetch_available_transitions",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.integrations.slack.assignment_notifications._post_ephemeral_response",
            new=AsyncMock(),
        ) as post_mock:
            opened = await assignment_notifications.handle_work_item_assignment_block_action(payload)

        self.assertFalse(opened)
        post_mock.assert_awaited_once()

    def test_resolve_work_item_assignee_identity_prefers_jira_account_id_mapping(self):
        project = {
            "members": [
                {
                    "user_id": "u-1",
                    "status": "active",
                    "integration_ids": {"jira_account_id": "acc-1"},
                    "email": "member@example.com",
                    "name": "Member One",
                }
            ]
        }
        task = {
            "provider": "jira",
            "assignee_account_id": "acc-1",
            "assignee_email": "different@example.com",
            "assignee_name": "Different Name",
        }

        user_id, identity, label = assignment_notifications._resolve_work_item_assignee_identity(project, task)

        self.assertEqual(user_id, "u-1")
        self.assertEqual(identity, "acc-1")
        self.assertEqual(label, "acc-1")

    def test_work_item_assignment_modal_uses_transition_ids(self):
        modal = assignment_notifications.build_work_item_assignment_modal(
            task={"external_id": "ABC-1", "title": "Title", "status": "todo"},
            project_id="proj-1234-5678",
            actor_user_id="u-1",
            actor_slack_user_id="U111",
            transitions=[
                {"id": "31", "name": "In Progress"},
                {"id": "41", "name": "Done"},
            ],
        )

        element = next(
            block["element"]
            for block in modal["blocks"]
            if block.get("block_id") == "status_input"
        )
        options = element.get("options") or []
        self.assertEqual(options[0]["value"], "31")
        self.assertEqual(options[1]["value"], "41")
        metadata = assignment_notifications._safe_parse_json_dict(modal.get("private_metadata"))
        self.assertEqual(metadata.get("transition_map"), {"31": "In Progress", "41": "Done"})

if __name__ == "__main__":
    unittest.main()
