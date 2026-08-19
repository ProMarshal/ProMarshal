import unittest
from unittest.mock import AsyncMock, patch

from app.tasks.parent_reference_validator import (
    build_parent_reference_not_available_message,
    resolve_parent_reference,
)


class ParentReferenceValidatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_parent_reference_returns_brain_hit_without_jira_lookup(self):
        project = {"project_id": "proj-2000-2000"}

        class _FakeCollection:
            async def find_one(self, *_args, **_kwargs):
                return {
                    "external_id": "PRM-44",
                    "provider_type": "Story",
                    "work_item_type": "standard",
                    "status": "todo",
                }

        with patch(
            "app.tasks.parent_reference_validator.get_brain_collection",
            return_value=_FakeCollection(),
        ), patch(
            "app.tasks.parent_reference_validator.jira_oauth_service.get_issue",
            new=AsyncMock(),
        ) as jira_lookup:
            resolved = await resolve_parent_reference(
                project=project,
                parent_external_id="prm-44",
            )

        self.assertTrue(bool(resolved.get("exists")))
        self.assertEqual(str(resolved.get("source") or ""), "brain")
        self.assertEqual(str(resolved.get("parent_external_id") or ""), "PRM-44")
        jira_lookup.assert_not_awaited()

    async def test_resolve_parent_reference_uses_jira_fallback_when_brain_missing(self):
        project = {
            "project_id": "proj-2000-2000",
            "integrations": {
                "jira": {
                    "cloud_id": "cloud-1",
                    "access_token": "encrypted-token",
                }
            },
        }

        class _FakeCollection:
            async def find_one(self, *_args, **_kwargs):
                return None

        with patch(
            "app.tasks.parent_reference_validator.get_brain_collection",
            return_value=_FakeCollection(),
        ), patch(
            "app.tasks.parent_reference_validator.encryption_service.decrypt",
            return_value="token",
        ), patch(
            "app.tasks.parent_reference_validator.jira_oauth_service.get_issue",
            new=AsyncMock(
                return_value={
                    "key": "PRM-77",
                    "fields": {
                        "issuetype": {"name": "Epic"},
                        "project": {"key": "PRM"},
                        "status": {"name": "To Do"},
                    },
                }
            ),
        ):
            resolved = await resolve_parent_reference(
                project=project,
                parent_external_id="PRM-77",
            )

        self.assertTrue(bool(resolved.get("exists")))
        self.assertEqual(str(resolved.get("source") or ""), "jira_live")
        self.assertEqual(str(resolved.get("provider_type") or ""), "Epic")
        self.assertEqual(str(resolved.get("project_key") or ""), "PRM")

    async def test_resolve_parent_reference_returns_not_found_when_missing_everywhere(self):
        project = {"project_id": "proj-2000-2000"}

        class _FakeCollection:
            async def find_one(self, *_args, **_kwargs):
                return None

        with patch(
            "app.tasks.parent_reference_validator.get_brain_collection",
            return_value=_FakeCollection(),
        ):
            resolved = await resolve_parent_reference(
                project=project,
                parent_external_id="VALUE1-2",
            )

        self.assertFalse(bool(resolved.get("exists")))
        self.assertEqual(str(resolved.get("source") or ""), "unverified")
        self.assertEqual(str(resolved.get("reason") or ""), "parent_not_found")

    def test_not_available_message_contains_parent_key(self):
        message = build_parent_reference_not_available_message("value1-2")
        self.assertIn("VALUE1-2", message)
        self.assertIn("not available", message.lower())


if __name__ == "__main__":
    unittest.main()
