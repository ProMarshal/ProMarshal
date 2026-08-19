import asyncio
from unittest.mock import AsyncMock, patch

from app.tasks.providers.jira_adapter import JiraAdapter


class _FakeMetadataCollection:
    def __init__(self, initial_doc=None):
        self._doc = initial_doc
        self.updates = []

    async def find_one(self, *_args, **_kwargs):
        return self._doc

    async def update_one(self, _filter, update, upsert=False):
        self.updates.append((_filter, update, upsert))
        set_payload = (update or {}).get("$set") or {}
        unset_payload = (update or {}).get("$unset") or {}
        self._doc = {
            "allowed_create_types": list(set_payload.get("allowed_create_types") or []),
            "type_rules": dict(set_payload.get("type_rules") or {}),
            "updated_at": set_payload.get("updated_at"),
        }
        if unset_payload:
            for key in unset_payload.keys():
                self._doc.pop(str(key), None)


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


def test_validate_issue_type_uses_cache_without_refresh_when_present() -> None:
    adapter = _build_adapter()
    collection = _FakeMetadataCollection(
        initial_doc={"allowed_create_types": ["Task", "Story", "Bug"]}
    )

    refresh_mock = AsyncMock(return_value=["Task", "Story", "Bug"])

    async def _run() -> None:
        with patch("app.tasks.providers.jira_adapter.get_brain_collection", return_value=collection):
            with patch.object(
                adapter,
                "_fetch_and_cache_allowed_issue_types",
                refresh_mock,
            ):
                resolved = await adapter._validate_issue_type_allowed_for_create(
                    jira_project_key="PRM",
                    issue_type_name="Story",
                )
                assert resolved == "Story"

    asyncio.run(_run())
    refresh_mock.assert_not_awaited()


def test_validate_issue_type_refreshes_on_cache_miss_and_allows_when_now_present() -> None:
    adapter = _build_adapter()
    collection = _FakeMetadataCollection(initial_doc={"allowed_create_types": ["Task"]})

    async def _run() -> None:
        with patch("app.tasks.providers.jira_adapter.get_brain_collection", return_value=collection):
            with patch(
                "app.tasks.providers.jira_adapter.jira_oauth_service.get_project_create_metadata",
                AsyncMock(
                    return_value={
                        "allowed_create_types": ["Task", "Story", "Bug"],
                        "parent_rules": {},
                    }
                ),
            ):
                resolved = await adapter._validate_issue_type_allowed_for_create(
                    jira_project_key="PRM",
                    issue_type_name="Story",
                )
                assert resolved == "Story"

    asyncio.run(_run())
    assert len(collection.updates) == 1
    assert "Story" in (collection._doc or {}).get("allowed_create_types", [])


def test_validate_issue_type_rejects_when_missing_after_refresh() -> None:
    adapter = _build_adapter()
    collection = _FakeMetadataCollection(initial_doc={"allowed_create_types": ["Task"]})

    async def _run() -> None:
        with patch("app.tasks.providers.jira_adapter.get_brain_collection", return_value=collection):
            with patch(
                "app.tasks.providers.jira_adapter.jira_oauth_service.get_project_create_metadata",
                AsyncMock(
                    return_value={
                        "allowed_create_types": ["Task", "Bug"],
                        "parent_rules": {},
                    }
                ),
            ):
                await adapter._validate_issue_type_allowed_for_create(
                    jira_project_key="PRM",
                    issue_type_name="Story",
                )

    try:
        asyncio.run(_run())
    except ValueError as exc:
        message = str(exc)
        assert "Issue type 'Story'" in message
        assert "Allowed types: Task, Bug" in message
        return

    raise AssertionError("Expected ValueError for unsupported create issue type")


def test_validate_issue_type_allows_subtask_alias_and_returns_project_label() -> None:
    adapter = _build_adapter()
    collection = _FakeMetadataCollection(initial_doc={"allowed_create_types": ["Task", "Subtask"]})

    async def _run() -> None:
        with patch("app.tasks.providers.jira_adapter.get_brain_collection", return_value=collection):
            resolved = await adapter._validate_issue_type_allowed_for_create(
                jira_project_key="PRM",
                issue_type_name="Sub-task",
            )
            assert resolved == "Subtask"

    asyncio.run(_run())


