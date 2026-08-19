import unittest
from unittest.mock import AsyncMock, patch

from app.tasks.pending_parent_handler import (
    _recency_bucket,
    handle_pending_work_item_parent_clarification,
    queue_work_item_parent_clarification,
)


class WorkItemParentPendingHandlerTests(unittest.IsolatedAsyncioTestCase):
    def test_recency_bucket_has_real_tiers(self):
        self.assertEqual(_recency_bucket(""), "unknown")
        self.assertEqual(_recency_bucket("2026-04-01T00:00:00Z"), "last_7_days")
        self.assertIn(
            _recency_bucket("2026-03-20T00:00:00Z"),
            {"last_30_days", "older"},
        )

    async def test_queue_filters_candidates_by_provider_parent_rules_for_subtask(self):
        project = {
            "project_id": "proj-2000-2000",
            "integrations": {"jira": {"default_project_key": "PRM"}},
        }
        fake_docs = [
            {
                "external_id": "PRM-50",
                "title": "Task Parent",
                "provider_type": "Task",
                "work_item_type": "standard",
                "status": "todo",
                "updated_at": "2026-03-30T00:00:00Z",
            },
            {
                "external_id": "PRM-51",
                "title": "Story Parent",
                "provider_type": "Story",
                "work_item_type": "standard",
                "status": "todo",
                "updated_at": "2026-03-29T00:00:00Z",
            },
        ]
        metadata_doc = {
            "type_rules": {
                "subtask": {
                    "provider_type": "Subtask",
                    "parent": {
                        "required": True,
                        "allowed_parent_provider_types": ["Task"],
                    },
                }
            }
        }

        class _FakeCursor:
            def __init__(self, docs):
                self._docs = docs

            def sort(self, *_args, **_kwargs):
                return self

            def limit(self, *_args, **_kwargs):
                return self

            async def to_list(self, length=None):
                return list(self._docs[:length] if isinstance(length, int) else self._docs)

        class _FakeCollection:
            def find(self, *_args, **_kwargs):
                return _FakeCursor(fake_docs)

            async def find_one(self, *_args, **_kwargs):
                return metadata_doc

        with patch(
            "app.tasks.pending_parent_handler.get_brain_collection",
            return_value=_FakeCollection(),
        ), patch(
            "app.tasks.provider_parent_rules.get_brain_collection",
            return_value=_FakeCollection(),
        ), patch(
            "app.tasks.pending_parent_handler._rank_parent_candidates_with_llm",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.tasks.pending_parent_handler.create_pending_interaction",
            new=AsyncMock(return_value={"interaction_id": "pi-filter"}),
        ):
            result = await queue_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                actor_email="owner@example.com",
                user_message="create a subtask test",
                create_payload={
                    "provider": "jira",
                    "title": "Sub-task title",
                    "provider_type": "Subtask",
                    "work_item_type": "subtask",
                },
            )

        candidates = list(result.get("candidates") or [])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].get("external_id"), "PRM-50")

    async def test_queue_applies_confidence_threshold_before_building_candidate_pool(self):
        project = {
            "project_id": "proj-2000-2000",
            "integrations": {"jira": {"default_project_key": "PRM"}},
        }
        fake_docs = [
            {
                "external_id": "PRM-50",
                "title": "Task Parent",
                "provider_type": "Task",
                "work_item_type": "standard",
                "status": "todo",
                "updated_at": "2026-03-30T00:00:00Z",
            },
            {
                "external_id": "PRM-51",
                "title": "Story Parent",
                "provider_type": "Story",
                "work_item_type": "standard",
                "status": "todo",
                "updated_at": "2026-03-29T00:00:00Z",
            },
        ]
        metadata_doc = {
            "type_rules": {
                "subtask": {
                    "provider_type": "Subtask",
                    "parent": {
                        "required": True,
                        "allowed_parent_provider_types": ["Task", "Story"],
                    },
                }
            }
        }

        class _FakeCursor:
            def __init__(self, docs):
                self._docs = docs

            def sort(self, *_args, **_kwargs):
                return self

            def limit(self, *_args, **_kwargs):
                return self

            async def to_list(self, length=None):
                return list(self._docs[:length] if isinstance(length, int) else self._docs)

        class _FakeCollection:
            def find(self, *_args, **_kwargs):
                return _FakeCursor(fake_docs)

            async def find_one(self, *_args, **_kwargs):
                return metadata_doc

        with patch(
            "app.tasks.pending_parent_handler.get_brain_collection",
            return_value=_FakeCollection(),
        ), patch(
            "app.tasks.provider_parent_rules.get_brain_collection",
            return_value=_FakeCollection(),
        ), patch(
            "app.tasks.pending_parent_handler._rank_parent_candidates_with_llm",
            new=AsyncMock(
                return_value=[
                    {
                        "external_id": "PRM-51",
                        "title": "Story Parent",
                        "provider_type": "Story",
                        "status": "todo",
                        "confidence": 0.1,
                    }
                ]
            ),
        ), patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.tasks.pending_parent_handler.create_pending_interaction",
            new=AsyncMock(return_value={"interaction_id": "pi-threshold"}),
        ):
            result = await queue_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                actor_email="owner@example.com",
                user_message="create a subtask test",
                create_payload={
                    "provider": "jira",
                    "title": "Sub-task title",
                    "provider_type": "Subtask",
                    "work_item_type": "subtask",
                },
            )

        # Low-confidence ranked candidate is filtered out, then fallback keeps options available.
        candidates = list(result.get("candidates") or [])
        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual(candidates[0].get("external_id"), "PRM-50")

    async def test_queue_fills_candidate_pool_beyond_top3_when_llm_ranking_is_empty(self):
        project = {
            "project_id": "proj-2000-2000",
            "integrations": {"jira": {"default_project_key": "PRM"}},
        }
        fake_docs = [
            {
                "external_id": f"PRM-{50 + i}",
                "title": f"Candidate {i}",
                "provider_type": "Task" if i % 2 else "Story",
                "work_item_type": "standard",
                "status": "todo",
                "updated_at": "2026-03-30T00:00:00Z",
            }
            for i in range(1, 15)
        ]
        metadata_doc = {
            "type_rules": {
                "subtask": {
                    "provider_type": "Subtask",
                    "parent": {
                        "required": True,
                        "allowed_parent_provider_types": ["Task", "Story"],
                    },
                }
            }
        }

        class _FakeCursor:
            def __init__(self, docs):
                self._docs = docs

            def sort(self, *_args, **_kwargs):
                return self

            def limit(self, *_args, **_kwargs):
                return self

            async def to_list(self, length=None):
                return list(self._docs[:length] if isinstance(length, int) else self._docs)

        class _FakeCollection:
            def find(self, *_args, **_kwargs):
                return _FakeCursor(fake_docs)

            async def find_one(self, *_args, **_kwargs):
                return metadata_doc

        create_pending = AsyncMock(return_value={"interaction_id": "pi-fill"})
        with patch(
            "app.tasks.pending_parent_handler.get_brain_collection",
            return_value=_FakeCollection(),
        ), patch(
            "app.tasks.provider_parent_rules.get_brain_collection",
            return_value=_FakeCollection(),
        ), patch(
            "app.tasks.pending_parent_handler._rank_parent_candidates_with_llm",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.tasks.pending_parent_handler.create_pending_interaction",
            new=create_pending,
        ):
            result = await queue_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                actor_email="owner@example.com",
                user_message="create a subtask test",
                create_payload={
                    "provider": "jira",
                    "title": "Sub-task title",
                    "provider_type": "Subtask",
                    "work_item_type": "subtask",
                },
            )

        candidates = list(result.get("candidates") or [])
        self.assertEqual(len(candidates), 3)
        context_payload = create_pending.await_args.kwargs.get("context_payload") or {}
        candidate_pool = list(context_payload.get("candidate_pool") or [])
        self.assertEqual(len(candidate_pool), 14)

    async def test_queue_subtask_never_surfaces_epic_parent_even_if_metadata_lists_it(self):
        project = {
            "project_id": "proj-2000-2000",
            "integrations": {"jira": {"default_project_key": "PRM"}},
        }
        fake_docs = [
            {
                "external_id": "PRM-2",
                "title": "MVP",
                "provider_type": "Epic",
                "work_item_type": "epic",
                "status": "in_progress",
                "updated_at": "2026-03-30T00:00:00Z",
            },
            {
                "external_id": "PRM-50",
                "title": "Task Parent",
                "provider_type": "Task",
                "work_item_type": "standard",
                "status": "todo",
                "updated_at": "2026-03-30T00:00:00Z",
            },
        ]
        metadata_doc = {
            "allowed_create_types": ["Task", "Story", "Subtask", "Epic"],
            "type_rules": {
                "subtask": {
                    "provider_type": "Subtask",
                    "parent": {
                        "required": False,
                        "allowed_parent_provider_types": ["Task", "Epic"],
                    },
                }
            },
        }

        class _FakeCursor:
            def __init__(self, docs):
                self._docs = docs

            def sort(self, *_args, **_kwargs):
                return self

            def limit(self, *_args, **_kwargs):
                return self

            async def to_list(self, length=None):
                return list(self._docs[:length] if isinstance(length, int) else self._docs)

        class _FakeCollection:
            def find(self, *_args, **_kwargs):
                return _FakeCursor(fake_docs)

            async def find_one(self, *_args, **_kwargs):
                return metadata_doc

        with patch(
            "app.tasks.pending_parent_handler.get_brain_collection",
            return_value=_FakeCollection(),
        ), patch(
            "app.tasks.provider_parent_rules.get_brain_collection",
            return_value=_FakeCollection(),
        ), patch(
            "app.tasks.pending_parent_handler._rank_parent_candidates_with_llm",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.tasks.pending_parent_handler.create_pending_interaction",
            new=AsyncMock(return_value={"interaction_id": "pi-no-epic"}),
        ):
            result = await queue_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                actor_email="owner@example.com",
                user_message="create a subtask mvp milestone",
                create_payload={
                    "provider": "jira",
                    "title": "mvp milestone",
                    "provider_type": "Subtask",
                    "work_item_type": "subtask",
                },
            )

        candidates = list(result.get("candidates") or [])
        ext_ids = {str(item.get("external_id") or "").upper() for item in candidates}
        self.assertIn("PRM-50", ext_ids)
        self.assertNotIn("PRM-2", ext_ids)

    async def test_queue_excludes_closed_parents_from_suggestions(self):
        project = {
            "project_id": "proj-2000-2000",
            "integrations": {"jira": {"default_project_key": "PRM"}},
        }
        fake_docs = [
            {
                "external_id": "PRM-70",
                "title": "Open parent",
                "provider_type": "Task",
                "work_item_type": "standard",
                "status": "todo",
                "updated_at": "2026-03-30T00:00:00Z",
            },
            {
                "external_id": "PRM-71",
                "title": "Closed parent",
                "provider_type": "Task",
                "work_item_type": "standard",
                "status": "done",
                "updated_at": "2026-03-30T00:00:00Z",
            },
        ]
        metadata_doc = {
            "type_rules": {
                "subtask": {
                    "provider_type": "Subtask",
                    "parent": {
                        "required": True,
                        "allowed_parent_provider_types": ["Task"],
                    },
                }
            }
        }

        class _FakeCursor:
            def __init__(self, docs):
                self._docs = docs

            def sort(self, *_args, **_kwargs):
                return self

            def limit(self, *_args, **_kwargs):
                return self

            async def to_list(self, length=None):
                return list(self._docs[:length] if isinstance(length, int) else self._docs)

        class _FakeCollection:
            def find(self, *_args, **_kwargs):
                return _FakeCursor(fake_docs)

            async def find_one(self, *_args, **_kwargs):
                return metadata_doc

        create_pending = AsyncMock(return_value={"interaction_id": "pi-open-only"})
        with patch(
            "app.tasks.pending_parent_handler.get_brain_collection",
            return_value=_FakeCollection(),
        ), patch(
            "app.tasks.provider_parent_rules.get_brain_collection",
            return_value=_FakeCollection(),
        ), patch(
            "app.tasks.pending_parent_handler._rank_parent_candidates_with_llm",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.tasks.pending_parent_handler.create_pending_interaction",
            new=create_pending,
        ):
            result = await queue_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                actor_email="owner@example.com",
                user_message="create a subtask test",
                create_payload={
                    "provider": "jira",
                    "title": "Sub-task title",
                    "provider_type": "Subtask",
                    "work_item_type": "subtask",
                },
            )

        candidate_ids = {str(item.get("external_id") or "").upper() for item in (result.get("candidates") or [])}
        self.assertIn("PRM-70", candidate_ids)
        self.assertNotIn("PRM-71", candidate_ids)
        context_payload = create_pending.await_args.kwargs.get("context_payload") or {}
        pool_ids = {str(item.get("external_id") or "").upper() for item in (context_payload.get("candidate_pool") or [])}
        self.assertIn("PRM-70", pool_ids)
        self.assertNotIn("PRM-71", pool_ids)

    async def test_queue_reuses_existing_pending_for_equivalent_request(self):
        project = {
            "project_id": "proj-2000-2000",
            "integrations": {"jira": {"default_project_key": "PRM"}},
        }
        existing_pending = {
            "interaction_id": "pi-existing",
            "context_payload": {
                "create_payload": {
                    "provider": "jira",
                    "title": "Sub-task title",
                    "provider_type": "Subtask",
                    "work_item_type": "subtask",
                    "requested_assignee_hint": "sridevi",
                },
                "requested_assignee_hint": "sridevi",
                "candidate_pool": [
                    {"external_id": "PRM-91", "title": "Alpha", "provider_type": "Task", "status": "todo"},
                    {"external_id": "PRM-92", "title": "Beta", "provider_type": "Story", "status": "todo"},
                    {"external_id": "PRM-93", "title": "Gamma", "provider_type": "Epic", "status": "todo"},
                ],
                "view_mode": "top3",
                "view_offset": 0,
                "view_page_size": 10,
                "child_item_label": "subtask",
            },
        }

        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=existing_pending),
        ), patch(
            "app.tasks.pending_parent_handler.create_pending_interaction",
            new=AsyncMock(side_effect=AssertionError("should_not_create_new_pending")),
        ):
            result = await queue_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                actor_email="owner@example.com",
                user_message="create a subtask title and assign to sridevi",
                create_payload={
                    "provider": "jira",
                    "title": "Sub-task title",
                    "provider_type": "Subtask",
                    "work_item_type": "subtask",
                    "requested_assignee_hint": "sridevi",
                },
            )

        self.assertTrue(bool(result.get("queued")))
        self.assertEqual(result.get("interaction_id"), "pi-existing")
        self.assertIn(
            "most relevant parent options",
            str(result.get("response_text") or "").lower(),
        )
        self.assertEqual(len(list(result.get("candidates") or [])), 3)

    async def test_yes_reply_selects_first_candidate_and_returns_resume_payload(self):
        project = {"project_id": "proj-2000-2000"}
        pending = {
            "interaction_id": "pi-1",
            "context_payload": {
                "actor_email": "owner@example.com",
                "create_payload": {
                    "title": "Add retry guard",
                    "priority": "medium",
                    "provider_type": "Sub-task",
                    "work_item_type": "subtask",
                },
                "candidate_parents": [
                    {"external_id": "PRM-44", "title": "Auth flow"},
                    {"external_id": "PRM-45", "title": "Payments flow"},
                ],
            },
        }

        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.tasks.pending_parent_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ) as resolve_call:
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                text="yes",
            )

        self.assertFalse(bool(result.get("handled")))
        self.assertTrue(bool(result.get("resume_to_cortex")))
        self.assertIn("PRM-44", str(result.get("resume_message_text") or ""))
        self.assertIn("under PRM-44", str(result.get("resume_message_text") or ""))
        self.assertEqual(result.get("resume_target_refs"), ["PRM-44"])
        resolve_call.assert_awaited_once()

    async def test_explicit_key_reply_uses_given_parent_key(self):
        project = {"project_id": "proj-2000-2000"}
        pending = {
            "interaction_id": "pi-2",
            "context_payload": {
                "actor_email": "owner@example.com",
                "create_payload": {
                    "provider": "jira",
                    "title": "Fix timeout edge case",
                    "priority": "high",
                    "provider_type": "Sub-task",
                    "work_item_type": "subtask",
                },
                "candidate_parents": [
                    {"external_id": "PRM-44", "title": "Auth flow"},
                ],
            },
        }

        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.tasks.pending_parent_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ):
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                text="Use PRM-77",
            )

        self.assertFalse(bool(result.get("handled")))
        self.assertTrue(bool(result.get("resume_to_cortex")))
        self.assertIn("under PRM-77", str(result.get("resume_message_text") or ""))
        self.assertEqual(result.get("resume_target_refs"), ["PRM-77"])

    async def test_explicit_key_closed_parent_is_still_allowed(self):
        project = {"project_id": "proj-2000-2000"}
        pending = {
            "interaction_id": "pi-closed-key-ok",
            "context_payload": {
                "actor_email": "owner@example.com",
                "create_payload": {
                    "provider": "jira",
                    "title": "Fix timeout edge case",
                    "priority": "high",
                    "provider_type": "Sub-task",
                    "work_item_type": "subtask",
                },
                "candidate_parents": [
                    {"external_id": "PRM-44", "title": "Open parent"},
                ],
            },
        }

        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.tasks.pending_parent_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ):
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                text="Use PRM-71",
            )

        self.assertFalse(bool(result.get("handled")))
        self.assertTrue(bool(result.get("resume_to_cortex")))
        self.assertIn("under PRM-71", str(result.get("resume_message_text") or ""))

    async def test_explicit_key_not_available_returns_clarification_without_resume(self):
        project = {"project_id": "proj-2000-2000"}
        pending = {
            "interaction_id": "pi-parent-missing",
            "context_payload": {
                "actor_email": "owner@example.com",
                "create_payload": {
                    "provider": "jira",
                    "title": "Fix timeout edge case",
                    "priority": "high",
                    "provider_type": "Sub-task",
                    "work_item_type": "subtask",
                },
                "candidate_parents": [
                    {"external_id": "PRM-44", "title": "Open parent"},
                ],
            },
        }

        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.tasks.pending_parent_handler.resolve_parent_reference",
            new=AsyncMock(
                return_value={
                    "exists": False,
                    "source": "unverified",
                    "reason": "parent_not_found",
                    "parent_external_id": "VALUE1-2",
                }
            ),
        ), patch(
            "app.tasks.pending_parent_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ) as resolve_call:
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                text="Use value1-2",
            )

        self.assertTrue(bool(result.get("handled")))
        self.assertFalse(bool(result.get("resume_to_cortex")))
        response = str(result.get("response_text") or "").lower()
        self.assertIn("not available", response)
        self.assertIn("provide a valid parent work item key", response)
        resolve_call.assert_not_awaited()

    async def test_unclear_reply_does_not_auto_select_from_llm(self):
        project = {"project_id": "proj-2000-2000"}
        pending = {
            "interaction_id": "pi-llm-select",
            "context_payload": {
                "create_payload": {
                    "title": "Child task",
                    "priority": "medium",
                    "provider_type": "Sub-task",
                    "work_item_type": "subtask",
                },
                "candidate_parents": [
                    {"external_id": "PRM-44", "title": "Auth flow"},
                    {"external_id": "PRM-45", "title": "Payments flow"},
                ],
            },
        }
        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.tasks.pending_parent_handler._classify_followup_with_llm",
            new=AsyncMock(
                return_value={
                    "intent": "select",
                    "selected_external_id": "PRM-45",
                    "selected_option_index": 2,
                    "confidence": 0.92,
                }
            ),
        ), patch(
            "app.tasks.pending_parent_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ) as resolve_call:
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                text="what do you think",
            )
        self.assertTrue(bool(result.get("handled")))
        self.assertFalse(bool(result.get("resume_to_cortex")))
        self.assertIn("still need your confirmation", str(result.get("response_text") or "").lower())
        resolve_call.assert_not_awaited()

    async def test_new_intent_reply_falls_back_to_cortex(self):
        project = {"project_id": "proj-2000-2000"}
        pending = {
            "interaction_id": "pi-3",
            "context_payload": {
                "create_payload": {"title": "Add docs", "priority": "medium"},
                "candidate_parents": [{"external_id": "PRM-44", "title": "Auth flow"}],
            },
        }
        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ):
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                text="create a story for login page",
            )
        self.assertFalse(bool(result.get("handled")))

    async def test_show_more_updates_pending_context_and_returns_next_page(self):
        project = {"project_id": "proj-2000-2000"}
        pending = {
            "interaction_id": "pi-4",
            "context_payload": {
                "create_payload": {"title": "Add docs", "priority": "medium"},
                "candidate_pool": [
                    {"external_id": f"PRM-{40 + i}", "title": f"Task {i}"} for i in range(1, 15)
                ],
                "view_mode": "top3",
                "view_offset": 0,
                "view_page_size": 5,
            },
        }
        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.tasks.pending_parent_handler.update_pending_interaction_context",
            new=AsyncMock(return_value=True),
        ) as update_call:
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                text="show more",
            )

        self.assertTrue(bool(result.get("handled")))
        response = str(result.get("response_text") or "")
        self.assertIn("more relevant parent options", response.lower())
        self.assertIn("4. PRM-44", response)
        self.assertIn("8. PRM-48", response)
        kwargs = update_call.await_args.kwargs
        self.assertEqual(kwargs["interaction_id"], "pi-4")
        self.assertEqual(kwargs["context_payload"]["view_mode"], "paged")
        self.assertEqual(int(kwargs["context_payload"]["view_offset"]), 3)

    async def test_build_message_has_no_hardcoded_key_example(self):
        project = {"project_id": "proj-2000-2000"}
        pending = {
            "interaction_id": "pi-5",
            "context_payload": {
                "create_payload": {"title": "Add docs", "priority": "medium"},
                "candidate_parents": [{"external_id": "PRM-44", "title": "Auth flow"}],
            },
        }
        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ):
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                text="what are the options",
            )

        self.assertTrue(bool(result.get("handled")))
        response = str(result.get("response_text") or "")
        self.assertNotIn("PRM-123", response)

    async def test_top3_message_still_includes_list_all_when_only_three_candidates(self):
        project = {"project_id": "proj-2000-2000"}
        pending = {
            "interaction_id": "pi-6",
            "context_payload": {
                "create_payload": {"title": "Add docs", "priority": "medium"},
                "candidate_pool": [
                    {"external_id": "PRM-41", "title": "Task 1"},
                    {"external_id": "PRM-42", "title": "Task 2"},
                    {"external_id": "PRM-43", "title": "Task 3"},
                ],
                "view_mode": "top3",
                "view_offset": 0,
                "view_page_size": 5,
            },
        }
        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ):
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                text="what are the options",
            )

        self.assertTrue(bool(result.get("handled")))
        response = str(result.get("response_text") or "")
        self.assertIn("I still need your confirmation to continue.", response)

    async def test_show_more_with_only_top3_returns_no_more_options_message(self):
        project = {"project_id": "proj-2000-2000"}
        pending = {
            "interaction_id": "pi-6b",
            "context_payload": {
                "create_payload": {"title": "Add docs", "priority": "medium"},
                "candidate_pool": [
                    {"external_id": "PRM-41", "title": "Task 1"},
                    {"external_id": "PRM-42", "title": "Task 2"},
                    {"external_id": "PRM-43", "title": "Task 3"},
                ],
                "view_mode": "top3",
                "view_offset": 0,
                "view_page_size": 5,
            },
        }
        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ):
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                text="show more",
            )

        self.assertTrue(bool(result.get("handled")))
        response = str(result.get("response_text") or "").lower()
        self.assertIn("no more relevant parent options", response)

    async def test_absolute_option_number_selects_correct_candidate_in_paged_view(self):
        project = {"project_id": "proj-2000-2000"}
        pending = {
            "interaction_id": "pi-7",
            "context_payload": {
                "actor_email": "owner@example.com",
                "create_payload": {
                    "title": "Child task",
                    "priority": "medium",
                    "provider_type": "Sub-task",
                    "work_item_type": "subtask",
                },
                "candidate_pool": [
                    {"external_id": f"PRM-{40 + i}", "title": f"Task {i}"} for i in range(1, 15)
                ],
                "view_mode": "paged",
                "view_offset": 3,
                "view_page_size": 10,
            },
        }
        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.tasks.pending_parent_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ):
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                text="4",
            )
        self.assertIn("under PRM-44", str(result.get("resume_message_text") or ""))

    async def test_paged_view_numeric_one_selects_global_option_one(self):
        project = {"project_id": "proj-2000-2000"}
        pending = {
            "interaction_id": "pi-7b",
            "context_payload": {
                "actor_email": "owner@example.com",
                "create_payload": {
                    "title": "Child task",
                    "priority": "medium",
                    "provider_type": "Sub-task",
                    "work_item_type": "subtask",
                },
                "candidate_pool": [
                    {"external_id": f"PRM-{40 + i}", "title": f"Task {i}"} for i in range(1, 15)
                ],
                "view_mode": "paged",
                "view_offset": 3,
                "view_page_size": 10,
            },
        }
        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.tasks.pending_parent_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ):
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                text="1",
            )
        self.assertIn("under PRM-41", str(result.get("resume_message_text") or ""))

    async def test_invalid_numeric_option_returns_range_guidance(self):
        project = {"project_id": "proj-2000-2000"}
        pending = {
            "interaction_id": "pi-7c",
            "context_payload": {
                "create_payload": {"title": "Child task", "priority": "medium"},
                "candidate_pool": [
                    {"external_id": "PRM-41", "title": "Task 1"},
                    {"external_id": "PRM-42", "title": "Task 2"},
                    {"external_id": "PRM-43", "title": "Task 3"},
                ],
                "view_mode": "paged",
                "view_offset": 0,
                "view_page_size": 2,
            },
        }
        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ):
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="u-123",
                text="99",
            )
        self.assertTrue(bool(result.get("handled")))
        response = str(result.get("response_text") or "")
        self.assertIn("between 1 and 3", response)

    async def test_pending_parent_selection_preserves_assign_to_me_intent_in_resume(self):
        project = {"project_id": "proj-2000-2000", "members": []}
        pending = {
            "interaction_id": "pi-8",
            "context_payload": {
                "actor_email": "owner@example.com",
                "requested_user_message": "create a sub task draw aws architecture and assign to me",
                "create_payload": {
                    "title": "draw aws architecture",
                    "priority": "medium",
                    "provider_type": "Sub-task",
                    "work_item_type": "subtask",
                },
                "candidate_parents": [
                    {"external_id": "PRM-6", "title": "Integrations and Architecture"},
                    {"external_id": "PRM-1", "title": "Foundation"},
                ],
            },
        }
        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.tasks.pending_parent_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ):
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="U-SLACK-1",
                text="1",
            )
        self.assertFalse(bool(result.get("handled")))
        self.assertTrue(bool(result.get("resume_to_cortex")))
        self.assertIn("assign to me", str(result.get("resume_message_text") or "").lower())

    async def test_pending_parent_selection_prefers_stored_assignee_hint(self):
        project = {"project_id": "proj-2000-2000", "members": []}
        pending = {
            "interaction_id": "pi-9",
            "context_payload": {
                "requested_user_message": "create subtask for demo testing and assign to sridevi",
                "create_payload": {
                    "title": "demo testing",
                    "priority": "medium",
                    "provider_type": "Sub-task",
                    "work_item_type": "subtask",
                    "requested_assignee_hint": "Sridevi Prabhu",
                },
                "candidate_parents": [{"external_id": "PRM-48", "title": "Demo Parent"}],
            },
        }
        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.tasks.pending_parent_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ):
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="U-SLACK-1",
                text="1",
            )
        self.assertFalse(bool(result.get("handled")))
        self.assertTrue(bool(result.get("resume_to_cortex")))
        self.assertIn("assign to sridevi prabhu", str(result.get("resume_message_text") or "").lower())

    async def test_pending_parent_selection_does_not_extract_assignee_from_for_phrase(self):
        project = {"project_id": "proj-2000-2000", "members": []}
        pending = {
            "interaction_id": "pi-10",
            "context_payload": {
                "requested_user_message": "create sub task for demo testing",
                "create_payload": {
                    "title": "demo testing",
                    "priority": "medium",
                    "provider_type": "Sub-task",
                    "work_item_type": "subtask",
                },
                "candidate_parents": [{"external_id": "PRM-48", "title": "Demo Parent"}],
            },
        }
        with patch(
            "app.tasks.pending_parent_handler.find_pending_interaction",
            new=AsyncMock(return_value=pending),
        ), patch(
            "app.tasks.pending_parent_handler.resolve_pending_interaction",
            new=AsyncMock(return_value=True),
        ):
            result = await handle_pending_work_item_parent_clarification(
                project=project,
                actor_user_id="U-SLACK-1",
                text="1",
            )
        self.assertFalse(bool(result.get("handled")))
        self.assertTrue(bool(result.get("resume_to_cortex")))
        self.assertNotIn("assign to demo testing", str(result.get("resume_message_text") or "").lower())


if __name__ == "__main__":
    unittest.main()
