import unittest
from datetime import datetime
from unittest.mock import patch

from app.tasks.read_repository.providers.linear_repository import LinearTaskReadRepository
from app.tasks.schemas import ListTasksFilters


class _FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)
        self.sort_calls = []
        self.limit_calls = []

    def sort(self, field, direction):
        self.sort_calls.append((field, direction))
        return self

    def limit(self, limit):
        self.limit_calls.append(limit)
        return self

    async def to_list(self, length=None):
        if length is None:
            return list(self.docs)
        return list(self.docs)[:length]


class _FakeCollection:
    def __init__(self, docs):
        self.docs = list(docs)
        self.find_queries = []
        self.find_one_queries = []
        self.cursor = _FakeCursor(self.docs)

    def find(self, query):
        self.find_queries.append(query)
        return self.cursor

    async def find_one(self, query):
        self.find_one_queries.append(query)
        external_id = str(query.get("external_id") or "").strip()
        for doc in self.docs:
            if str(doc.get("external_id") or "").strip() == external_id:
                return doc
        return None


class LinearTaskReadRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_tasks_uses_linear_scope_and_returns_dtos(self):
        docs = [
            {
                "integration_type": "linear",
                "external_id": "LIN-101",
                "title": "Document API parity",
                "status": "todo",
                "url": "https://linear.app/workspace/issue/LIN-101",
                "created_at": datetime(2026, 3, 30),
                "updated_at": datetime(2026, 3, 30),
            }
        ]
        fake_collection = _FakeCollection(docs)
        repository = LinearTaskReadRepository()

        with patch(
            "app.tasks.read_repository.providers.linear_repository.get_brain_collection",
            return_value=fake_collection,
        ):
            results = await repository.list_tasks(
                project_id="proj-1",
                filters=ListTasksFilters(status_filter=["todo"], limit=10),
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].external_id, "LIN-101")
        self.assertEqual(results[0].provider, "linear")
        self.assertEqual(results[0].source, "linear")
        self.assertEqual(fake_collection.find_queries[0]["integration_type"], "linear")

    async def test_search_and_get_task_remain_linear_scoped(self):
        docs = [
            {
                "integration_type": "linear",
                "external_id": "LIN-200",
                "title": "Stabilize migration smoke checks",
                "status": "in_progress",
                "url": "https://linear.app/workspace/issue/LIN-200",
                "created_at": datetime(2026, 3, 30),
                "updated_at": datetime(2026, 3, 30),
            }
        ]
        fake_collection = _FakeCollection(docs)
        repository = LinearTaskReadRepository()

        with patch(
            "app.tasks.read_repository.providers.linear_repository.get_brain_collection",
            return_value=fake_collection,
        ):
            search_results = await repository.search_tasks(
                project_id="proj-1",
                query="migration",
                limit=5,
            )
            item = await repository.get_task(project_id="proj-1", external_id="LIN-200")

        self.assertEqual(len(search_results), 1)
        self.assertEqual(search_results[0].external_id, "LIN-200")
        self.assertEqual(fake_collection.find_queries[-1]["integration_type"], "linear")
        self.assertEqual(fake_collection.find_one_queries[0]["integration_type"], "linear")
        self.assertIsNotNone(item)
        self.assertEqual(item.external_id, "LIN-200")


if __name__ == "__main__":
    unittest.main()
