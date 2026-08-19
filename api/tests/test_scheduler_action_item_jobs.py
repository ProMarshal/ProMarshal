import unittest
from unittest.mock import patch

from app.core.config import settings
from app.action_items import reminders as action_item_reminders
from app.scheduler.executors import EXECUTOR_REGISTRY
from app.scheduler import service as scheduler_service


class _FakeProjectsCursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, length=None):
        return list(self._rows)


class _FakeProjectsCollection:
    def __init__(self, rows):
        self._rows = rows

    def find(self, *args, **kwargs):
        return _FakeProjectsCursor(self._rows)


class _FakeRepo:
    instances = []

    def __init__(self, db):
        self.db = db
        self.upserts = []
        self.existing = {}
        _FakeRepo.instances.append(self)

    async def ensure_indexes(self):
        return None

    async def upsert_schedule(self, *, job_type, project_id, schedule_spec, next_run_at, enabled):
        self.upserts.append(
            {
                "job_type": job_type,
                "project_id": project_id,
                "schedule_spec": schedule_spec,
                "next_run_at": next_run_at,
                "enabled": enabled,
            }
        )
        return {"job_type": job_type, "project_id": project_id}

    async def get_schedule(self, *, job_type, project_id):
        return self.existing.get((job_type, project_id))


class _FakeDB:
    def __init__(self, project_rows):
        self.projects = _FakeProjectsCollection(project_rows)


