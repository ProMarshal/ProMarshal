import unittest
from unittest.mock import AsyncMock, patch

from app.action_items import extraction as extraction_module
from app.action_items.title_extractor import ActionItemExtractedCandidate


class ActionItemExtractionJobTests(unittest.TestCase):
    def test_status_only_prefilter_covers_regional_phrasing(self):
        self.assertTrue(extraction_module._looks_like_status_only_comment("done from my side"))
        self.assertTrue(extraction_module._looks_like_status_only_comment("needful done"))
        self.assertTrue(extraction_module._looks_like_status_only_comment("fixed"))
        self.assertTrue(extraction_module._looks_like_status_only_comment("fixed in latest build"))
        self.assertTrue(extraction_module._looks_like_status_only_comment("fyi deployed"))
        self.assertTrue(extraction_module._looks_like_status_only_comment("working on this"))

    def test_explicit_external_cues_cover_varied_sentence_styles(self):
        self.assertTrue(extraction_module._has_explicit_external_action_cue("pls chase infra team for access"))
        self.assertTrue(
            extraction_module._has_explicit_external_action_cue(
                "kindly remind rahul to share deck by eod"
            )
        )
        self.assertTrue(extraction_module._has_explicit_external_action_cue("check with finance and revert"))
        self.assertTrue(extraction_module._has_explicit_external_action_cue("loop in ops for deployment call"))
        self.assertFalse(extraction_module._has_explicit_external_action_cue("please check this"))

    def test_task_comment_job_skips_invalid_canonical_event(self):
        result = extraction_module.process_action_item_extraction_job(
            {
                "source_type": "task_comment_mutation",
                "project_id": "proj-1234-5678",
                "comment_event": {
                    "provider": "jira",
                    "project_id": "proj-1234-5678",
                    "comment_text": "Please follow up",
                    # task_key intentionally missing
                },
            }
        )
        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("created"), False)
        self.assertEqual(result.get("reason"), "invalid_comment_event")

    def test_task_comment_job_skips_when_actor_unresolved(self):
        result = extraction_module.process_action_item_extraction_job(
            {
                "source_type": "task_comment_mutation",
                "project_id": "proj-1234-5678",
                "task_key": "ABC-1",
                "posted_comment_text": "Please follow up",
                "actor_user_id": "",
            }
        )
        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("created"), False)
        self.assertEqual(result.get("reason"), "actor_unresolved")

    def test_task_comment_job_uses_timestamp_in_dedupe_key(self):
        create_mock = AsyncMock(return_value={"action_item_id": "ai-1"})
        candidate = ActionItemExtractedCandidate(
            title="Please follow up",
            owner_hint=None,
            due_type="no_due_date",
            due_date=None,
            related_to=None,
            source="deterministic",
            confidence=0.55,
        )
        extract_mock = AsyncMock(return_value=candidate)
        with patch.object(extraction_module.ActionItemService, "create", new=create_mock), patch.object(
            extraction_module,
            "resolve_action_item_candidate",
            new=extract_mock,
        ):
            result = extraction_module.process_action_item_extraction_job(
                {
                    "source_type": "task_comment_mutation",
                    "project_id": "proj-1234-5678",
                    "task_key": "ABC-1",
                    "posted_comment_text": "Please follow up",
                    "actor_user_id": "u-1",
                    "mutation_timestamp": "2026-03-14T12:00:00Z",
                }
            )

        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("created"), True)
        create_mock.assert_awaited_once()
        payload = create_mock.await_args.args[1]
        dedupe_key = str(payload.get("source_event_key") or "")
        self.assertTrue(dedupe_key.startswith("comment:ABC-1:"))
        self.assertTrue(dedupe_key.endswith(":2026-03-14T12:00:00Z"))
        self.assertEqual(payload.get("title"), "Please follow up (ABC-1)")

    def test_task_comment_job_prefers_canonical_source_event_key(self):
        create_mock = AsyncMock(return_value={"action_item_id": "ai-2"})
        candidate = ActionItemExtractedCandidate(
            title="Please follow up",
            owner_hint=None,
            due_type="no_due_date",
            due_date=None,
            related_to=None,
            source="deterministic",
            confidence=0.55,
        )
        extract_mock = AsyncMock(return_value=candidate)
        with patch.object(extraction_module.ActionItemService, "create", new=create_mock), patch.object(
            extraction_module,
            "resolve_action_item_candidate",
            new=extract_mock,
        ):
            result = extraction_module.process_action_item_extraction_job(
                {
                    "source_type": "task_comment_mutation",
                    "project_id": "proj-1234-5678",
                    "comment_event": {
                        "provider": "jira",
                        "source": "webhook_comment_create",
                        "project_id": "proj-1234-5678",
                        "task_key": "ABC-1",
                        "comment_text": "Please follow up",
                        "actor_user_id": "u-1",
                        "mutation_timestamp": "2026-03-14T12:00:00Z",
                        "source_event_key": "jira:comment:abc123",
                    },
                }
            )

        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("created"), True)
        create_mock.assert_awaited_once()
        payload = create_mock.await_args.args[1]
        self.assertEqual(payload.get("source_event_key"), "jira:comment:abc123")

    def test_task_comment_job_skips_status_only_comment(self):
        extract_mock = AsyncMock()
        with patch.object(
            extraction_module,
            "resolve_action_item_candidate",
            new=extract_mock,
        ):
            result = extraction_module.process_action_item_extraction_job(
                {
                    "source_type": "task_comment_mutation",
                    "project_id": "proj-1234-5678",
                    "task_key": "ABC-1",
                    "raw_comment_text": "completed",
                    "posted_comment_text": "[Updated by x via ProMarshal]\ncompleted",
                    "actor_user_id": "u-1",
                }
            )

        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("created"), False)
        self.assertEqual(result.get("reason"), "prefilter_status_only")
        extract_mock.assert_not_awaited()

    def test_task_comment_job_skips_when_classifier_negative(self):
        create_mock = AsyncMock()
        with patch.object(extraction_module.ActionItemService, "create", new=create_mock), patch.object(
            extraction_module,
            "_llm_is_action_item_comment",
            new=AsyncMock(return_value={"is_action_item": False, "confidence": 0.2, "reason": "status_update"}),
        ), patch.object(
            extraction_module.settings,
            "action_items_comment_classifier_enabled",
            True,
        ):
            result = extraction_module.process_action_item_extraction_job(
                {
                    "source_type": "task_comment_mutation",
                    "project_id": "proj-1234-5678",
                    "task_key": "ABC-1",
                    "raw_comment_text": "please check",
                    "posted_comment_text": "please check",
                    "actor_user_id": "u-1",
                }
            )

        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("created"), False)
        self.assertEqual(result.get("reason"), "status_update")
        create_mock.assert_not_awaited()

    def test_task_comment_job_creates_when_explicit_external_cue_present(self):
        create_mock = AsyncMock(return_value={"action_item_id": "ai-3"})
        candidate = ActionItemExtractedCandidate(
            title="Ask Sridevi to test again",
            owner_hint="Sridevi",
            due_type="no_due_date",
            due_date=None,
            related_to=None,
            source="deterministic",
            confidence=0.55,
        )
        with patch.object(extraction_module.ActionItemService, "create", new=create_mock), patch.object(
            extraction_module,
            "resolve_action_item_candidate",
            new=AsyncMock(return_value=candidate),
        ), patch.object(
            extraction_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"project_doc": {}}),
        ), patch.object(
            extraction_module,
            "_llm_is_action_item_comment",
            new=AsyncMock(return_value={"is_action_item": False, "confidence": 0.2, "reason": "should_not_be_called"}),
        ):
            result = extraction_module.process_action_item_extraction_job(
                {
                    "source_type": "task_comment_mutation",
                    "project_id": "proj-1234-5678",
                    "task_key": "ABC-1",
                    "raw_comment_text": "ask sridevi to test again",
                    "posted_comment_text": "ask sridevi to test again",
                    "actor_user_id": "u-1",
                }
            )

        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("created"), True)
        create_mock.assert_awaited_once()

    def test_task_comment_job_creates_when_classifier_positive_multilingual(self):
        create_mock = AsyncMock(return_value={"action_item_id": "ai-4"})
        candidate = ActionItemExtractedCandidate(
            title="Remind Juan to share the report",
            owner_hint="Juan",
            due_type="no_due_date",
            due_date=None,
            related_to=None,
            source="deterministic",
            confidence=0.72,
        )
        with patch.object(extraction_module.ActionItemService, "create", new=create_mock), patch.object(
            extraction_module,
            "resolve_action_item_candidate",
            new=AsyncMock(return_value=candidate),
        ), patch.object(
            extraction_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"project_doc": {}}),
        ), patch.object(
            extraction_module,
            "_llm_is_action_item_comment",
            new=AsyncMock(return_value={"is_action_item": True, "confidence": 0.91, "reason": "follow_up_request"}),
        ), patch.object(
            extraction_module.settings,
            "action_items_comment_classifier_enabled",
            True,
        ):
            result = extraction_module.process_action_item_extraction_job(
                {
                    "source_type": "task_comment_mutation",
                    "project_id": "proj-1234-5678",
                    "task_key": "ABC-1",
                    "raw_comment_text": "por favor recuerda a juan enviar el reporte",
                    "posted_comment_text": "por favor recuerda a juan enviar el reporte",
                    "actor_user_id": "u-1",
                }
            )

        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("created"), True)
        create_mock.assert_awaited_once()

    def test_task_comment_job_resolves_owner_from_name_to_action_phrase(self):
        create_mock = AsyncMock(return_value={"action_item_id": "ai-5"})
        candidate = ActionItemExtractedCandidate(
            title="Sridevi to connect with DevOps team and get necessary access",
            owner_hint=None,
            due_type="no_due_date",
            due_date=None,
            related_to=None,
            source="deterministic",
            confidence=0.55,
        )
        project_doc = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "email": "owner@example.com", "status": "active", "name": "Owner"},
                {"user_id": "u-sridevi", "email": "sridevi@example.com", "status": "active", "name": "Sridevi"},
            ],
        }
        with patch.object(extraction_module.ActionItemService, "create", new=create_mock), patch.object(
            extraction_module,
            "resolve_action_item_candidate",
            new=AsyncMock(return_value=candidate),
        ), patch.object(
            extraction_module.ActionItemService,
            "_resolve_project_context",
            new=AsyncMock(return_value={"project_doc": project_doc}),
        ), patch.object(
            extraction_module,
            "_llm_is_action_item_comment",
            new=AsyncMock(return_value={"is_action_item": True, "confidence": 0.9, "reason": "follow_up_request"}),
        ), patch.object(
            extraction_module.settings,
            "action_items_comment_classifier_enabled",
            True,
        ):
            result = extraction_module.process_action_item_extraction_job(
                {
                    "source_type": "task_comment_mutation",
                    "project_id": "proj-1234-5678",
                    "task_key": "PRM-80",
                    "raw_comment_text": "sridevi to connect with devops team and get necessary access",
                    "posted_comment_text": "sridevi to connect with devops team and get necessary access",
                    "actor_user_id": "u-owner",
                }
            )

        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("created"), True)
        create_mock.assert_awaited_once()
        payload = create_mock.await_args.args[1]
        self.assertEqual(payload.get("owner_user_id"), "u-sridevi")

    def test_task_comment_job_skips_when_classifier_negative_multilingual(self):
        create_mock = AsyncMock()
        with patch.object(extraction_module.ActionItemService, "create", new=create_mock), patch.object(
            extraction_module,
            "_llm_is_action_item_comment",
            new=AsyncMock(return_value={"is_action_item": False, "confidence": 0.88, "reason": "status_update"}),
        ), patch.object(
            extraction_module.settings,
            "action_items_comment_classifier_enabled",
            True,
        ):
            result = extraction_module.process_action_item_extraction_job(
                {
                    "source_type": "task_comment_mutation",
                    "project_id": "proj-1234-5678",
                    "task_key": "ABC-1",
                    "raw_comment_text": "arreglado y desplegado",
                    "posted_comment_text": "arreglado y desplegado",
                    "actor_user_id": "u-1",
                }
            )

        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("created"), False)
        self.assertEqual(result.get("reason"), "status_update")
        create_mock.assert_not_awaited()

    def test_task_comment_job_skips_when_project_not_found(self):
        candidate = ActionItemExtractedCandidate(
            title="Please email ops",
            owner_hint=None,
            due_type="no_due_date",
            due_date=None,
            related_to=None,
            source="deterministic",
            confidence=0.55,
        )
        with patch.object(
            extraction_module.ActionItemService,
            "create",
            new=AsyncMock(side_effect=ValueError("Project not found")),
        ), patch.object(
            extraction_module,
            "resolve_action_item_candidate",
            new=AsyncMock(return_value=candidate),
        ), patch.object(
            extraction_module,
            "_llm_is_action_item_comment",
            new=AsyncMock(return_value={"is_action_item": True, "confidence": 0.95, "reason": "follow_up_request"}),
        ), patch.object(
            extraction_module.settings,
            "action_items_comment_classifier_enabled",
            True,
        ):
            result = extraction_module.process_action_item_extraction_job(
                {
                    "source_type": "task_comment_mutation",
                    "project_id": "proj-1234-5678",
                    "task_key": "ABC-1",
                    "raw_comment_text": "please email ops",
                    "posted_comment_text": "please email ops",
                    "actor_user_id": "u-1",
                }
            )

        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("created"), False)
        self.assertEqual(result.get("reason"), "project_not_found")


if __name__ == "__main__":
    unittest.main()