def test_refresh_persists_type_rules_in_metadata_cache() -> None:
    adapter = _build_adapter()
    collection = _FakeMetadataCollection(initial_doc={"allowed_create_types": ["Task"]})

    async def _run() -> None:
        with patch("app.tasks.providers.jira_adapter.get_brain_collection", return_value=collection):
            with patch(
                "app.tasks.providers.jira_adapter.jira_oauth_service.get_project_create_metadata",
                AsyncMock(
                    return_value={
                        "allowed_create_types": ["Task", "Subtask"],
                        "parent_rules": {
                            "subtask": {
                                "provider_type": "Subtask",
                                "parent_required": True,
                                "allowed_parent_provider_types": ["Task"],
                            }
                        },
                    }
                ),
            ):
                resolved = await adapter._validate_issue_type_allowed_for_create(
                    jira_project_key="PRM",
                    issue_type_name="Sub-task",
                )
                assert resolved == "Subtask"

    asyncio.run(_run())
    type_rules = (collection._doc or {}).get("type_rules") or {}
    assert "subtask" in type_rules
    subtask_parent = ((type_rules.get("subtask") or {}).get("parent") or {})
    assert bool(subtask_parent.get("allowed")) is True
    assert list(subtask_parent.get("allowed_parent_provider_types") or []) == ["Task"]


def test_refresh_persists_effective_type_rules_when_metadata_parent_list_is_empty() -> None:
    adapter = _build_adapter()
    collection = _FakeMetadataCollection(initial_doc={"allowed_create_types": ["Task"]})

    async def _run() -> None:
        with patch("app.tasks.providers.jira_adapter.get_brain_collection", return_value=collection):
            with patch(
                "app.tasks.providers.jira_adapter.jira_oauth_service.get_project_create_metadata",
                AsyncMock(
                    return_value={
                        "allowed_create_types": ["Task", "Story", "Subtask", "Epic"],
                        "parent_rules": {
                            "subtask": {
                                "provider_type": "Subtask",
                                "parent_required": True,
                                "allowed_parent_provider_types": [],
                            }
                        },
                    }
                ),
            ):
                resolved = await adapter._validate_issue_type_allowed_for_create(
                    jira_project_key="PRM",
                    issue_type_name="Sub-task",
                )
                assert resolved == "Subtask"

    asyncio.run(_run())
    type_rules = (collection._doc or {}).get("type_rules") or {}
    subtask_rule = dict(type_rules.get("subtask") or {})
    parent = dict(subtask_rule.get("parent") or {})
    assert bool(parent.get("required")) is True
    # Effective persisted rule should use Jira-safe fallback from allowed types.
    assert bool(parent.get("allowed")) is True
    assert list(parent.get("allowed_parent_provider_types") or []) == ["Task", "Story", "Epic"]


def test_refresh_marks_epic_parent_not_allowed_in_effective_type_rules() -> None:
    adapter = _build_adapter()
    collection = _FakeMetadataCollection(initial_doc={"allowed_create_types": ["Task"]})

    async def _run() -> None:
        with patch("app.tasks.providers.jira_adapter.get_brain_collection", return_value=collection):
            with patch(
                "app.tasks.providers.jira_adapter.jira_oauth_service.get_project_create_metadata",
                AsyncMock(
                    return_value={
                        "allowed_create_types": ["Task", "Story", "Subtask", "Epic"],
                        "parent_rules": {},
                    }
                ),
            ):
                resolved = await adapter._validate_issue_type_allowed_for_create(
                    jira_project_key="PRM",
                    issue_type_name="Epic",
                )
                assert resolved == "Epic"

    asyncio.run(_run())
    type_rules = (collection._doc or {}).get("type_rules") or {}
    epic_parent = ((type_rules.get("epic") or {}).get("parent") or {})
    assert bool(epic_parent.get("allowed")) is False
    assert bool(epic_parent.get("required")) is False
    assert list(epic_parent.get("allowed_parent_provider_types") or []) == []
