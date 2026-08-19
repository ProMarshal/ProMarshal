import importlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


jira_webhooks_module = importlib.import_module("app.routes.jira_webhooks")


class JiraWebhookRecommendationDirtyTests(unittest.TestCase):
    def test_resolve_task_key_prefers_issue_key(self):
        issue = {"key": "ABC-1"}
        normalized = {"external_id": "FALLBACK-1"}
        resolved = jira_webhooks_module._resolve_task_key(issue, normalized)
        self.assertEqual(resolved, "ABC-1")

    def test_resolve_task_key_falls_back_to_normalized_external_id(self):
        issue = {"id": "10001"}
        normalized = {"external_id": "ABC-2"}
        resolved = jira_webhooks_module._resolve_task_key(issue, normalized)
        self.assertEqual(resolved, "ABC-2")

    def test_mark_task_dirty_best_effort_swallows_errors(self):
        with patch(
            "app.routes.jira_webhooks.mark_project_task_dirty",
            side_effect=RuntimeError("redis down"),
        ):
            jira_webhooks_module._mark_task_dirty_best_effort("proj-1111-2222", "ABC-3")

    def test_detects_comment_create_event(self):
        self.assertTrue(
            jira_webhooks_module._is_jira_comment_create_event(
                {"webhookEvent": "comment_created"}
            )
        )
        self.assertTrue(
            jira_webhooks_module._is_jira_comment_create_event(
                {"issue_event_type_name": "issue_commented"}
            )
        )
        self.assertFalse(
            jira_webhooks_module._is_jira_comment_create_event(
                {"webhookEvent": "jira:issue_updated", "issue_event_type_name": "issue_updated"}
            )
        )

    def test_extracts_comment_author_and_resolves_actor(self):
        payload = {
            "comment": {
                "author": {
                    "accountId": "acc-1",
                    "emailAddress": "member@example.com",
                    "displayName": "Member One",
                }
            }
        }
        author = jira_webhooks_module._extract_jira_comment_author(payload)
        self.assertEqual(author["account_id"], "acc-1")
        self.assertEqual(author["email"], "member@example.com")

        project = {
            "members": [
                {
                    "user_id": "u-1",
                    "status": "active",
                    "email": "member@example.com",
                    "name": "Member One",
                    "integration_ids": {"jira_account_id": "acc-1"},
                }
            ]
        }
        resolved = jira_webhooks_module._resolve_actor_user_id_from_jira_author(project, author)
        self.assertEqual(resolved, "u-1")

    def test_builds_source_event_key_from_comment_id(self):
        payload = {"comment": {"id": "9001"}}
        key = jira_webhooks_module._build_jira_webhook_comment_source_event_key(payload, "ABC-9")
        self.assertEqual(key, "jira:comment:ABC-9:9001")


class JiraWebhookActionItemExtractionTriggerTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_webhook_enqueues_action_item_extraction_for_comment_create(self):
        payload = {
            "webhookEvent": "comment_created",
            "issue_event_type_name": "issue_commented",
            "issue": {
                "key": "ABC-42",
                "self": "https://project-pseudo.atlassian.net/rest/api/2/issue/12345",
                "fields": {
                    "project": {"key": "ABC"},
                    "updated": "2026-03-17T11:00:00.000+0530",
                },
            },
            "comment": {
                "id": "c-101",
                "body": "Please ask ops to verify network telemetry",
                "created": "2026-03-17T10:58:00.000+0530",
                "updated": "2026-03-17T10:59:00.000+0530",
                "author": {
                    "accountId": "acc-1",
                    "emailAddress": "member@example.com",
                    "displayName": "Member One",
                },
            },
        }

        project_doc = {
            "_id": "mongo-1",
            "project_id": "proj-1234-5678",
            "project_name": "Test Project",
            "integrations": {"jira": {"site_url": "https://project-pseudo.atlassian.net", "cloud_id": "cloud-1"}},
            "members": [
                {
                    "user_id": "u-1",
                    "status": "active",
                    "email": "member@example.com",
                    "name": "Member One",
                    "integration_ids": {"jira_account_id": "acc-1"},
                }
            ],
        }

        fake_projects = MagicMock()
        fake_projects.find_one = AsyncMock(return_value=project_doc)
        fake_db = AsyncMock()
        fake_db.projects = fake_projects

        fake_brain = AsyncMock()
        fake_brain.update_one = AsyncMock(return_value=AsyncMock(upserted_id=None, modified_count=1))

        normalizer_instance = MagicMock()
        normalizer_instance.normalize_task.return_value = {
            "external_id": "ABC-42",
            "entity_type": "task",
            "issue_type": "Task",
            "status": "in_progress",
            "assignee_name": "Member One",
            "assignee_email": "member@example.com",
        }

        with patch("app.routes.jira_webhooks.get_database", return_value=fake_db), patch(
            "app.routes.jira_webhooks.get_brain_collection", return_value=fake_brain
        ), patch(
            "app.routes.jira_webhooks.JiraNormalizer", return_value=normalizer_instance
        ), patch(
            "app.routes.jira_webhooks._mark_task_dirty_best_effort"
        ), patch(
            "app.routes.jira_webhooks.invalidate_pm_board_summary_cache"
        ), patch.object(
            jira_webhooks_module.settings, "action_items_enabled", True
        ), patch.object(
            jira_webhooks_module.settings, "action_items_auto_detect_enabled", True
        ), patch(
            "app.routes.jira_webhooks.enqueue_from_task_comment_mutation",
            return_value={"queued": True, "job_id": "job-1"},
        ) as enqueue_mock:
            await jira_webhooks_module.process_jira_webhook(payload, project_id="proj-1234-5678")

        enqueue_mock.assert_called_once()
        kwargs = enqueue_mock.call_args.kwargs
        self.assertEqual(kwargs["project_id"], "proj-1234-5678")
        self.assertEqual(kwargs["task_key"], "ABC-42")
        self.assertEqual(kwargs["provider"], "jira")
        self.assertEqual(kwargs["source"], "jira_webhook_comment_create")
        self.assertEqual(kwargs["source_event_key"], "jira:comment:ABC-42:c-101")
        self.assertEqual(kwargs["actor_user_id"], "u-1")
        update_call = fake_brain.update_one.await_args_list[0]
        set_payload = update_call.args[1]["$set"]
        self.assertEqual(set_payload.get("entity_type"), "work_item")
        self.assertEqual(set_payload.get("provider_type"), "Task")
        self.assertEqual(set_payload.get("work_item_type"), "standard")

    async def test_process_webhook_sends_assignment_notification_after_upsert(self):
        payload = {
            "webhookEvent": "jira:issue_updated",
            "issue_event_type_name": "issue_updated",
            "issue": {
                "key": "ABC-77",
                "fields": {
                    "project": {"key": "ABC"},
                    "updated": "2026-03-17T11:00:00.000+0530",
                },
            },
        }
        project_doc = {
            "_id": "mongo-1",
            "project_id": "proj-1234-5678",
            "project_name": "Test Project",
            "integrations": {"jira": {"site_url": "https://project-pseudo.atlassian.net", "cloud_id": "cloud-1"}},
            "members": [],
        }
        fake_projects = MagicMock()
        fake_projects.find_one = AsyncMock(return_value=project_doc)
        fake_db = AsyncMock()
        fake_db.projects = fake_projects

        existing_task = {
            "integration_type": "jira",
            "external_id": "ABC-77",
            "assignee_email": "old@example.com",
            "assignee_name": "Old User",
        }
        fake_brain = AsyncMock()
        fake_brain.find_one = AsyncMock(return_value=existing_task)
        fake_brain.update_one = AsyncMock(return_value=AsyncMock(upserted_id=None, modified_count=1))

        normalizer_instance = MagicMock()
        normalized_task = {
            "external_id": "ABC-77",
            "entity_type": "work_item",
            "provider_type": "Task",
            "work_item_type": "standard",
            "status": "todo",
            "assignee_name": "New User",
            "assignee_email": "new@example.com",
        }
        normalizer_instance.normalize_task.return_value = normalized_task

        with patch("app.routes.jira_webhooks.get_database", return_value=fake_db), patch(
            "app.routes.jira_webhooks.get_brain_collection", return_value=fake_brain
        ), patch(
            "app.routes.jira_webhooks.JiraNormalizer", return_value=normalizer_instance
        ), patch(
            "app.routes.jira_webhooks._mark_task_dirty_best_effort"
        ), patch(
            "app.routes.jira_webhooks.invalidate_pm_board_summary_cache"
        ), patch(
            "app.routes.jira_webhooks._trigger_pm_summary_recompute_best_effort"
        ), patch(
            "app.routes.jira_webhooks.send_work_item_assignment_notification",
            new=AsyncMock(return_value=True),
        ) as notify_mock:
            await jira_webhooks_module.process_jira_webhook(payload, project_id="proj-1234-5678")

        notify_mock.assert_awaited_once()
        self.assertEqual(notify_mock.await_args.kwargs["old_task"], existing_task)
        self.assertEqual(notify_mock.await_args.kwargs["task"]["assignee_email"], "new@example.com")
        self.assertIsNone(notify_mock.await_args.kwargs["actor_hint"])


if __name__ == "__main__":
    unittest.main()
