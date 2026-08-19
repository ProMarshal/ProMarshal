import unittest
from datetime import datetime
from unittest.mock import patch

from app.tasks.read_repository.providers.jira_repository import JiraTaskReadRepository
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


class JiraTaskReadRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_tasks_uses_jira_filter_and_returns_dtos(self):
        docs = [
            {
                "integration_type": "jira",
                "external_id": "PROJ-1",
                "title": "Ship onboarding",
                "status": "todo",
                "url": "https://example.atlassian.net/browse/PROJ-1",
                "created_at": datetime(2026, 2, 20),
                "updated_at": datetime(2026, 2, 20),
            }
        ]
        fake_collection = _FakeCollection(docs)
        repository = JiraTaskReadRepository()

        with patch(
            "app.tasks.read_repository.providers.jira_repository.get_brain_collection",
            return_value=fake_collection,
        ):
            results = await repository.list_tasks(
                project_id="proj-1",
                filters=ListTasksFilters(status_filter=["todo"], limit=10),
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].external_id, "PROJ-1")
        self.assertEqual(results[0].provider, "jira")
        self.assertEqual(results[0].source, "jira")
        self.assertEqual(results[0].schema_version, "1.0")
        self.assertIsNone(results[0].assignee)
        self.assertEqual(fake_collection.find_queries[0]["integration_type"], "jira")
        self.assertEqual(fake_collection.cursor.sort_calls[0], ("updated_at", -1))
        self.assertEqual(fake_collection.cursor.limit_calls[0], 10)

    async def test_search_and_get_task_remain_provider_scoped(self):
        docs = [
            {
                "integration_type": "jira",
                "external_id": "PROJ-9",
                "title": "Fix release notes",
                "status": "in_progress",
                "url": "https://example.atlassian.net/browse/PROJ-9",
                "created_at": datetime(2026, 2, 20),
                "updated_at": datetime(2026, 2, 20),
            }
        ]
        fake_collection = _FakeCollection(docs)
        repository = JiraTaskReadRepository()

        with patch(
            "app.tasks.read_repository.providers.jira_repository.get_brain_collection",
            return_value=fake_collection,
        ):
            search_results = await repository.search_tasks(
                project_id="proj-1",
                query="release",
                limit=5,
            )
            item = await repository.get_task(project_id="proj-1", external_id="PROJ-9")

        self.assertEqual(len(search_results), 1)
        self.assertEqual(search_results[0].external_id, "PROJ-9")
        self.assertEqual(fake_collection.find_queries[-1]["integration_type"], "jira")
        self.assertEqual(fake_collection.find_one_queries[0]["integration_type"], "jira")
        self.assertIsNotNone(item)
        self.assertEqual(item.external_id, "PROJ-9")
        self.assertEqual(item.provider, "jira")
        self.assertEqual(item.source, "jira")
        self.assertEqual(item.schema_version, "1.0")

    async def test_list_tasks_due_date_missing_adds_query_clause(self):
        docs = []
        fake_collection = _FakeCollection(docs)
        repository = JiraTaskReadRepository()

        with patch(
            "app.tasks.read_repository.providers.jira_repository.get_brain_collection",
            return_value=fake_collection,
        ):
            await repository.list_tasks(
                project_id="proj-1",
                filters=ListTasksFilters(due_date_mode="missing", limit=10),
            )

        query = fake_collection.find_queries[0]
        and_clauses = query.get("$and") or []
        self.assertTrue(
            any(
                isinstance(clause, dict)
                and isinstance(clause.get("$or"), list)
                and any("due_date" in dict(item) for item in clause.get("$or", []))
                for clause in and_clauses
            )
        )

    async def test_list_tasks_email_filter_does_not_widen_to_name_match(self):
        docs = []
        fake_collection = _FakeCollection(docs)
        repository = JiraTaskReadRepository()

        with patch(
            "app.tasks.read_repository.providers.jira_repository.get_brain_collection",
            return_value=fake_collection,
        ):
            await repository.list_tasks(
                project_id="proj-1",
                filters=ListTasksFilters(
                    assignee_email="sridevi.one@example.com",
                    assignee_name_exact="Sridevi",
                    limit=10,
                ),
            )

        query = fake_collection.find_queries[0]
        and_clauses = query.get("$and") or []
        assignee_clause = next(
            (
                clause
                for clause in and_clauses
                if isinstance(clause, dict) and isinstance(clause.get("$or"), list)
            ),
            {},
        )
        assignee_or = assignee_clause.get("$or") or []
        self.assertIn({"assignee_email": "sridevi.one@example.com"}, assignee_or)
        self.assertIn({"assignee.email": "sridevi.one@example.com"}, assignee_or)
        self.assertFalse(any("assignee_name" in row for row in assignee_or))
        self.assertFalse(any("assignee.name" in row for row in assignee_or))

    async def test_list_tasks_name_filter_used_only_without_id_or_email(self):
        docs = []
        fake_collection = _FakeCollection(docs)
        repository = JiraTaskReadRepository()

        with patch(
            "app.tasks.read_repository.providers.jira_repository.get_brain_collection",
            return_value=fake_collection,
        ):
            await repository.list_tasks(
                project_id="proj-1",
                filters=ListTasksFilters(
                    assignee_name_exact="Sridevi",
                    limit=10,
                ),
            )

        query = fake_collection.find_queries[0]
        and_clauses = query.get("$and") or []
        assignee_clause = next(
            (
                clause
                for clause in and_clauses
                if isinstance(clause, dict) and isinstance(clause.get("$or"), list)
            ),
            {},
        )
        assignee_or = assignee_clause.get("$or") or []
        self.assertTrue(any("assignee_name" in row for row in assignee_or))
        self.assertTrue(any("assignee.name" in row for row in assignee_or))
        self.assertFalse(any("assignee_email" in row for row in assignee_or))


if __name__ == "__main__":
    unittest.main()
