import json
import unittest
from unittest.mock import AsyncMock, patch

from app.integrations import router


class SlackRouterResponseGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        router._seen_slack_response_keys.clear()
        router._seen_slack_semantic_event_keys.clear()

    def test_response_slot_memory_dedupe(self):
        with patch("app.integrations.router.get_redis_connection", return_value=None):
            key = router._build_slack_response_gate_key(
                kind="ack",
                team_id="T1",
                channel_id="C1",
                user_id="U1",
                thread_ts=None,
                event_ts="1710000.1",
                normalized_text="hello there",
            )
            self.assertTrue(router._try_acquire_slack_response_slot(key))
            self.assertFalse(router._try_acquire_slack_response_slot(key))

    def test_dm_ack_gate_key_is_event_scoped(self):
        key1 = router._build_slack_response_gate_key(
            kind="ack",
            team_id="T1",
            channel_id="D1",
            user_id="U1",
            thread_ts=None,
            event_ts="1710000.1",
            normalized_text="create action item",
        )
        key2 = router._build_slack_response_gate_key(
            kind="ack",
            team_id="T1",
            channel_id="D1",
            user_id="U1",
            thread_ts=None,
            event_ts="1710000.2",
            normalized_text="create action item",
        )
        self.assertNotEqual(key1, key2)
        self.assertIn("evt:1710000.1", key1)
        self.assertIn("evt:1710000.2", key2)

    def test_thread_ack_gate_key_is_event_scoped(self):
        key1 = router._build_slack_response_gate_key(
            kind="ack",
            team_id="T1",
            channel_id="C1",
            user_id="U1",
            thread_ts="1709999.9",
            event_ts="1710000.1",
            normalized_text="list open action items",
        )
        key2 = router._build_slack_response_gate_key(
            kind="ack",
            team_id="T1",
            channel_id="C1",
            user_id="U1",
            thread_ts="1709999.9",
            event_ts="1710000.2",
            normalized_text="list open action items",
        )
        self.assertNotEqual(key1, key2)
        self.assertIn("thread:1709999.9|evt:1710000.1", key1)
        self.assertIn("thread:1709999.9|evt:1710000.2", key2)

    def test_failure_reply_policy(self):
        self.assertFalse(router._should_send_cortex_failure_reply("ingress_not_accepted"))
        self.assertTrue(router._should_send_cortex_failure_reply("session_bootstrap_failed"))

    def test_build_cortex_failure_reply_specific_for_session_bootstrap(self):
        text = router._build_cortex_failure_reply("session_bootstrap_failed")
        self.assertIn("session", text.lower())

    def test_context_footer_gate_requires_tool_call(self):
        self.assertFalse(
            router._should_append_cortex_context_footer(
                is_dm_channel=True,
                response_text="Here is the status.",
                message_intent="project_related",
                used_tool_call=False,
            )
        )
        self.assertTrue(
            router._should_append_cortex_context_footer(
                is_dm_channel=True,
                response_text="Here are your tasks.",
                message_intent="project_related",
                used_tool_call=True,
            )
        )

    def test_context_footer_gate_skips_greeting_and_non_dm(self):
        self.assertFalse(
            router._should_append_cortex_context_footer(
                is_dm_channel=True,
                response_text="Hello!",
                message_intent="greeting_only",
                used_tool_call=True,
            )
        )
        self.assertFalse(
            router._should_append_cortex_context_footer(
                is_dm_channel=False,
                response_text="Here are your tasks.",
                message_intent="project_related",
                used_tool_call=True,
            )
        )

    def test_semantic_event_memory_dedupe_uses_client_msg_id_anchor(self):
        with patch("app.integrations.router.get_redis_connection", return_value=None):
            key = router._build_slack_semantic_event_key(
                team_id="T1",
                channel_id="D1",
                user_id="U1",
                event_type="message",
                event_ts="1710000.1",
                thread_ts=None,
                client_msg_id="abc-123",
                text="list my tasks",
            )
            self.assertFalse(router._is_duplicate_slack_semantic_event(key))
            self.assertTrue(router._is_duplicate_slack_semantic_event(key))

    def test_semantic_event_key_falls_back_to_event_ts_when_client_msg_id_absent(self):
        key = router._build_slack_semantic_event_key(
            team_id="T1",
            channel_id="D1",
            user_id="U1",
            event_type="message",
            event_ts="1710000.2",
            thread_ts="1710000.0",
            client_msg_id="",
            text="list my tasks",
        )
        self.assertIn("ts:1710000.2", key)


class SlackRouterAmbiguousPromptDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_ambiguous_prompt_prefers_resolver_token_client(self):
        resolver_client = AsyncMock()
        with patch(
            "app.integrations.router._build_slack_client_from_access_token",
            return_value=resolver_client,
        ), patch(
            "app.integrations.router._get_workspace_slack_client",
            new=AsyncMock(return_value=None),
        ):
            sent = await router._send_slack_dm_text_with_workspace_fallback(
                db=object(),
                team_id="T1",
                channel="D1",
                text="choose project",
                thread_ts="171",
                reason_label="ambiguous_project_prompt",
                fallback_access_token="xoxb-token",
                candidate_projects=[{"project_id": "proj-1", "project_name": "A"}],
            )
        self.assertTrue(sent)
        resolver_client.chat_postMessage.assert_awaited_once()

    async def test_ambiguous_prompt_falls_back_to_workspace_client(self):
        failing_resolver_client = AsyncMock()
        failing_resolver_client.chat_postMessage = AsyncMock(side_effect=Exception("resolver_send_failed"))
        workspace_client = AsyncMock()
        with patch(
            "app.integrations.router._build_slack_client_from_access_token",
            return_value=failing_resolver_client,
        ), patch(
            "app.integrations.router._get_workspace_slack_client",
            new=AsyncMock(return_value=workspace_client),
        ):
            sent = await router._send_slack_dm_text_with_workspace_fallback(
                db=object(),
                team_id="T1",
                channel="D1",
                text="choose project",
                reason_label="ambiguous_project_prompt",
                fallback_access_token="xoxb-token",
            )
        self.assertTrue(sent)
        failing_resolver_client.chat_postMessage.assert_awaited_once()
        workspace_client.chat_postMessage.assert_awaited_once()

    async def test_ambiguous_prompt_returns_false_when_all_send_attempts_fail(self):
        failing_resolver_client = AsyncMock()
        failing_resolver_client.chat_postMessage = AsyncMock(side_effect=Exception("resolver_send_failed"))
        failing_workspace_client = AsyncMock()
        failing_workspace_client.chat_postMessage = AsyncMock(side_effect=Exception("workspace_send_failed"))
        with patch(
            "app.integrations.router._build_slack_client_from_access_token",
            return_value=failing_resolver_client,
        ), patch(
            "app.integrations.router._get_workspace_slack_client",
            new=AsyncMock(return_value=failing_workspace_client),
        ):
            sent = await router._send_slack_dm_text_with_workspace_fallback(
                db=object(),
                team_id="T1",
                channel="D1",
                text="choose project",
                reason_label="ambiguous_project_prompt",
                fallback_access_token="xoxb-token",
            )
        self.assertFalse(sent)


class SlackAppHomeOpenedTests(unittest.IsolatedAsyncioTestCase):
    async def test_app_home_opened_returns_ok_without_sending_dm(self):
        payload = {
            "type": "event_callback",
            "event_id": "EvHome1",
            "team_id": "T1",
            "event": {
                "type": "app_home_opened",
                "user": "U1",
                "channel": "D1",
            },
        }

        response = await router.slack_events(raw_body=json.dumps(payload).encode("utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b'{"ok":true}')


class SlackProjectContextMessageFormattingTests(unittest.TestCase):
    def test_ambiguous_message_hides_project_ids(self):
        candidate_projects = [
            {"project_id": "proj-1341-4302", "project_name": "Test1"},
            {"project_id": "proj-1699-5169", "project_name": "proj-1699-5169"},
        ]
        text = router._build_ambiguous_project_message(candidate_projects=candidate_projects)
        self.assertIn("1. Test1", text)
        self.assertIn("2. Project 2", text)
        self.assertNotIn("proj-1341-4302", text)
        self.assertNotIn("proj-1699-5169", text)

    def test_switch_list_message_hides_project_ids(self):
        candidate_projects = [
            {"project_id": "proj-1341-4302", "project_name": "Test1"},
            {"project_id": "proj-1699-5169", "project_name": "proj-1699-5169"},
        ]
        text = router._build_project_switch_list_message(
            candidate_projects=candidate_projects,
            active_project_id="proj-1341-4302",
        )
        self.assertIn("1. Test1 [active]", text)
        self.assertIn("2. Project 2", text)
        self.assertNotIn("proj-1341-4302", text)
        self.assertNotIn("proj-1699-5169", text)

    def test_project_set_confirmation_mentions_switch_command(self):
        text = router._build_project_set_confirmation_message("Test1")
        self.assertIn("Active project set to Test1.", text)
        self.assertIn("switch project", text)

    def test_exact_switch_project_command_match(self):
        self.assertTrue(router._is_exact_switch_project_command("switch project"))
        self.assertTrue(router._is_exact_switch_project_command("  switch   project "))
        self.assertFalse(router._is_exact_switch_project_command("switch to project"))


class SlackProjectSwitchModalPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_clarification_check_detects_existing(self):
        with patch(
            "app.integrations.router.list_pending_interactions_by_external_identity",
            new=AsyncMock(return_value=[{"interaction_id": "pi-1"}]),
        ):
            blocked = await router._has_non_terminal_pending_clarification_for_identity(
                team_id="T1",
                slack_user_id="U1",
            )
        self.assertTrue(blocked)

    async def test_switch_modal_button_prompt_sends_blocks(self):
        fake_client = AsyncMock()
        with patch(
            "app.integrations.router._get_workspace_slack_client",
            new=AsyncMock(return_value=fake_client),
        ):
            sent = await router._send_switch_project_modal_button_prompt(
                db=object(),
                team_id="T1",
                channel="D1",
                thread_ts="123.45",
            )
        self.assertTrue(sent)
        fake_client.chat_postMessage.assert_awaited_once()
        payload = fake_client.chat_postMessage.await_args.kwargs
        self.assertIn("blocks", payload)
        self.assertEqual(payload["blocks"][1]["elements"][0]["action_id"], "project_switch_open_modal")


if __name__ == "__main__":
    unittest.main()
