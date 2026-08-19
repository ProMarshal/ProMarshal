import importlib
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from app.tasks.schemas import UpdateTaskDTO


session_store_module = importlib.import_module("app.cadence.session_store")
task_service_module = importlib.import_module("app.tasks.service")


class ActionItemCadenceTriggerTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_session_summary_enqueues_action_item_extraction(self):
        session = {
            "session_id": "sid-1",
            "project_id": "proj-1234-5678",
            "user_id": "u-member",
            "member_name": "Member",
            "tasks": [],
            "backlog_prompt_sent": False,
            "backlog_prompt_skipped_by_policy": False,
            "backlog_prompt_skip_reason": None,
            "backlog_prompt_active_workload_count": None,
            "backlog_prompt_threshold": None,
            "created_at": datetime.utcnow(),
            "completed_at": datetime.utcnow(),
        }

        with patch.object(
            session_store_module,
            "get_session",
            new=AsyncMock(return_value=session),
        ), patch.object(
            session_store_module,
            "_persist_session_summary",
            new=AsyncMock(return_value=True),
        ), patch.object(
            session_store_module.settings,
            "action_items_enabled",
            True,
        ), patch.object(
            session_store_module.settings,
            "action_items_auto_detect_enabled",
            True,
        ), patch(
            "app.action_items.extraction.enqueue_from_cadence_session_summary",
            return_value={"queued": True, "job_id": "job-1"},
        ) as enqueue_mock:
            success = await session_store_module.build_and_store_session_summary(
                db=object(),
                session_id="sid-1",
                reason="completed",
            )

        self.assertTrue(success)
        enqueue_mock.assert_called_once()
        kwargs = enqueue_mock.call_args.kwargs
        self.assertEqual(kwargs["project_id"], "proj-1234-5678")
        self.assertEqual(kwargs["session_id"], "sid-1")
        self.assertEqual(kwargs["actor_user_id"], "u-member")


class ActionItemCortexCommentTriggerTests(unittest.IsolatedAsyncioTestCase):
    async def test_mutate_task_enqueues_comment_extraction_for_promarshal_source(self):
        project = {
            "project_id": "proj-1234-5678",
            "project_name": "Test Project",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-member", "email": "member@example.com", "role": "member", "status": "active"}
            ],
        }
        adapter = AsyncMock()
        adapter.mutate_task = AsyncMock(
            return_value=(
                {
                    "external_id": "ABC-1",
                    "title": "Task",
                    "status": "in_progress",
                    "url": "https://example.com/ABC-1",
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                },
                False,
                True,
                "Please follow up with ops on network checks",
                False,
                False,
                False,
                False,
                False,
                None,
            )
        )

        with patch.object(
            task_service_module,
            "JiraAdapter",
            return_value=adapter,
        ), patch.object(
            task_service_module.BrainSync,
            "sync_task_to_brain",
            new=AsyncMock(return_value=None),
        ), patch.object(
            task_service_module.settings,
            "action_items_enabled",
            True,
        ), patch.object(
            task_service_module.settings,
            "action_items_auto_detect_enabled",
            True,
        ), patch(
            "app.action_items.extraction.resolve_actor_user_id_from_project",
            return_value="u-member",
        ), patch(
            "app.action_items.extraction.enqueue_from_task_comment_mutation",
            return_value={"queued": True, "job_id": "job-2"},
        ) as enqueue_mock:
            result = await task_service_module.TaskService.mutate_task(
                task_data=UpdateTaskDTO(
                    task_id="ABC-1",
                    project_id="proj-1234-5678",
                    comment="Please follow up with ops on network checks",
                ),
                project=project,
                updated_by="member@example.com",
                source_label="promarshal",
            )

        self.assertTrue(result.comment_added)
        enqueue_mock.assert_called_once()
        kwargs = enqueue_mock.call_args.kwargs
        self.assertEqual(kwargs["project_id"], "proj-1234-5678")
        self.assertEqual(kwargs["task_key"], "ABC-1")
        self.assertEqual(kwargs["actor_user_id"], "u-member")
        self.assertIn("ops", kwargs["posted_comment_text"].lower())
        self.assertTrue(kwargs["mutation_timestamp"])

    async def test_mutate_task_does_not_enqueue_for_auto_status_comment_without_user_comment(self):
        project = {
            "project_id": "proj-1234-5678",
            "project_name": "Test Project",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-member", "email": "member@example.com", "role": "member", "status": "active"}
            ],
        }
        adapter = AsyncMock()
        adapter.mutate_task = AsyncMock(
            return_value=(
                {
                    "external_id": "ABC-1",
                    "title": "Task",
                    "status": "done",
                    "url": "https://example.com/ABC-1",
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                },
                True,
                True,
                "Status updated to done by ProMarshal",
                False,
                False,
                False,
                False,
                False,
                None,
            )
        )

        with patch.object(
            task_service_module,
            "JiraAdapter",
            return_value=adapter,
        ), patch.object(
            task_service_module.BrainSync,
            "sync_task_to_brain",
            new=AsyncMock(return_value=None),
        ), patch.object(
            task_service_module.settings,
            "action_items_enabled",
            True,
        ), patch.object(
            task_service_module.settings,
            "action_items_auto_detect_enabled",
            True,
        ), patch(
            "app.action_items.extraction.enqueue_from_task_comment_mutation",
            return_value={"queued": True, "job_id": "job-2"},
        ) as enqueue_mock:
            result = await task_service_module.TaskService.mutate_task(
                task_data=UpdateTaskDTO(
                    task_id="ABC-1",
                    project_id="proj-1234-5678",
                    status="done",
                ),
                project=project,
                updated_by="member@example.com",
                source_label="promarshal",
                add_status_auto_comment=True,
            )

        self.assertTrue(result.comment_added)
        enqueue_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
