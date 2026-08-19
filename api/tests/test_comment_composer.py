import unittest

from app.tasks.comment_composer import compose_comment_text


class CommentComposerTests(unittest.TestCase):
    def test_composes_requestor_command(self):
        self.assertEqual(
            compose_comment_text("ask requestor to attach error logs"),
            "Please attach error logs.",
        )

    def test_composes_named_target_command(self):
        self.assertEqual(
            compose_comment_text("ask sridevi to attach error screenshots"),
            "@sridevi, please attach error screenshots.",
        )

    def test_leaves_plain_comment_unchanged(self):
        self.assertEqual(
            compose_comment_text("Please attach logs to this task."),
            "Please attach logs to this task.",
        )


if __name__ == "__main__":
    unittest.main()
