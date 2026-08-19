import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi import HTTPException


integrations_router_module = importlib.import_module("app.integrations.router")


class _FakeProjectsCollection:
    def __init__(self, project_doc, updated_doc=None):
        self._project_doc = project_doc
        self._updated_doc = updated_doc if updated_doc is not None else project_doc
        self._find_calls = 0
        self.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=1))

    async def find_one(self, *_args, **_kwargs):
        self._find_calls += 1
        if self._find_calls == 1:
            return self._project_doc
        return self._updated_doc


class _FakeBrainCollection:
    def __init__(self, count=0, deleted=0):
        self._count = int(count)
        self._deleted = int(deleted)
        self.delete_many = AsyncMock(return_value=SimpleNamespace(deleted_count=self._deleted))

    async def count_documents(self, *_args, **_kwargs):
        return self._count


class JiraDisconnectCleanupFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_jira_with_purge_deletes_project_work_items(self):
        object_id = ObjectId("69bee8596fe6571c0813bf6a")
        project_doc = {
            "_id": object_id,
            "project_id": "proj-1111-2222",
            "active_provider": "jira",
            "integrations": {"jira": {"status": "connected"}},
        }
        updated_doc = {
            "_id": object_id,
            "project_id": "proj-1111-2222",
            "active_provider": None,
            "integrations": {},
        }
        fake_projects = _FakeProjectsCollection(project_doc=project_doc, updated_doc=updated_doc)
        fake_db = SimpleNamespace(projects=fake_projects)
        fake_brain = _FakeBrainCollection(deleted=6)

        with patch.object(integrations_router_module, "get_database", return_value=fake_db), patch.object(
            integrations_router_module, "get_brain_collection", return_value=fake_brain
        ), patch.object(
            integrations_router_module, "_invalidate_work_item_read_caches", new=AsyncMock(return_value=None)
        ):
            payload = await integrations_router_module.disconnect_jira(
                project_id=str(object_id),
                purge_data=True,
                current_user={"user_id": "user-1"},
            )

        self.assertTrue(payload.get("purged"))
        self.assertEqual(payload.get("deleted_count"), 6)
        fake_brain.delete_many.assert_awaited_once_with({"entity_type": "work_item"})

    async def test_get_workitem_data_status_requires_cleanup_when_data_exists(self):
        object_id = ObjectId("69bee8596fe6571c0813bf6a")
        project_doc = {
            "_id": object_id,
            "project_id": "proj-1111-2222",
            "integrations": {},
        }
        fake_projects = _FakeProjectsCollection(project_doc=project_doc)
        fake_db = SimpleNamespace(projects=fake_projects)
        fake_brain = _FakeBrainCollection(count=4)

        with patch.object(integrations_router_module, "get_database", return_value=fake_db), patch.object(
            integrations_router_module, "get_brain_collection", return_value=fake_brain
        ):
            payload = await integrations_router_module.get_workitem_data_status(
                project_id=str(object_id),
                current_user={"user_id": "user-1"},
            )

        self.assertTrue(payload.get("has_work_items"))
        self.assertEqual(payload.get("work_items_count"), 4)
        self.assertTrue(payload.get("requires_cleanup_before_connect"))

    async def test_cleanup_workitems_blocks_while_connected(self):
        object_id = ObjectId("69bee8596fe6571c0813bf6a")
        project_doc = {
            "_id": object_id,
            "project_id": "proj-1111-2222",
            "integrations": {"jira": {"status": "connected", "category": "workitem"}},
        }
        fake_projects = _FakeProjectsCollection(project_doc=project_doc)
        fake_db = SimpleNamespace(projects=fake_projects)

        with patch.object(integrations_router_module, "get_database", return_value=fake_db):
            with self.assertRaises(HTTPException) as raised:
                await integrations_router_module.cleanup_workitems(
                    project_id=str(object_id),
                    current_user={"user_id": "user-1"},
                )

        self.assertEqual(raised.exception.status_code, 409)

    async def test_cleanup_workitems_deletes_when_disconnected(self):
        object_id = ObjectId("69bee8596fe6571c0813bf6a")
        project_doc = {
            "_id": object_id,
            "project_id": "proj-1111-2222",
            "integrations": {},
        }
        fake_projects = _FakeProjectsCollection(project_doc=project_doc)
        fake_db = SimpleNamespace(projects=fake_projects)
        fake_brain = _FakeBrainCollection(count=5, deleted=5)

        with patch.object(integrations_router_module, "get_database", return_value=fake_db), patch.object(
            integrations_router_module, "get_brain_collection", return_value=fake_brain
        ), patch.object(
            integrations_router_module, "_invalidate_work_item_read_caches", new=AsyncMock(return_value=None)
        ) as invalidate_mock:
            payload = await integrations_router_module.cleanup_workitems(
                project_id=str(object_id),
                current_user={"user_id": "user-1"},
            )

        self.assertEqual(payload.get("deleted_count"), 5)
        self.assertEqual(payload.get("remaining_count"), 0)
        invalidate_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