class SchedulerActionItemJobsTests(unittest.IsolatedAsyncioTestCase):
    def test_executor_registry_contains_action_item_jobs(self):
        self.assertIn("action_item_digest", EXECUTOR_REGISTRY)
        self.assertIn("action_item_followup_escalation", EXECUTOR_REGISTRY)

    async def test_ensure_action_item_schedules_for_projects_seeds_digest_and_escalation(self):
        _FakeRepo.instances = []
        fake_db = _FakeDB(
            [
                {"project_id": "proj-1234-5678", "timezone": "Asia/Kolkata"},
                {"project_id": "proj-0000-0000", "timezone": "UTC"},
            ]
        )
        with patch.object(scheduler_service, "SchedulerRepository", _FakeRepo), patch.object(
            settings,
            "action_items_enabled",
            True,
        ), patch.object(
            settings,
            "action_items_digest_enabled",
            True,
        ):
            changed = await scheduler_service.ensure_action_item_schedules_for_projects(fake_db)

        self.assertEqual(changed, 4)
        self.assertEqual(len(_FakeRepo.instances), 1)
        upserts = _FakeRepo.instances[0].upserts
        self.assertEqual(len(upserts), 4)
        job_types = [item["job_type"] for item in upserts]
        self.assertEqual(job_types.count("action_item_digest"), 2)
        self.assertEqual(job_types.count("action_item_followup_escalation"), 2)
        for item in upserts:
            if item["job_type"] == "action_item_digest":
                self.assertEqual(item["schedule_spec"]["time"], "12:30")
                self.assertTrue(item["enabled"])
            if item["job_type"] == "action_item_followup_escalation":
                self.assertEqual(item["schedule_spec"]["minutes"], 60)

    async def test_ensure_action_item_schedules_for_projects_does_not_overwrite_existing(self):
        _FakeRepo.instances = []
        fake_db = _FakeDB(
            [
                {"project_id": "proj-1234-5678", "timezone": "Asia/Kolkata"},
            ]
        )
        with patch.object(scheduler_service, "SchedulerRepository", _FakeRepo), patch.object(
            settings,
            "action_items_enabled",
            True,
        ), patch.object(
            settings,
            "action_items_digest_enabled",
            True,
        ):
            # prime existing schedules on created repo instance
            repo = _FakeRepo(fake_db)
            repo.existing[("action_item_digest", "proj-1234-5678")] = {"job_type": "action_item_digest"}
            repo.existing[("action_item_followup_escalation", "proj-1234-5678")] = {"job_type": "action_item_followup_escalation"}
            # replace constructor to return our primed instance
            with patch.object(scheduler_service, "SchedulerRepository", lambda db: repo):
                changed = await scheduler_service.ensure_action_item_schedules_for_projects(fake_db)

        self.assertEqual(changed, 0)
        self.assertEqual(len(repo.upserts), 0)

    async def test_followup_actor_reminder_includes_assignment_suggestions(self):
        class _FakeCursor:
            def __init__(self, rows):
                self._rows = rows

            async def to_list(self, length=None):
                return list(self._rows)

        class _FakeCollection:
            def __init__(self, rows):
                self._rows = rows
                self.update_many = unittest.mock.AsyncMock(return_value=None)
                self._calls = 0

            def find(self, query):
                self._calls += 1
                # First query: actor reminder candidates; second query: escalation candidates.
                if self._calls == 1:
                    return _FakeCursor(self._rows)
                return _FakeCursor([])

        class _FakeProjects:
            def __init__(self, project_doc):
                self._project_doc = project_doc

            async def find_one(self, query):
                return self._project_doc

        class _FakeDB:
            def __init__(self, project_doc):
                self.projects = _FakeProjects(project_doc)

        sent_messages = []

        class _FakeSlackClient:
            def __init__(self, token):
                self.token = token

            async def send_message(self, user_id, text=None, blocks=None):
                sent_messages.append({"user_id": user_id, "text": str(text or "")})
                return True

        project_doc = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "name": "Owner", "status": "active"},
                {"user_id": "u-111", "name": "Prabhu", "status": "active"},
                {"user_id": "u-222", "name": "Sridevi", "status": "active"},
            ],
            "integrations": {"slack": {"access_token": "enc-token"}},
        }
        rows = [
            {
                "entity_type": "action_item",
                "action_item_id": "ai-1",
                "display_key": "ACTION-1",
                "title": "Owner unresolved item",
                "status": "open",
                "needs_followup": True,
                "needs_followup_set_at": "2026-03-16T00:00:00Z",
                "created_by_user_id": "u-111",
            }
        ]
        fake_collection = _FakeCollection(rows)
        fake_db = _FakeDB(project_doc)

        with patch.object(settings, "action_items_enabled", True), patch.object(
            settings,
            "action_items_followup_actor_reminder_hours",
            1,
        ), patch.object(
            settings,
            "action_items_followup_pm_escalation_hours",
            4,
        ), patch(
            "app.action_items.reminders.get_brain_collection",
            return_value=fake_collection,
        ), patch(
            "app.core.security.encryption_service.decrypt",
            return_value="slack-token",
        ), patch(
            "app.integrations.slack.client.SlackClient",
            _FakeSlackClient,
        ), patch(
            "app.cadence.slack_identity.resolve_slack_user_id_for_member",
            new=unittest.mock.AsyncMock(return_value="U111"),
        ):
            result = await action_item_reminders.execute_followup_escalation(
                fake_db,
                {"project_id": "proj-1234-5678"},
            )

        self.assertEqual(result.get("actor_reminded"), 1)
        self.assertTrue(sent_messages)
        reminder_text = sent_messages[0]["text"]
        self.assertIn("Suggestions:", reminder_text)
        self.assertIn("u-111", reminder_text)

    async def test_followup_escalation_message_marks_heading_and_unassigned_reason(self):
        class _FakeCursor:
            def __init__(self, rows):
                self._rows = rows

            async def to_list(self, length=None):
                return list(self._rows)

        class _FakeCollection:
            def __init__(self, *, reminder_rows, escalation_rows):
                self._reminder_rows = reminder_rows
                self._escalation_rows = escalation_rows
                self.update_many = unittest.mock.AsyncMock(return_value=None)
                self._calls = 0

            def find(self, query):
                self._calls += 1
                if self._calls == 1:
                    return _FakeCursor(self._reminder_rows)
                return _FakeCursor(self._escalation_rows)

        class _FakeProjects:
            def __init__(self, project_doc):
                self._project_doc = project_doc

            async def find_one(self, query):
                return self._project_doc

        class _FakeDB:
            def __init__(self, project_doc):
                self.projects = _FakeProjects(project_doc)

        sent_messages = []

        class _FakeSlackClient:
            def __init__(self, token):
                self.token = token

            async def send_message(self, user_id, text=None, blocks=None):
                sent_messages.append({"user_id": user_id, "text": str(text or "")})
                return True

        project_doc = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "name": "Owner", "status": "active"},
                {"user_id": "u-111", "name": "Prabhu", "status": "active"},
            ],
            "integrations": {"slack": {"access_token": "enc-token"}},
        }
        escalation_rows = [
            {
                "entity_type": "action_item",
                "action_item_id": "ai-1",
                "display_key": "ACTION-1",
                "title": "Owner unresolved item",
                "status": "open",
                "needs_followup": True,
                "needs_followup_set_at": "2026-03-16T00:00:00Z",
                "owner_user_id": None,
                "created_by_user_id": "u-111",
            }
        ]
        fake_collection = _FakeCollection(
            reminder_rows=[],
            escalation_rows=escalation_rows,
        )
        fake_db = _FakeDB(project_doc)

        with patch.object(settings, "action_items_enabled", True), patch.object(
            settings,
            "action_items_followup_actor_reminder_hours",
            1,
        ), patch.object(
            settings,
            "action_items_followup_pm_escalation_hours",
            4,
        ), patch(
            "app.action_items.reminders.get_brain_collection",
            return_value=fake_collection,
        ), patch(
            "app.core.security.encryption_service.decrypt",
            return_value="slack-token",
        ), patch(
            "app.integrations.slack.client.SlackClient",
            _FakeSlackClient,
        ), patch(
            "app.cadence.slack_identity.resolve_slack_user_id_for_member",
            new=unittest.mock.AsyncMock(return_value="U111"),
        ):
            result = await action_item_reminders.execute_followup_escalation(
                fake_db,
                {"project_id": "proj-1234-5678"},
            )

        self.assertTrue(result.get("notified"))
        self.assertTrue(sent_messages)
        escalation_text = sent_messages[-1]["text"]
        self.assertIn("*Action items follow-up escalation:*", escalation_text)
        self.assertIn("(reason: unassigned)", escalation_text)

    async def test_followup_escalation_skips_owner_when_policy_enabled(self):
        class _FakeCursor:
            def __init__(self, rows):
                self._rows = rows

            async def to_list(self, length=None):
                return list(self._rows)

        class _FakeCollection:
            def __init__(self, reminder_rows):
                self._reminder_rows = reminder_rows
                self.update_many = unittest.mock.AsyncMock(return_value=None)
                self._calls = 0

            def find(self, _query):
                self._calls += 1
                if self._calls == 1:
                    return _FakeCursor(self._reminder_rows)
                return _FakeCursor(self._reminder_rows)

        class _FakeProjects:
            def __init__(self, project_doc):
                self._project_doc = project_doc

            async def find_one(self, _query):
                return self._project_doc

        class _FakeProjectSchedules:
            async def find_one(self, _query, _projection=None):
                return {"schedule_spec": {"ignore_followup_for_owner": True}}

        class _FakeDB:
            def __init__(self, project_doc):
                self.projects = _FakeProjects(project_doc)
                self.project_schedules = _FakeProjectSchedules()

        sent_messages = []

        class _FakeSlackClient:
            def __init__(self, token):
                self.token = token

            async def send_message(self, user_id, text=None, blocks=None):
                sent_messages.append({"user_id": user_id, "text": str(text or "")})
                return True

        project_doc = {
            "project_id": "proj-1234-5678",
            "user_id": "u-owner",
            "members": [
                {"user_id": "u-owner", "name": "Owner", "status": "active"},
            ],
            "integrations": {"slack": {"access_token": "enc-token"}},
        }
        reminder_rows = [
            {
                "entity_type": "action_item",
                "action_item_id": "ai-1",
                "display_key": "ACTION-1",
                "title": "Owner unresolved item",
                "status": "open",
                "needs_followup": True,
                "needs_followup_set_at": "2026-03-16T00:00:00Z",
                "created_by_user_id": "u-owner",
            }
        ]
        fake_collection = _FakeCollection(reminder_rows)
        fake_db = _FakeDB(project_doc)

        with patch.object(settings, "action_items_enabled", True), patch.object(
            settings,
            "action_items_followup_actor_reminder_hours",
            1,
        ), patch.object(
            settings,
            "action_items_followup_pm_escalation_hours",
            4,
        ), patch(
            "app.action_items.reminders.get_brain_collection",
            return_value=fake_collection,
        ), patch(
            "app.core.security.encryption_service.decrypt",
            return_value="slack-token",
        ), patch(
            "app.integrations.slack.client.SlackClient",
            _FakeSlackClient,
        ), patch(
            "app.cadence.slack_identity.resolve_slack_user_id_for_member",
            new=unittest.mock.AsyncMock(return_value="UOWNER"),
        ):
            result = await action_item_reminders.execute_followup_escalation(
                fake_db,
                {"project_id": "proj-1234-5678"},
            )

        self.assertEqual(result.get("reason"), "owner_followup_ignored_by_policy")
        self.assertEqual(result.get("escalated"), 0)
        self.assertFalse(result.get("notified"))
        self.assertFalse(sent_messages)


if __name__ == "__main__":
    unittest.main()
