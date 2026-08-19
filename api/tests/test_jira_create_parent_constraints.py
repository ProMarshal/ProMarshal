import asyncio
from unittest.mock import AsyncMock, patch

from app.tasks.providers.jira_adapter import JiraAdapter


def _build_adapter() -> JiraAdapter:
    project = {
        "project_id": "proj-0001-0001",
        "integrations": {
            "jira": {
                "cloud_id": "cloud-1",
                "site_url": "https://example.atlassian.net",
            }
        },
    }
    return JiraAdapter(project=project, access_token_override="token")


def test_parent_key_rejected_for_non_subtask_create() -> None:
    adapter = _build_adapter()

    async def _run() -> None:
        await adapter._validate_parent_constraints_for_create(
            jira_project_key="PRM",
            issue_type_name="Story",
            parent_external_id="PRM-101",
            parent_rule_filter={
                "parent_allowed": False,
                "parent_required": False,
                "allowed_parent_provider_types": [],
            },
        )

    try:
        asyncio.run(_run())
    except ValueError as exc:
        assert "not supported for Jira issue type 'Story'" in str(exc)
        return
    raise AssertionError("Expected ValueError when parent key is supplied for non-subtask create")


def test_subtask_parent_rejected_when_parent_project_differs() -> None:
    adapter = _build_adapter()

    async def _run() -> None:
        with patch(
            "app.tasks.providers.jira_adapter.jira_oauth_service.get_issue",
            AsyncMock(
                return_value={
                    "fields": {
                        "project": {"key": "OTHER"},
                        "issuetype": {"name": "Story", "subtask": False},
                    }
                }
            ),
        ):
            await adapter._validate_parent_constraints_for_create(
                jira_project_key="PRM",
                issue_type_name="Sub-task",
                parent_external_id="OTHER-22",
                parent_rule_filter={
                    "parent_allowed": True,
                    "parent_required": True,
                    "allowed_parent_provider_types": ["Task", "Story"],
                },
            )

    try:
        asyncio.run(_run())
    except ValueError as exc:
        assert "belongs to project OTHER" in str(exc)
        return
    raise AssertionError("Expected ValueError for cross-project parent on subtask create")


def test_subtask_parent_rejected_when_parent_is_subtask() -> None:
    adapter = _build_adapter()

    async def _run() -> None:
        with patch(
            "app.tasks.providers.jira_adapter.jira_oauth_service.get_issue",
            AsyncMock(
                return_value={
                    "fields": {
                        "project": {"key": "PRM"},
                        "issuetype": {"name": "Sub-task", "subtask": True},
                    }
                }
            ),
        ):
            await adapter._validate_parent_constraints_for_create(
                jira_project_key="PRM",
                issue_type_name="Sub-task",
                parent_external_id="PRM-44",
                parent_rule_filter={
                    "parent_allowed": True,
                    "parent_required": True,
                    "allowed_parent_provider_types": ["Task", "Story"],
                },
            )

    try:
        asyncio.run(_run())
    except ValueError as exc:
        assert "cannot be used as a parent" in str(exc)
        return
    raise AssertionError("Expected ValueError when subtask is used as parent")


def test_subtask_parent_allows_same_project_non_subtask_parent() -> None:
    adapter = _build_adapter()

    async def _run() -> None:
        with patch(
            "app.tasks.providers.jira_adapter.jira_oauth_service.get_issue",
            AsyncMock(
                return_value={
                    "fields": {
                        "project": {"key": "PRM"},
                        "issuetype": {"name": "Story", "subtask": False},
                    }
                }
            ),
        ):
            await adapter._validate_parent_constraints_for_create(
                jira_project_key="PRM",
                issue_type_name="Sub-task",
                parent_external_id="PRM-45",
                parent_rule_filter={
                    "parent_allowed": True,
                    "parent_required": True,
                    "allowed_parent_provider_types": ["Task", "Story"],
                },
            )

    asyncio.run(_run())


def test_subtask_parent_rejected_when_parent_type_not_allowed_by_rule() -> None:
    adapter = _build_adapter()

    async def _run() -> None:
        with patch(
            "app.tasks.providers.jira_adapter.jira_oauth_service.get_issue",
            AsyncMock(
                return_value={
                    "fields": {
                        "project": {"key": "PRM"},
                        "issuetype": {"name": "Epic", "subtask": False},
                    }
                }
            ),
        ):
            await adapter._validate_parent_constraints_for_create(
                jira_project_key="PRM",
                issue_type_name="Sub-task",
                parent_external_id="PRM-2",
                parent_rule_filter={
                    "parent_allowed": True,
                    "parent_required": True,
                    "allowed_parent_provider_types": ["Task", "Story"],
                },
            )

    try:
        asyncio.run(_run())
    except ValueError as exc:
        assert "not allowed for 'Sub-task'" in str(exc)
        return
    raise AssertionError("Expected ValueError when Epic is used as Sub-task parent")


def test_story_parent_allowed_when_parent_type_matches_rule() -> None:
    adapter = _build_adapter()

    async def _run() -> None:
        with patch(
            "app.tasks.providers.jira_adapter.jira_oauth_service.get_issue",
            AsyncMock(
                return_value={
                    "fields": {
                        "project": {"key": "PRM"},
                        "issuetype": {"name": "Epic", "subtask": False},
                    }
                }
            ),
        ):
            await adapter._validate_parent_constraints_for_create(
                jira_project_key="PRM",
                issue_type_name="Story",
                parent_external_id="PRM-2",
                parent_rule_filter={
                    "parent_allowed": True,
                    "parent_required": False,
                    "allowed_parent_provider_types": ["Epic"],
                },
            )

    asyncio.run(_run())
