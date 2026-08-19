import importlib
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


tools_module = importlib.import_module("app.cortex.tools.providers.action_item_tools")


class CortexActionItemToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_tool_returns_feature_disabled_when_flag_off(self):
        tool = tools_module.ActionItemCreateTool()
        with patch.object(tools_module.settings, "action_items_enabled", False):
            result = await tool.execute_with_context(
                {"title": "Follow up", "operation_id": "op_test_123"},
                {"project_id": "proj-1234-5678", "user_id": "u-1"},
            )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_code"), "feature_disabled")

    async def test_create_tool_calls_service_and_returns_committed_action(self):
        tool = tools_module.ActionItemCreateTool()
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "email": "owner@example.com", "status": "active"}],
        }
        created = {
            "entity_type": "action_item",
            "action_item_id": "ai-1",
            "display_key": "ACTION-1",
            "project_id": "proj-1234-5678",
            "title": "Follow up with ops",
            "status": "open",
            "owner_user_id": "u-owner",
            "created_by_user_id": "u-owner",
            "source": "manual",
            "related_to": "Ops topic",
            "related_type": "topic",
            "related_id": None,
            "due_type": "no_due_date",
            "due_date": None,
            "needs_followup": False,
            "needs_followup_set_at": None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "closed_at": None,
            "closed_by_user_id": None,
        }
        create_mock = AsyncMock(return_value=created)
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "create",
            new=create_mock,
        ):
            result = await tool.execute_with_context(
                {
                    "title": "Follow up with ops",
                    "owner_user_id": "u-owner",
                    "related_to": "Ops topic",
                    "operation_id": "op_test_456",
                },
                {"project_id": "proj-1234-5678", "user_id": "u-owner"},
            )

        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("tool_name"), "action_item_create")
        self.assertIn("committed_action", result)
        create_mock.assert_awaited_once()

    async def test_create_tool_resolves_owner_me_to_actor_user_id(self):
        tool = tools_module.ActionItemCreateTool()
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "email": "owner@example.com", "status": "active"}],
        }
        create_mock = AsyncMock(
            return_value={
                "entity_type": "action_item",
                "action_item_id": "ai-11",
                "display_key": "ACTION-11",
                "project_id": "proj-1234-5678",
                "title": "Follow up with ops",
                "status": "open",
                "owner_user_id": "u-owner",
                "created_by_user_id": "u-owner",
                "source": "manual",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "create",
            new=create_mock,
        ):
            result = await tool.execute_with_context(
                {
                    "title": "Follow up with ops",
                    "owner_user_id": "me",
                    "operation_id": "op_test_457",
                },
                {"project_id": "proj-1234-5678", "user_id": "u-owner"},
            )

        self.assertTrue(result.get("ok"))
        create_mock.assert_awaited_once()
        args = create_mock.await_args.args
        self.assertEqual(args[1].get("owner_user_id"), "u-owner")

    async def test_create_tool_resolves_noisy_owner_hint_with_assign_to_me_phrase(self):
        tool = tools_module.ActionItemCreateTool()
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "email": "owner@example.com", "status": "active"}],
        }
        create_mock = AsyncMock(
            return_value={
                "entity_type": "action_item",
                "action_item_id": "ai-12",
                "display_key": "ACTION-12",
                "project_id": "proj-1234-5678",
                "title": "Send meeting invite",
                "status": "open",
                "owner_user_id": "u-owner",
                "created_by_user_id": "u-owner",
                "source": "manual",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "create",
            new=create_mock,
        ):
            result = await tool.execute_with_context(
                {
                    "title": "Send meeting invite",
                    "owner_user_id": "create action item to send meeting invite for customer tomorrow at 12pm and assign to me",
                    "operation_id": "op_test_458",
                },
                {"project_id": "proj-1234-5678", "user_id": "u-owner"},
            )

        self.assertTrue(result.get("ok"))
        create_mock.assert_awaited_once()
        args = create_mock.await_args.args
        self.assertEqual(args[1].get("owner_user_id"), "u-owner")

    async def test_create_tool_ignores_owner_when_message_has_no_explicit_owner_intent(self):
        tool = tools_module.ActionItemCreateTool()
        project = {
            "project_id": "proj-1234-5678",
            "timezone": "Asia/Kolkata",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "email": "owner@example.com", "status": "active"}],
        }
        create_mock = AsyncMock(
            return_value={
                "entity_type": "action_item",
                "action_item_id": "ai-121",
                "display_key": "ACTION-121",
                "project_id": "proj-1234-5678",
                "title": "Send meeting invite for customer on Friday at 9 AM",
                "status": "open",
                "owner_user_id": None,
                "created_by_user_id": "u-owner",
                "source": "manual",
                "needs_followup": True,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "create",
            new=create_mock,
        ):
            result = await tool.execute_with_context(
                {
                    "title": "Send meeting invite for customer on Friday at 9 AM",
                    # Simulate model hallucinating owner though message has no assignment phrase.
                    "owner_user_id": "u-owner",
                    "operation_id": "op_test_458a",
                },
                {
                    "project_id": "proj-1234-5678",
                    "user_id": "u-owner",
                    "latest_user_message": "create action item to send invite to customer meeting on friday 9 am",
                },
            )

        self.assertTrue(result.get("ok"))
        args = create_mock.await_args.args
        payload = args[1]
        self.assertIsNone(payload.get("owner_user_id"))
        summary = str(((result.get("committed_action") or {}).get("summary")) or "")
        self.assertIn("owner is unresolved", summary.lower())

    async def test_create_tool_backfills_owner_from_latest_user_message_self_alias(self):
        tool = tools_module.ActionItemCreateTool()
        project = {
            "project_id": "proj-1234-5678",
            "timezone": "Asia/Kolkata",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "email": "owner@example.com", "status": "active"}],
        }
        create_mock = AsyncMock(
            return_value={
                "entity_type": "action_item",
                "action_item_id": "ai-13",
                "display_key": "ACTION-13",
                "project_id": "proj-1234-5678",
                "title": "Send meeting invite",
                "status": "open",
                "owner_user_id": "u-owner",
                "created_by_user_id": "u-owner",
                "source": "manual",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "create",
            new=create_mock,
        ):
            result = await tool.execute_with_context(
                {
                    "title": "Send meeting invite",
                    "operation_id": "op_test_459",
                },
                {
                    "project_id": "proj-1234-5678",
                    "user_id": "u-owner",
                    "latest_user_message": "create action item to send meeting invite for customer tomorrow at 12pm and assign to me",
                },
            )

        self.assertTrue(result.get("ok"))
        args = create_mock.await_args.args
        payload = args[1]
        self.assertEqual(payload.get("owner_user_id"), "u-owner")
        self.assertIn("assign to me", str(payload.get("raw_text") or "").lower())
        self.assertIn("tomorrow", str(payload.get("title") or "").lower())
        self.assertIn("12pm", str(payload.get("title") or "").lower())

    async def test_create_tool_strips_create_wrapper_and_assignment_suffix_from_title(self):
        tool = tools_module.ActionItemCreateTool()
        project = {
            "project_id": "proj-1234-5678",
            "timezone": "Asia/Kolkata",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "email": "owner@example.com", "status": "active", "name": "Owner"},
                {"user_id": "u-sridevi", "email": "sridevi@example.com", "status": "active", "name": "Sridevi"},
            ],
        }
        create_mock = AsyncMock(
            return_value={
                "entity_type": "action_item",
                "action_item_id": "ai-13a",
                "display_key": "ACTION-13A",
                "project_id": "proj-1234-5678",
                "title": "send meeting invite to customer on 24th March 2026 at 10:00 IST",
                "status": "open",
                "owner_user_id": "u-sridevi",
                "created_by_user_id": "u-owner",
                "source": "manual",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )
        raw_text = "create an action item to send meeting invite to customer on tuesday at 10 am and assign to sridevi"
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "create",
            new=create_mock,
        ):
            result = await tool.execute_with_context(
                {
                    "title": raw_text,
                    "operation_id": "op_test_459a",
                },
                {
                    "project_id": "proj-1234-5678",
                    "user_id": "u-owner",
                    "latest_user_message": raw_text,
                },
            )

        self.assertTrue(result.get("ok"))
        payload = create_mock.await_args.args[1]
        normalized_title = str(payload.get("title") or "").lower()
        self.assertNotIn("create an action item", normalized_title)
        self.assertNotIn("assign to sridevi", normalized_title)
        self.assertIn("send meeting invite", normalized_title)
        self.assertIn("tuesday", normalized_title)
        self.assertIn("10 am", normalized_title)

    async def test_create_tool_strips_wrapper_variants_from_title(self):
        tool = tools_module.ActionItemCreateTool()
        project = {
            "project_id": "proj-1234-5678",
            "timezone": "Asia/Kolkata",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "email": "owner@example.com", "status": "active", "name": "Owner"},
                {"user_id": "u-sridevi", "email": "sridevi@example.com", "status": "active", "name": "Sridevi"},
            ],
        }
        create_mock = AsyncMock(
            return_value={
                "entity_type": "action_item",
                "action_item_id": "ai-13b",
                "display_key": "ACTION-13B",
                "project_id": "proj-1234-5678",
                "title": "send meeting invite to customer on 24th March 2026 at 10:00 IST",
                "status": "open",
                "owner_user_id": "u-sridevi",
                "created_by_user_id": "u-owner",
                "source": "manual",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )
        phrasing_cases = [
            "create one action item to send meeting invite to customer on tuesday at 10 am and assign to sridevi",
            "please add an action item to send meeting invite to customer on tuesday at 10 am and assign to sridevi",
            "kindly make a new action item to send meeting invite to customer on tuesday at 10 am and assign to sridevi",
        ]
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "create",
            new=create_mock,
        ):
            for idx, raw_text in enumerate(phrasing_cases, start=1):
                create_mock.reset_mock()
                result = await tool.execute_with_context(
                    {
                        "title": raw_text,
                        "operation_id": f"op_test_459b_{idx}",
                    },
                    {
                        "project_id": "proj-1234-5678",
                        "user_id": "u-owner",
                        "latest_user_message": raw_text,
                    },
                )

                self.assertTrue(result.get("ok"), msg=raw_text)
                payload = create_mock.await_args.args[1]
                normalized_title = str(payload.get("title") or "").lower()
                self.assertNotIn("action item", normalized_title, msg=raw_text)
                self.assertNotIn("assign to sridevi", normalized_title, msg=raw_text)
                self.assertIn("send meeting invite", normalized_title, msg=raw_text)

    async def test_create_tool_summary_requests_owner_followup_when_needs_followup(self):
        tool = tools_module.ActionItemCreateTool()
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "email": "owner@example.com", "status": "active"}],
        }
        create_mock = AsyncMock(
            return_value={
                "entity_type": "action_item",
                "action_item_id": "ai-14",
                "display_key": "ACTION-14",
                "project_id": "proj-1234-5678",
                "title": "Send meeting invite",
                "status": "open",
                "owner_user_id": None,
                "created_by_user_id": "u-owner",
                "source": "manual",
                "needs_followup": True,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "create",
            new=create_mock,
        ):
            result = await tool.execute_with_context(
                {
                    "title": "Send meeting invite",
                    "operation_id": "op_test_460",
                },
                {
                    "project_id": "proj-1234-5678",
                    "user_id": "u-owner",
                    "latest_user_message": "create action item to send meeting invite for customer tomorrow at 12pm",
                },
            )

        self.assertTrue(result.get("ok"))
        summary = str(((result.get("committed_action") or {}).get("summary")) or "")
        self.assertIn("owner is unresolved", summary.lower())
        self.assertIn("action item created:", summary.lower())

    async def test_create_tool_resolves_owner_from_create_for_phrase(self):
        tool = tools_module.ActionItemCreateTool()
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "email": "owner@example.com", "status": "active", "name": "Sridevi"},
                {"user_id": "u-prabhu", "email": "prabhu@example.com", "status": "active", "name": "Prabhu"},
            ],
        }
        create_mock = AsyncMock(
            return_value={
                "entity_type": "action_item",
                "action_item_id": "ai-15",
                "display_key": "ACTION-15",
                "project_id": "proj-1234-5678",
                "title": "Setup CI CD pipeline",
                "status": "open",
                "owner_user_id": "u-prabhu",
                "created_by_user_id": "u-owner",
                "source": "manual",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "create",
            new=create_mock,
        ):
            result = await tool.execute_with_context(
                {
                    "title": "Setup CI CD pipeline",
                    "operation_id": "op_test_460a",
                },
                {
                    "project_id": "proj-1234-5678",
                    "user_id": "u-owner",
                    "latest_user_message": "Can you create an action item for Prabhu saying setup CI CD pipeline",
                },
            )

        self.assertTrue(result.get("ok"))
        payload = create_mock.await_args.args[1]
        self.assertEqual(payload.get("owner_user_id"), "u-prabhu")

    async def test_create_tool_resolves_owner_from_name_to_action_phrase(self):
        tool = tools_module.ActionItemCreateTool()
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "email": "owner@example.com", "status": "active", "name": "Owner"},
                {"user_id": "u-sridevi", "email": "sridevi@example.com", "status": "active", "name": "Sridevi"},
            ],
        }
        create_mock = AsyncMock(
            return_value={
                "entity_type": "action_item",
                "action_item_id": "ai-15a",
                "display_key": "ACTION-15A",
                "project_id": "proj-1234-5678",
                "title": "Connect with DevOps team",
                "status": "open",
                "owner_user_id": "u-sridevi",
                "created_by_user_id": "u-owner",
                "source": "manual",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "create",
            new=create_mock,
        ):
            result = await tool.execute_with_context(
                {
                    "title": "Connect with DevOps team",
                    "operation_id": "op_test_460aa",
                },
                {
                    "project_id": "proj-1234-5678",
                    "user_id": "u-owner",
                    "latest_user_message": "create action item - sridevi to connect with devops team and get necessary access",
                },
            )

        self.assertTrue(result.get("ok"))
        payload = create_mock.await_args.args[1]
        self.assertEqual(payload.get("owner_user_id"), "u-sridevi")

    async def test_create_tool_ignores_weak_unresolved_for_phrase_owner_hint(self):
        tool = tools_module.ActionItemCreateTool()
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "email": "owner@example.com", "status": "active", "name": "Sridevi"},
            ],
        }
        create_mock = AsyncMock(
            return_value={
                "entity_type": "action_item",
                "action_item_id": "ai-16",
                "display_key": "ACTION-16",
                "project_id": "proj-1234-5678",
                "title": "Share release notes",
                "status": "open",
                "owner_user_id": None,
                "created_by_user_id": "u-owner",
                "source": "manual",
                "needs_followup": True,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "create",
            new=create_mock,
        ):
            result = await tool.execute_with_context(
                {
                    "title": "Share release notes",
                    "operation_id": "op_test_460b",
                },
                {
                    "project_id": "proj-1234-5678",
                    "user_id": "u-owner",
                    "latest_user_message": "create action item for customer saying share release notes",
                },
            )

        self.assertTrue(result.get("ok"))
        payload = create_mock.await_args.args[1]
        self.assertIsNone(payload.get("owner_user_id"))

    async def test_create_tool_owner_resolution_regional_phrasing_variants(self):
        tool = tools_module.ActionItemCreateTool()
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "email": "owner@example.com", "status": "active", "name": "Sridevi"},
                {"user_id": "u-prabhu", "email": "prabhu@example.com", "status": "active", "name": "Prabhu"},
                {"user_id": "u-anna", "email": "anna@example.com", "status": "active", "name": "Anna"},
                {"user_id": "u-rahul", "email": "rahul@example.com", "status": "active", "name": "Rahul"},
            ],
        }
        create_mock = AsyncMock(
            return_value={
                "entity_type": "action_item",
                "action_item_id": "ai-regional",
                "display_key": "ACTION-REG",
                "project_id": "proj-1234-5678",
                "title": "Regional phrasing task",
                "status": "open",
                "owner_user_id": None,
                "created_by_user_id": "u-owner",
                "source": "manual",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )
        phrasing_cases = [
            # US-style phrasing
            ("Could you create an action item for Prabhu to follow up on CI pipeline?", "u-prabhu"),
            ("Please assign this action item to Anna for release note review.", "u-anna"),
            # Europe-style phrasing
            ("Please create an action item for Anna regarding deployment checks.", "u-anna"),
            ("Kindly set owner as Prabhu for CI/CD setup.", "u-prabhu"),
            # India-style/common phrasing
            ("Pls create action item for Rahul saying verify API latency fix.", "u-rahul"),
            ("Create action item and assign to me for final validation.", "u-owner"),
            # Should stay unresolved (not an owner mention)
            ("Create an action item for customer update on board status.", None),
            ("Please create action item to track release readiness.", None),
        ]

        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "create",
            new=create_mock,
        ):
            for idx, (utterance, expected_owner) in enumerate(phrasing_cases, start=1):
                create_mock.reset_mock()
                result = await tool.execute_with_context(
                    {
                        "title": "Regional phrasing task",
                        "operation_id": f"op_test_regional_{idx}",
                    },
                    {
                        "project_id": "proj-1234-5678",
                        "user_id": "u-owner",
                        "latest_user_message": utterance,
                    },
                )
                self.assertTrue(result.get("ok"), msg=f"Failed for utterance: {utterance}")
                payload = create_mock.await_args.args[1]
                self.assertEqual(
                    payload.get("owner_user_id"),
                    expected_owner,
                    msg=f"Unexpected owner resolution for utterance: {utterance}",
                )

    async def test_create_tool_llm_stage2_fallback_resolves_owner_hint(self):
        tool = tools_module.ActionItemCreateTool()
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "email": "owner@example.com", "status": "active", "name": "Sridevi"},
                {"user_id": "u-anna", "email": "anna@example.com", "status": "active", "name": "Anna"},
            ],
        }
        create_mock = AsyncMock(
            return_value={
                "entity_type": "action_item",
                "action_item_id": "ai-llm",
                "display_key": "ACTION-LLM",
                "project_id": "proj-1234-5678",
                "title": "Regional phrasing task",
                "status": "open",
                "owner_user_id": "u-anna",
                "created_by_user_id": "u-owner",
                "source": "manual",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )
        gateway = AsyncMock()
        gateway.generate = AsyncMock(
            return_value=SimpleNamespace(
                ok=True,
                text='{"explicit_owner_intent": true, "owner_hint": "Anna"}',
            )
        )
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module,
            "get_shared_llm_gateway",
            return_value=gateway,
        ), patch.object(
            tools_module.ActionItemService,
            "create",
            new=create_mock,
        ), patch.object(
            tools_module.ActionItemCreateTool,
            "_parse_owner_intent",
            return_value=(True, None, "weak"),
        ):
            result = await tool.execute_with_context(
                {
                    "title": "Regional phrasing task",
                    "operation_id": "op_test_llm_stage2",
                },
                {
                    "project_id": "proj-1234-5678",
                    "user_id": "u-owner",
                    "latest_user_message": "Please assign this action item to Anna for release note review.",
                },
            )

        self.assertTrue(result.get("ok"))
        gateway.generate.assert_awaited_once()
        payload = create_mock.await_args.args[1]
        self.assertEqual(payload.get("owner_user_id"), "u-anna")

    async def test_list_tool_resolves_actor_from_email(self):
        tool = tools_module.ActionItemListTool()
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-member", "email": "member@example.com", "status": "active"}],
        }
        list_mock = AsyncMock(return_value={"items": [], "total": 0, "limit": 100, "offset": 0})
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "list",
            new=list_mock,
        ):
            result = await tool.execute_with_context(
                {"status": "open"},
                {
                    "project_id": "proj-1234-5678",
                    "user_id": "external-actor-id",
                    "actor_email": "member@example.com",
                },
            )

        self.assertTrue(result.get("ok"))
        list_mock.assert_awaited_once()
        args = list_mock.await_args.args
        self.assertEqual(args[0], "proj-1234-5678")
        self.assertEqual(args[1].get("user_id"), "u-member")
        self.assertEqual(args[1].get("status"), "open")

    async def test_list_tool_defaults_to_open_when_status_missing(self):
        tool = tools_module.ActionItemListTool()
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-member", "email": "member@example.com", "status": "active"}],
        }
        list_mock = AsyncMock(return_value={"items": [], "total": 0, "limit": 100, "offset": 0})
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "list",
            new=list_mock,
        ):
            result = await tool.execute_with_context(
                {},
                {
                    "project_id": "proj-1234-5678",
                    "user_id": "u-member",
                },
            )

        self.assertTrue(result.get("ok"))
        list_mock.assert_awaited_once()
        args = list_mock.await_args.args
        self.assertEqual(args[1].get("status"), "open")

    async def test_list_tool_defaults_to_open_for_all_action_items_phrase(self):
        tool = tools_module.ActionItemListTool()
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-member", "email": "member@example.com", "status": "active"}],
        }
        list_mock = AsyncMock(return_value={"items": [], "total": 0, "limit": 100, "offset": 0})
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "list",
            new=list_mock,
        ):
            result = await tool.execute_with_context(
                {},
                {
                    "project_id": "proj-1234-5678",
                    "user_id": "u-member",
                    "latest_user_message": "list all action items",
                },
            )

        self.assertTrue(result.get("ok"))
        list_mock.assert_awaited_once()
        args = list_mock.await_args.args
        self.assertEqual(args[1].get("status"), "open")

    async def test_list_tool_uses_all_scope_when_user_explicitly_asks_all_statuses(self):
        tool = tools_module.ActionItemListTool()
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-member", "email": "member@example.com", "status": "active"}],
        }
        list_mock = AsyncMock(return_value={"items": [], "total": 0, "limit": 100, "offset": 0})
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "list",
            new=list_mock,
        ):
            result = await tool.execute_with_context(
                {},
                {
                    "project_id": "proj-1234-5678",
                    "user_id": "u-member",
                    "latest_user_message": "list all action items including closed",
                },
            )

        self.assertTrue(result.get("ok"))
        list_mock.assert_awaited_once()
        args = list_mock.await_args.args
        self.assertIsNone(args[1].get("status"))

    async def test_list_tool_includes_owner_display_name_in_items(self):
        tool = tools_module.ActionItemListTool()
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-member", "email": "member@example.com", "status": "active", "name": "Sridevi"}
            ],
        }
        list_mock = AsyncMock(
            return_value={
                "items": [
                    {
                        "action_item_id": "ai-1",
                        "display_key": "ACTION-1",
                        "status": "open",
                        "title": "Follow up with infra",
                        "owner_user_id": "u-member",
                    }
                ],
                "total": 1,
                "limit": 100,
                "offset": 0,
            }
        )
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module.ActionItemService,
            "list",
            new=list_mock,
        ):
            result = await tool.execute_with_context(
                {},
                {
                    "project_id": "proj-1234-5678",
                    "user_id": "u-member",
                },
            )

        self.assertTrue(result.get("ok"))
        items = result.get("items") or []
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].get("owner_display_name"), "Sridevi")

    async def test_update_tool_resolves_display_key_reference(self):
        tool = tools_module.ActionItemUpdateTool()
        project = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "email": "owner@example.com", "status": "active"}],
        }
        collection = AsyncMock()
        collection.find_one = AsyncMock(return_value={"action_item_id": "ai-7"})
        update_mock = AsyncMock(
            return_value={
                "entity_type": "action_item",
                "action_item_id": "ai-7",
                "display_key": "ACTION-7",
                "project_id": "proj-1234-5678",
                "title": "Updated",
                "status": "open",
                "owner_user_id": "u-owner",
                "created_by_user_id": "u-owner",
                "source": "manual",
                "related_to": "Topic",
                "related_type": "topic",
                "related_id": None,
                "due_type": "no_due_date",
                "due_date": None,
                "needs_followup": False,
                "needs_followup_set_at": None,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "closed_at": None,
                "closed_by_user_id": None,
            }
        )
        with patch.object(tools_module.settings, "action_items_enabled", True), patch.object(
            tools_module,
            "load_project",
            new=AsyncMock(return_value=project),
        ), patch.object(
            tools_module,
            "get_brain_collection",
            return_value=collection,
        ), patch.object(
            tools_module.ActionItemService,
            "update",
            new=update_mock,
        ):
            result = await tool.execute_with_context(
                {
                    "action_item_ref": "ACTION-7",
                    "title": "Updated",
                    "operation_id": "op_test_789",
                },
                {"project_id": "proj-1234-5678", "user_id": "u-owner"},
            )

        self.assertTrue(result.get("ok"))
        update_mock.assert_awaited_once()
        update_args = update_mock.await_args.args
        self.assertEqual(update_args[1], "ai-7")


if __name__ == "__main__":
    unittest.main()
