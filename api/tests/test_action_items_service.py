import importlib
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from app.action_items.due_inference_policy import DueInferenceResult
from app.action_items.temporal_parser import TemporalParseResult

service_module = importlib.import_module("app.action_items.service")


class ActionItemServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_ingest_duplicate_within_window_returns_existing_item(self):
        created_at = datetime.utcnow()
        existing_action_item = {
            "_id": "doc-existing",
            "entity_type": "action_item",
            "action_item_id": "ai-existing",
            "display_key": "ACTION-21",
            "project_id": "proj-1234-5678",
            "title": "Send update",
            "status": "open",
            "owner_user_id": "u-owner",
            "created_by_user_id": "u-owner",
            "source": "cortex_comment_auto",
            "related_to": "Task ABC-1",
            "related_type": "work_item",
            "due_type": "no_due_date",
            "needs_followup": False,
            "needs_followup_set_at": None,
            "source_event_key": "comment:ABC-1:hash:t1",
            "created_at": created_at,
            "updated_at": created_at,
        }
        collection = AsyncMock()
        collection.insert_one = AsyncMock()
        collection.find_one = AsyncMock(return_value=existing_action_item)
        project_doc = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "status": "active", "role": "owner"}],
        }

        with patch.object(
            service_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"custom_project_id": "proj-1234-5678", "project_doc": project_doc}),
        ), patch.object(
            service_module,
            "ensure_project_action_item_indexes",
            new=AsyncMock(return_value=None),
        ), patch.object(
            service_module,
            "get_brain_collection",
            return_value=collection,
        ), patch.object(
            service_module.settings,
            "action_items_ingest_dedupe_window_hours",
            168,
        ), patch.object(
            service_module,
            "create_pending_interaction",
            new=AsyncMock(return_value={}),
        ):
            result = await service_module.ActionItemService.create(
                "mongo-project-id",
                {
                        "user_id": "u-owner",
                        "title": "Send update",
                        "owner_user_id": "u-owner",
                        "source_event_key": "comment:ABC-1:hash:t1",
                    },
                )

        self.assertEqual(result["action_item_id"], "ai-existing")
        collection.insert_one.assert_not_awaited()
        collection.find_one.assert_awaited_once()

    async def test_create_without_related_to_can_still_be_open_when_owner_present(self):
        collection = AsyncMock()
        collection.find_one = AsyncMock(return_value=None)
        collection.insert_one = AsyncMock(return_value=AsyncMock(inserted_id="x"))
        project_doc = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "status": "active", "role": "owner"}],
        }

        with patch.object(
            service_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"custom_project_id": "proj-1234-5678", "project_doc": project_doc}),
        ), patch.object(
            service_module,
            "ensure_project_action_item_indexes",
            new=AsyncMock(return_value=None),
        ), patch.object(
            service_module,
            "get_brain_collection",
            return_value=collection,
        ), patch.object(
            service_module,
            "next_sequence",
            new=AsyncMock(return_value=8),
        ), patch.object(
            service_module,
            "invalidate_pm_board_summary_cache",
            return_value=None,
        ), patch.object(
            service_module,
            "create_pending_interaction",
            new=AsyncMock(return_value={}),
        ) as create_pending_mock, patch.object(
            service_module.settings,
            "action_items_followup_pm_escalation_hours",
            4,
        ):
            result = await service_module.ActionItemService.create(
                "mongo-project-id",
                {
                    "user_id": "u-owner",
                    "title": "Send update",
                    "owner_user_id": "u-owner",
                    # related_to intentionally omitted; now optional
                    "source": "manual",
                },
            )

        self.assertEqual(result["display_key"], "ACTION-8")
        self.assertFalse(result["needs_followup"])
        self.assertIsNone(result.get("needs_followup_set_at"))

    async def test_create_sets_followup_anchor_when_mandatory_fields_missing(self):
        collection = AsyncMock()
        collection.find_one = AsyncMock(return_value=None)
        collection.insert_one = AsyncMock(return_value=AsyncMock(inserted_id="x"))
        project_doc = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "status": "active", "role": "owner"}],
        }

        with patch.object(
            service_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"custom_project_id": "proj-1234-5678", "project_doc": project_doc}),
        ), patch.object(
            service_module,
            "ensure_project_action_item_indexes",
            new=AsyncMock(return_value=None),
        ), patch.object(
            service_module,
            "get_brain_collection",
            return_value=collection,
        ), patch.object(
            service_module,
            "next_sequence",
            new=AsyncMock(return_value=7),
        ), patch.object(
            service_module,
            "invalidate_pm_board_summary_cache",
            return_value=None,
        ), patch.object(
            service_module,
            "create_pending_interaction",
            new=AsyncMock(return_value={}),
        ) as create_pending_mock, patch.object(
            service_module.settings,
            "action_items_followup_pm_escalation_hours",
            4,
        ):
            result = await service_module.ActionItemService.create(
                "mongo-project-id",
                {
                    "user_id": "u-owner",
                    "title": "Follow up with ops",
                    "related_to": "Ops confirmation",
                    # owner_user_id intentionally missing -> needs_followup
                    "source": "manual",
                },
            )

        self.assertEqual(result["display_key"], "ACTION-7")
        self.assertTrue(result["needs_followup"])
        self.assertIsNotNone(result["needs_followup_set_at"])
        self.assertEqual(result["status"], "open")
        create_pending_mock.assert_awaited_once()
        pending_kwargs = create_pending_mock.await_args.kwargs
        self.assertIsNotNone(pending_kwargs.get("escalate_at"))
        self.assertIsNotNone(pending_kwargs.get("expires_at"))
        self.assertEqual(
            int((pending_kwargs["expires_at"] - result["created_at"]).total_seconds()),
            4 * 3600,
        )

    async def test_create_rejects_non_member_owner_user_id(self):
        collection = AsyncMock()
        collection.find_one = AsyncMock(return_value=None)
        project_doc = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "status": "active", "role": "owner"}],
        }

        with patch.object(
            service_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"custom_project_id": "proj-1234-5678", "project_doc": project_doc}),
        ), patch.object(
            service_module,
            "ensure_project_action_item_indexes",
            new=AsyncMock(return_value=None),
        ), patch.object(
            service_module,
            "get_brain_collection",
            return_value=collection,
        ), patch.object(
            service_module,
            "next_sequence",
            new=AsyncMock(return_value=1),
        ):
            with self.assertRaises(ValueError):
                await service_module.ActionItemService.create(
                    "mongo-project-id",
                    {
                        "user_id": "u-owner",
                        "title": "Email ops",
                        "owner_user_id": "u-outsider",
                        "related_to": "Topic",
                    },
                )

    async def test_create_infers_due_from_target_datetime_for_invite_action(self):
        collection = AsyncMock()
        collection.find_one = AsyncMock(return_value=None)
        collection.insert_one = AsyncMock(return_value=AsyncMock(inserted_id="x"))
        project_doc = {
            "project_id": "proj-1234-5678",
            "timezone": "Asia/Kolkata",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "status": "active", "role": "owner"}],
        }

        with patch.object(
            service_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"custom_project_id": "proj-1234-5678", "project_doc": project_doc}),
        ), patch.object(
            service_module,
            "ensure_project_action_item_indexes",
            new=AsyncMock(return_value=None),
        ), patch.object(
            service_module,
            "get_brain_collection",
            return_value=collection,
        ), patch.object(
            service_module,
            "next_sequence",
            new=AsyncMock(return_value=9),
        ), patch.object(
            service_module,
            "invalidate_pm_board_summary_cache",
            return_value=None,
        ), patch.object(
            service_module,
            "create_pending_interaction",
            new=AsyncMock(return_value={}),
        ):
            result = await service_module.ActionItemService.create(
                "mongo-project-id",
                {
                    "user_id": "u-owner",
                    "title": "send meeting invite for customer tomorrow at 11am",
                    "owner_user_id": "u-owner",
                    "source": "manual",
                    "due_type": "no_due_date",
                },
            )

        self.assertEqual(result["display_key"], "ACTION-9")
        self.assertEqual(result["due_type"], "by_date")
        self.assertIsNotNone(result.get("due_date"))
        self.assertIsNotNone(result.get("target_datetime"))
        self.assertLess(result["due_date"], result["target_datetime"])

    async def test_create_canonicalizes_relative_day_and_time_in_title(self):
        collection = AsyncMock()
        collection.find_one = AsyncMock(return_value=None)
        collection.insert_one = AsyncMock(return_value=AsyncMock(inserted_id="x"))
        project_doc = {
            "project_id": "proj-1234-5678",
            "timezone": "Asia/Kolkata",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "status": "active", "role": "owner"}],
        }
        target_datetime = datetime(2026, 3, 17, 4, 30)  # 10:00 IST

        with patch.object(
            service_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"custom_project_id": "proj-1234-5678", "project_doc": project_doc}),
        ), patch.object(
            service_module,
            "ensure_project_action_item_indexes",
            new=AsyncMock(return_value=None),
        ), patch.object(
            service_module,
            "get_brain_collection",
            return_value=collection,
        ), patch.object(
            service_module,
            "next_sequence",
            new=AsyncMock(return_value=11),
        ), patch.object(
            service_module,
            "invalidate_pm_board_summary_cache",
            return_value=None,
        ), patch.object(
            service_module,
            "create_pending_interaction",
            new=AsyncMock(return_value={}),
        ), patch.object(
            service_module,
            "parse_temporal_hints",
            return_value=TemporalParseResult(
                target_datetime=target_datetime,
                explicit_due_datetime=None,
                explicit_due_type=None,
            ),
        ), patch.object(
            service_module,
            "infer_due",
            return_value=DueInferenceResult(due_type="by_date", due_date=datetime(2026, 3, 16, 11, 30)),
        ):
            result = await service_module.ActionItemService.create(
                "mongo-project-id",
                {
                    "user_id": "u-owner",
                    "title": "send invite to customer tomorrow at 10am",
                    "owner_user_id": "u-owner",
                    "source": "manual",
                    "raw_text": "create action item to send invite to customer tomorrow at 10am and assign to me",
                    "due_type": "no_due_date",
                },
            )

        self.assertNotIn("tomorrow", result["title"].lower())
        self.assertIn("17th March 2026", result["title"])
        self.assertIn("10:00 IST", result["title"])

    async def test_create_canonicalizes_weekday_in_title(self):
        collection = AsyncMock()
        collection.find_one = AsyncMock(return_value=None)
        collection.insert_one = AsyncMock(return_value=AsyncMock(inserted_id="x"))
        project_doc = {
            "project_id": "proj-1234-5678",
            "timezone": "Asia/Kolkata",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "status": "active", "role": "owner"}],
        }
        target_datetime = datetime(2026, 3, 20, 11, 30)  # 17:00 IST

        with patch.object(
            service_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"custom_project_id": "proj-1234-5678", "project_doc": project_doc}),
        ), patch.object(
            service_module,
            "ensure_project_action_item_indexes",
            new=AsyncMock(return_value=None),
        ), patch.object(
            service_module,
            "get_brain_collection",
            return_value=collection,
        ), patch.object(
            service_module,
            "next_sequence",
            new=AsyncMock(return_value=12),
        ), patch.object(
            service_module,
            "invalidate_pm_board_summary_cache",
            return_value=None,
        ), patch.object(
            service_module,
            "create_pending_interaction",
            new=AsyncMock(return_value={}),
        ), patch.object(
            service_module,
            "parse_temporal_hints",
            return_value=TemporalParseResult(
                target_datetime=target_datetime,
                explicit_due_datetime=None,
                explicit_due_type=None,
            ),
        ), patch.object(
            service_module,
            "infer_due",
            return_value=DueInferenceResult(due_type="by_date", due_date=datetime(2026, 3, 20, 11, 30)),
        ):
            result = await service_module.ActionItemService.create(
                "mongo-project-id",
                {
                    "user_id": "u-owner",
                    "title": "Send release notes by Friday",
                    "owner_user_id": "u-owner",
                    "source": "manual",
                    "raw_text": "create action item to send release notes by friday and assign to me",
                    "due_type": "no_due_date",
                },
            )

        self.assertNotIn("friday", result["title"].lower())
        self.assertIn("by 20th March 2026", result["title"])

    async def test_create_canonicalizes_relative_title_using_due_date_fallback_when_target_missing(self):
        collection = AsyncMock()
        collection.find_one = AsyncMock(return_value=None)
        collection.insert_one = AsyncMock(return_value=AsyncMock(inserted_id="x"))
        project_doc = {
            "project_id": "proj-1234-5678",
            "timezone": "Asia/Kolkata",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "status": "active", "role": "owner"}],
        }

        with patch.object(
            service_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"custom_project_id": "proj-1234-5678", "project_doc": project_doc}),
        ), patch.object(
            service_module,
            "ensure_project_action_item_indexes",
            new=AsyncMock(return_value=None),
        ), patch.object(
            service_module,
            "get_brain_collection",
            return_value=collection,
        ), patch.object(
            service_module,
            "next_sequence",
            new=AsyncMock(return_value=13),
        ), patch.object(
            service_module,
            "invalidate_pm_board_summary_cache",
            return_value=None,
        ), patch.object(
            service_module,
            "create_pending_interaction",
            new=AsyncMock(return_value={}),
        ), patch.object(
            service_module,
            "parse_temporal_hints",
            return_value=TemporalParseResult(
                target_datetime=None,
                explicit_due_datetime=None,
                explicit_due_type=None,
            ),
        ), patch.object(
            service_module,
            "infer_due",
            return_value=DueInferenceResult(due_type="by_date", due_date=datetime(2026, 3, 20, 11, 30)),
        ):
            result = await service_module.ActionItemService.create(
                "mongo-project-id",
                {
                    "user_id": "u-owner",
                    "title": "Send release notes by Friday",
                    "owner_user_id": "u-owner",
                    "source": "manual",
                    "raw_text": "create action item to send release notes by friday and assign to me",
                    "due_type": "no_due_date",
                },
            )

        self.assertNotIn("friday", result["title"].lower())
        self.assertIn("by 20th March 2026", result["title"])

    async def test_create_ignores_stale_due_input_when_text_is_target_context(self):
        collection = AsyncMock()
        collection.find_one = AsyncMock(return_value=None)
        collection.insert_one = AsyncMock(return_value=AsyncMock(inserted_id="x"))
        project_doc = {
            "project_id": "proj-1234-5678",
            "timezone": "Asia/Kolkata",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "status": "active", "role": "owner"}],
        }
        captured: dict = {}

        def _fake_infer_due(**kwargs):
            captured.update(kwargs)
            return DueInferenceResult(due_type="no_due_date", due_date=None)

        parsed_target = datetime(2026, 3, 17, 4, 30)

        with patch.object(
            service_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"custom_project_id": "proj-1234-5678", "project_doc": project_doc}),
        ), patch.object(
            service_module,
            "ensure_project_action_item_indexes",
            new=AsyncMock(return_value=None),
        ), patch.object(
            service_module,
            "get_brain_collection",
            return_value=collection,
        ), patch.object(
            service_module,
            "next_sequence",
            new=AsyncMock(return_value=10),
        ), patch.object(
            service_module,
            "invalidate_pm_board_summary_cache",
            return_value=None,
        ), patch.object(
            service_module,
            "create_pending_interaction",
            new=AsyncMock(return_value={}),
        ), patch.object(
            service_module,
            "parse_temporal_hints",
            return_value=TemporalParseResult(
                target_datetime=parsed_target,
                explicit_due_datetime=None,
                explicit_due_type=None,
            ),
        ), patch.object(
            service_module,
            "infer_due",
            side_effect=_fake_infer_due,
        ):
            await service_module.ActionItemService.create(
                "mongo-project-id",
                {
                    "user_id": "u-owner",
                    "title": "Send meeting invite for customer",
                    "owner_user_id": "u-owner",
                    "source": "manual",
                    "raw_text": "create action item for me to send meeting invite for customer for tuesday at 10am",
                    # stale model-supplied due fields should not be treated as explicit due here
                    "due_type": "by_date",
                    "due_date": datetime(2023, 10, 31, 4, 30),
                },
            )

        self.assertIsNone(captured.get("explicit_due_date"))
        self.assertIsNone(captured.get("explicit_due_type"))
        self.assertEqual(captured.get("target_datetime"), parsed_target)

    async def test_update_followup_transition_sets_anchor_timestamp(self):
        created_at = datetime.utcnow()
        existing = {
            "_id": "doc1",
            "entity_type": "action_item",
            "action_item_id": "ai-1",
            "display_key": "ACTION-1",
            "project_id": "proj-1234-5678",
            "title": "Action",
            "status": "open",
            "owner_user_id": "u-owner",
            "created_by_user_id": "u-owner",
            "source": "manual",
            "related_to": "Context",
            "related_type": "other",
            "due_type": "no_due_date",
            "needs_followup": False,
            "needs_followup_set_at": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
        updated = dict(existing)
        updated["needs_followup"] = True
        updated["needs_followup_set_at"] = datetime.utcnow()

        collection = AsyncMock()
        collection.find_one = AsyncMock(side_effect=[existing, updated])
        collection.update_one = AsyncMock(return_value=AsyncMock(modified_count=1))
        project_doc = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "status": "active", "role": "owner"}],
        }

        with patch.object(
            service_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"custom_project_id": "proj-1234-5678", "project_doc": project_doc}),
        ), patch.object(
            service_module,
            "get_brain_collection",
            return_value=collection,
        ), patch.object(
            service_module,
            "invalidate_pm_board_summary_cache",
            return_value=None,
        ), patch.object(
            service_module,
            "create_pending_interaction",
            new=AsyncMock(return_value={}),
        ), patch.object(
            service_module,
            "delete_pending_interactions_by_context",
            new=AsyncMock(return_value=0),
        ):
            result = await service_module.ActionItemService.update(
                "mongo-project-id",
                "ai-1",
                {"user_id": "u-owner", "needs_followup": True},
            )

        self.assertTrue(result["needs_followup"])
        self.assertIsNotNone(result["needs_followup_set_at"])

    async def test_create_with_owner_sends_assignment_notification(self):
        created_at = datetime.utcnow()
        created_doc = {
            "_id": "doc-created",
            "entity_type": "action_item",
            "action_item_id": "ai-9",
            "display_key": "ACTION-9",
            "project_id": "proj-1234-5678",
            "title": "Send update",
            "status": "open",
            "owner_user_id": "u-owner",
            "created_by_user_id": "u-owner",
            "source": "manual",
            "related_to": "Context",
            "related_type": "other",
            "due_type": "no_due_date",
            "needs_followup": False,
            "needs_followup_set_at": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
        collection = AsyncMock()
        collection.find_one = AsyncMock(side_effect=[None, created_doc])
        collection.insert_one = AsyncMock(return_value=AsyncMock(inserted_id="x"))
        project_doc = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "status": "active", "role": "owner"}],
        }

        with patch.object(
            service_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"custom_project_id": "proj-1234-5678", "project_doc": project_doc}),
        ), patch.object(
            service_module,
            "ensure_project_action_item_indexes",
            new=AsyncMock(return_value=None),
        ), patch.object(
            service_module,
            "get_brain_collection",
            return_value=collection,
        ), patch.object(
            service_module,
            "next_sequence",
            new=AsyncMock(return_value=9),
        ), patch.object(
            service_module,
            "invalidate_pm_board_summary_cache",
            return_value=None,
        ), patch.object(
            service_module,
            "create_pending_interaction",
            new=AsyncMock(return_value={}),
        ), patch.object(
            service_module,
            "send_action_item_assignment_notification",
            new=AsyncMock(return_value=True),
        ) as notify_mock:
            result = await service_module.ActionItemService.create(
                "mongo-project-id",
                {
                    "user_id": "u-owner",
                    "title": "Send update",
                    "owner_user_id": "u-owner",
                    "related_to": "Context",
                    "source": "manual",
                },
            )

        self.assertEqual(result["action_item_id"], "ai-9")
        notify_mock.assert_awaited_once()
        self.assertIsNone(notify_mock.await_args.kwargs["old_owner_user_id"])

    async def test_update_owner_change_sends_assignment_notification(self):
        created_at = datetime.utcnow()
        existing = {
            "_id": "doc1",
            "entity_type": "action_item",
            "action_item_id": "ai-1",
            "display_key": "ACTION-1",
            "project_id": "proj-1234-5678",
            "title": "Send update",
            "status": "open",
            "owner_user_id": "u-owner",
            "created_by_user_id": "u-owner",
            "source": "manual",
            "related_to": "Context",
            "related_type": "other",
            "due_type": "no_due_date",
            "needs_followup": False,
            "needs_followup_set_at": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
        updated = dict(existing)
        updated["owner_user_id"] = "u-new"
        updated["updated_at"] = datetime.utcnow()

        collection = AsyncMock()
        collection.find_one = AsyncMock(side_effect=[existing, updated])
        collection.update_one = AsyncMock(return_value=AsyncMock(modified_count=1))
        project_doc = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "status": "active", "role": "owner"},
                {"user_id": "u-new", "status": "active", "role": "member"},
            ],
        }

        with patch.object(
            service_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"custom_project_id": "proj-1234-5678", "project_doc": project_doc}),
        ), patch.object(
            service_module,
            "get_brain_collection",
            return_value=collection,
        ), patch.object(
            service_module,
            "invalidate_pm_board_summary_cache",
            return_value=None,
        ), patch.object(
            service_module,
            "create_pending_interaction",
            new=AsyncMock(return_value={}),
        ), patch.object(
            service_module,
            "delete_pending_interactions_by_context",
            new=AsyncMock(return_value=0),
        ), patch.object(
            service_module,
            "send_action_item_assignment_notification",
            new=AsyncMock(return_value=True),
        ) as notify_mock:
            result = await service_module.ActionItemService.update(
                "mongo-project-id",
                "ai-1",
                {"user_id": "u-owner", "owner_user_id": "u-new"},
            )

        self.assertEqual(result["owner_user_id"], "u-new")
        notify_mock.assert_awaited_once()
        self.assertEqual(notify_mock.await_args.kwargs["old_owner_user_id"], "u-owner")

    async def test_delete_allows_project_owner(self):
        existing = {
            "_id": "doc1",
            "entity_type": "action_item",
            "action_item_id": "ai-1",
            "display_key": "ACTION-1",
            "project_id": "proj-1234-5678",
            "status": "open",
            "created_by_user_id": "u-owner",
        }
        collection = AsyncMock()
        collection.find_one = AsyncMock(return_value=existing)
        collection.delete_one = AsyncMock(return_value=AsyncMock(deleted_count=1))
        project_doc = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-owner", "status": "active", "role": "owner"}],
        }

        with patch.object(
            service_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"custom_project_id": "proj-1234-5678", "project_doc": project_doc}),
        ), patch.object(
            service_module,
            "get_brain_collection",
            return_value=collection,
        ), patch.object(
            service_module,
            "delete_pending_interactions_by_context",
            new=AsyncMock(return_value=1),
        ) as delete_pending_mock, patch.object(
            service_module,
            "invalidate_pm_board_summary_cache",
            return_value=None,
        ):
            await service_module.ActionItemService.delete(
                "mongo-project-id",
                "ai-1",
                {"user_id": "u-owner"},
            )

        collection.delete_one.assert_awaited_once()
        delete_pending_mock.assert_awaited_once()

    async def test_delete_rejects_non_owner(self):
        project_doc = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [{"user_id": "u-member", "status": "active", "role": "member"}],
        }

        with patch.object(
            service_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"custom_project_id": "proj-1234-5678", "project_doc": project_doc}),
        ):
            with self.assertRaises(PermissionError):
                await service_module.ActionItemService.delete(
                    "mongo-project-id",
                    "ai-1",
                    {"user_id": "u-member"},
                )


if __name__ == "__main__":
    unittest.main()
