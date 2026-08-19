import unittest
from unittest.mock import patch

from app.tasks.provider_parent_rules import (
    filter_candidates_by_parent_rule,
    resolve_parent_rule_filter,
)


class ProviderParentRulesTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_parent_rule_filter_uses_metadata_when_present(self):
        project = {
            "project_id": "proj-2000-2000",
            "integrations": {"jira": {"default_project_key": "PRM"}},
        }
        create_payload = {
            "provider": "jira",
            "provider_type": "Sub-task",
        }
        metadata_doc = {
            "allowed_create_types": ["Task", "Story", "Subtask"],
            "type_rules": {
                "subtask": {
                    "provider_type": "Subtask",
                    "parent": {
                        "required": True,
                        "allowed_parent_provider_types": ["Task"],
                    },
                }
            },
        }

        class _FakeCollection:
            async def find_one(self, *_args, **_kwargs):
                return metadata_doc

        with patch("app.tasks.provider_parent_rules.get_brain_collection", return_value=_FakeCollection()):
            resolved = await resolve_parent_rule_filter(project=project, create_payload=create_payload)

        self.assertTrue(bool(resolved.get("parent_allowed")))
        self.assertTrue(bool(resolved.get("parent_required")))
        self.assertEqual(resolved.get("allowed_parent_provider_types"), ["Task"])
        self.assertEqual(resolved.get("source"), "metadata")

    async def test_resolve_parent_rule_filter_falls_back_when_metadata_missing(self):
        project = {
            "project_id": "proj-2000-2000",
            "integrations": {"jira": {"default_project_key": "PRM"}},
        }
        create_payload = {
            "provider": "jira",
            "provider_type": "subtask",
        }

        class _FakeCollection:
            async def find_one(self, *_args, **_kwargs):
                return None

        with patch("app.tasks.provider_parent_rules.get_brain_collection", return_value=_FakeCollection()):
            resolved = await resolve_parent_rule_filter(project=project, create_payload=create_payload)

        self.assertTrue(bool(resolved.get("parent_allowed")))
        self.assertTrue(bool(resolved.get("parent_required")))
        allowed = list(resolved.get("allowed_parent_provider_types") or [])
        self.assertTrue(len(allowed) >= 1)
        self.assertNotIn("Epic", allowed)
        self.assertIn("source", resolved)

    async def test_resolve_parent_rule_filter_merges_incomplete_metadata_with_fallback(self):
        project = {
            "project_id": "proj-2000-2000",
            "integrations": {"jira": {"default_project_key": "PRM"}},
        }
        create_payload = {
            "provider": "jira",
            "provider_type": "subtask",
        }
        metadata_doc = {
            "allowed_create_types": ["Task", "Story", "Subtask"],
            "type_rules": {
                "subtask": {
                    "provider_type": "Subtask",
                    "parent": {
                        "required": True,
                        "allowed_parent_provider_types": [],
                    },
                }
            },
        }

        class _FakeCollection:
            async def find_one(self, *_args, **_kwargs):
                return metadata_doc

        with patch("app.tasks.provider_parent_rules.get_brain_collection", return_value=_FakeCollection()):
            resolved = await resolve_parent_rule_filter(project=project, create_payload=create_payload)

        self.assertTrue(bool(resolved.get("parent_allowed")))
        self.assertTrue(bool(resolved.get("parent_required")))
        self.assertIn("Task", list(resolved.get("allowed_parent_provider_types") or []))
        self.assertEqual(resolved.get("source"), "metadata_plus_fallback_allowed")

    async def test_resolve_parent_rule_filter_sanitizes_epic_for_subtask_even_when_metadata_has_it(self):
        project = {
            "project_id": "proj-2000-2000",
            "integrations": {"jira": {"default_project_key": "PRM"}},
        }
        create_payload = {
            "provider": "jira",
            "provider_type": "subtask",
        }
        metadata_doc = {
            "allowed_create_types": ["Task", "Story", "Subtask", "Epic"],
            "type_rules": {
                "subtask": {
                    "provider_type": "Subtask",
                    "parent": {
                        "required": False,
                        "allowed_parent_provider_types": ["Task", "Story", "Epic"],
                    },
                }
            },
        }

        class _FakeCollection:
            async def find_one(self, *_args, **_kwargs):
                return metadata_doc

        with patch("app.tasks.provider_parent_rules.get_brain_collection", return_value=_FakeCollection()):
            resolved = await resolve_parent_rule_filter(project=project, create_payload=create_payload)

        allowed = list(resolved.get("allowed_parent_provider_types") or [])
        self.assertTrue(bool(resolved.get("parent_allowed")))
        self.assertTrue(bool(resolved.get("parent_required")))
        self.assertIn("Task", allowed)
        self.assertIn("Story", allowed)
        self.assertNotIn("Epic", allowed)

    async def test_resolve_parent_rule_filter_marks_epic_parent_not_allowed(self):
        project = {
            "project_id": "proj-2000-2000",
            "integrations": {"jira": {"default_project_key": "PRM"}},
        }
        create_payload = {
            "provider": "jira",
            "provider_type": "epic",
        }
        metadata_doc = {
            "allowed_create_types": ["Task", "Story", "Subtask", "Epic"],
            "type_rules": {},
        }

        class _FakeCollection:
            async def find_one(self, *_args, **_kwargs):
                return metadata_doc

        with patch("app.tasks.provider_parent_rules.get_brain_collection", return_value=_FakeCollection()):
            resolved = await resolve_parent_rule_filter(project=project, create_payload=create_payload)

        self.assertFalse(bool(resolved.get("parent_allowed")))
        self.assertFalse(bool(resolved.get("parent_required")))
        self.assertEqual(list(resolved.get("allowed_parent_provider_types") or []), [])

    def test_filter_candidates_by_parent_rule_is_fail_safe(self):
        candidates = [
            {"external_id": "PRM-1", "provider_type": "Task"},
            {"external_id": "PRM-2", "provider_type": "Story"},
        ]
        filtered = filter_candidates_by_parent_rule(
            candidates=candidates,
            parent_rule_filter={"allowed_parent_provider_types": ["Bug"]},
        )
        # No candidates matched; fail-safe returns original list.
        self.assertEqual(len(filtered), 2)
        self.assertEqual(filtered[0]["external_id"], "PRM-1")


if __name__ == "__main__":
    unittest.main()
