import unittest

from app.tasks.read_repository.dispatcher import (
    TaskReadClarificationRequired,
    TaskReadDispatcher,
)


class _FakeRepository:
    def __init__(self, provider: str):
        self.provider = provider
        self.calls = []

    async def list_tasks(self, *, project_id: str, filters=None):
        self.calls.append(("list_tasks", project_id, filters))
        return [self.provider]

    async def search_tasks(self, *, project_id: str, query: str, limit: int = 50):
        self.calls.append(("search_tasks", project_id, query, limit))
        return [self.provider, query, limit]

    async def get_task(self, *, project_id: str, external_id: str):
        self.calls.append(("get_task", project_id, external_id))
        return {"provider": self.provider, "external_id": external_id}


class TaskReadDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_hint_routes_to_matching_repository(self):
        dispatcher = TaskReadDispatcher()
        jira_repo = _FakeRepository("jira")
        linear_repo = _FakeRepository("linear")
        dispatcher.register("jira", jira_repo)
        dispatcher.register("linear", linear_repo)

        result = await dispatcher.list_tasks(
            project_id="proj-1",
            provider_hint="linear",
            project_context={},
        )

        self.assertEqual(result, ["linear"])
        self.assertEqual(len(linear_repo.calls), 1)
        self.assertEqual(len(jira_repo.calls), 0)

    async def test_no_provider_hint_routes_to_active_provider(self):
        dispatcher = TaskReadDispatcher()
        jira_repo = _FakeRepository("jira")
        linear_repo = _FakeRepository("linear")
        dispatcher.register("jira", jira_repo)
        dispatcher.register("linear", linear_repo)

        result = await dispatcher.list_tasks(
            project_id="proj-1",
            project_context={
                "project_id": "proj-1",
                "integrations": {
                    "jira": {"status": "connected"},
                    "linear": {"status": "connected"},
                },
                "active_provider": "jira",
            },
        )

        self.assertEqual(result, ["jira"])
        self.assertEqual(len(jira_repo.calls), 1)
        self.assertEqual(len(linear_repo.calls), 0)

    async def test_no_hint_without_active_provider_in_multi_provider_requires_clarification(self):
        dispatcher = TaskReadDispatcher()
        dispatcher.register("jira", _FakeRepository("jira"))
        dispatcher.register("linear", _FakeRepository("linear"))

        with self.assertRaises(TaskReadClarificationRequired) as captured:
            await dispatcher.list_tasks(
                project_id="proj-1",
                project_context={
                    "project_id": "proj-1",
                    "integrations": {
                        "jira": {"status": "connected"},
                        "linear": {"status": "connected"},
                    },
                    "active_provider": None,
                },
            )

        err = captured.exception
        self.assertEqual(err.reason_code, "ambiguous_provider")
        self.assertEqual(err.candidate_providers, ["jira", "linear"])


if __name__ == "__main__":
    unittest.main()

