import unittest

from app.action_items.comment_mutation_event import (
    normalize_comment_mutation_event,
    validate_comment_mutation_event,
)


class CommentMutationEventContractTests(unittest.TestCase):
    def test_normalize_maps_legacy_posted_comment_text(self):
        event = normalize_comment_mutation_event(
            {
                "provider": "jira",
                "project_id": "proj-1234-5678",
                "task_key": "ABC-1",
                "posted_comment_text": "Please follow up",
                "actor_user_id": "u-1",
            }
        )
        self.assertEqual(event["comment_text"], "Please follow up")
        self.assertEqual(event["task_key"], "ABC-1")
        self.assertEqual(event["provider"], "jira")

    def test_validate_requires_project_task_and_comment(self):
        missing_project = validate_comment_mutation_event(
            {"project_id": "", "task_key": "ABC-1", "comment_text": "x"}
        )
        self.assertEqual(missing_project, "missing_project_id")

        missing_task = validate_comment_mutation_event(
            {"project_id": "proj-1", "task_key": "", "comment_text": "x"}
        )
        self.assertEqual(missing_task, "missing_task_key")

        missing_comment = validate_comment_mutation_event(
            {"project_id": "proj-1", "task_key": "ABC-1", "comment_text": ""}
        )
        self.assertEqual(missing_comment, "missing_comment_text")

        valid = validate_comment_mutation_event(
            {"project_id": "proj-1", "task_key": "ABC-1", "comment_text": "follow up"}
        )
        self.assertIsNone(valid)


if __name__ == "__main__":
    unittest.main()

