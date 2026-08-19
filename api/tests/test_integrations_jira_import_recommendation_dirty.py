import importlib
import unittest
from unittest.mock import patch


integrations_router_module = importlib.import_module("app.integrations.router")


class _FakeBrainCollection:
    def __init__(self):
        self.calls = []

    async def update_one(self, query, update, upsert=False):
        self.calls.append(
            {
                "query": query,
                "update": update,
                "upsert": bool(upsert),
            }
        )


class _FakeNormalizer:
    def normalize_task(self, task):
        return {
            "integration_type": "jira",
            "external_id": task.get("key"),
            "entity_type": "work_item",
        }


class _FallbackNormalizer:
    def normalize_task(self, task):
        return {
            "integration_type": "jira",
            "external_id": str(task.get("id") or "").strip() or None,
            "entity_type": "work_item",
        }


class _LegacyTaskNormalizer:
    def normalize_task(self, task):
        return {
            "integration_type": "jira",
            "external_id": task.get("key"),
            "entity_type": "task",
            "issue_type": "Epic",
            "provider_type": None,
            "work_item_type": None,
        }


class JiraImportRecommendationDirtyTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_upsert_marks_dirty_for_each_task_key(self):
        brain = _FakeBrainCollection()
        tasks = [{"key": "ABC-1"}, {"key": "ABC-2"}]
        with patch(
            "app.integrations.router.mark_project_task_dirty",
            return_value=True,
        ) as mark_dirty_mock:
            synced = await integrations_router_module._upsert_jira_tasks_to_brain_and_mark_dirty(
                brain_collection=brain,
                tasks=tasks,
                normalizer=_FakeNormalizer(),
                custom_project_id="proj-1111-2222",
            )
        self.assertEqual(synced, 2)
        self.assertEqual(len(brain.calls), 2)
        mark_dirty_mock.assert_any_call("proj-1111-2222", "ABC-1")
        mark_dirty_mock.assert_any_call("proj-1111-2222", "ABC-2")
        self.assertEqual(mark_dirty_mock.call_count, 2)

    async def test_import_upsert_skips_dirty_mark_for_missing_task_key(self):
        brain = _FakeBrainCollection()
        tasks = [{"key": "ABC-1"}, {"key": ""}, {}]
        with patch(
            "app.integrations.router.mark_project_task_dirty",
            return_value=True,
        ) as mark_dirty_mock:
            synced = await integrations_router_module._upsert_jira_tasks_to_brain_and_mark_dirty(
                brain_collection=brain,
                tasks=tasks,
                normalizer=_FakeNormalizer(),
                custom_project_id="proj-1111-2222",
            )
        self.assertEqual(synced, 3)
        self.assertEqual(len(brain.calls), 3)
        mark_dirty_mock.assert_called_once_with("proj-1111-2222", "ABC-1")

    async def test_import_upsert_uses_normalized_external_id_when_raw_key_missing(self):
        brain = _FakeBrainCollection()
        tasks = [{"id": "FALLBACK-7"}]
        with patch(
            "app.integrations.router.mark_project_task_dirty",
            return_value=True,
        ) as mark_dirty_mock:
            synced = await integrations_router_module._upsert_jira_tasks_to_brain_and_mark_dirty(
                brain_collection=brain,
                tasks=tasks,
                normalizer=_FallbackNormalizer(),
                custom_project_id="proj-1111-2222",
            )
        self.assertEqual(synced, 1)
        self.assertEqual(len(brain.calls), 1)
        mark_dirty_mock.assert_called_once_with("proj-1111-2222", "FALLBACK-7")

    async def test_import_upsert_continues_when_dirty_marker_raises(self):
        brain = _FakeBrainCollection()
        tasks = [{"key": "ABC-1"}, {"key": "ABC-2"}]
        with patch(
            "app.integrations.router.mark_project_task_dirty",
            side_effect=RuntimeError("redis down"),
        ):
            synced = await integrations_router_module._upsert_jira_tasks_to_brain_and_mark_dirty(
                brain_collection=brain,
                tasks=tasks,
                normalizer=_FakeNormalizer(),
                custom_project_id="proj-1111-2222",
            )
        self.assertEqual(synced, 2)
        self.assertEqual(len(brain.calls), 2)

    async def test_import_upsert_canonicalizes_legacy_task_shape(self):
        brain = _FakeBrainCollection()
        tasks = [{"key": "ABC-88"}]
        with patch(
            "app.integrations.router.mark_project_task_dirty",
            return_value=True,
        ):
            synced = await integrations_router_module._upsert_jira_tasks_to_brain_and_mark_dirty(
                brain_collection=brain,
                tasks=tasks,
                normalizer=_LegacyTaskNormalizer(),
                custom_project_id="proj-1111-2222",
            )
        self.assertEqual(synced, 1)
        set_payload = brain.calls[0]["update"]["$set"]
        self.assertEqual(set_payload.get("entity_type"), "work_item")
        self.assertEqual(set_payload.get("provider_type"), "Epic")
        self.assertEqual(set_payload.get("work_item_type"), "portfolio")


if __name__ == "__main__":
    unittest.main()
