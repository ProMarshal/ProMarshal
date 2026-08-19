import unittest

from app.cortex.plans.planner import ActionPlanBuilder
from app.domain.capabilities.deterministic.pattern_registry import pattern_registry
from app.domain.capabilities.jira_bundles import get_jira_deterministic_pattern_specs


class CortexDeterministicCommentExtractionTests(unittest.TestCase):
    def setUp(self):
        pattern_registry.reset()
        pattern_registry.register_many(
            get_jira_deterministic_pattern_specs(),
            provider="jira",
            bundle_id="test.jira.comment.patterns",
        )

    def tearDown(self):
        pattern_registry.reset()

    def test_update_comments_phrase_extracts_comment_for_jira_add_comment(self):
        builder = ActionPlanBuilder()
        interpretation = builder._interpret_deterministic(
            text="update prm59 comments, need more info on error and logs",
            visible_capabilities={"workitem_add_comment"},
        )
        matches = builder._extract_action_matches(interpretation)
        steps, unresolved_targets, missing_required = builder._build_steps_from_matches(
            text="update prm59 comments, need more info on error and logs",
            matches=matches,
        )

        self.assertEqual(unresolved_targets, 0)
        self.assertEqual(missing_required, 0)
        comment_steps = [step for step in steps if step.tool_name == "jira_add_comment"]
        self.assertEqual(len(comment_steps), 1)
        self.assertEqual(comment_steps[0].args.get("external_id"), "PRM-59")
        self.assertEqual(
            comment_steps[0].args.get("comment"),
            "need more info on error and logs",
        )

    def test_followup_style_comment_phrase_extracts_required_comment_slot(self):
        builder = ActionPlanBuilder()
        interpretation = builder._interpret_deterministic(
            text="task PRM-59. comment: please attach logs and stack trace",
            visible_capabilities={"workitem_add_comment"},
        )
        matches = builder._extract_action_matches(interpretation)
        steps, unresolved_targets, missing_required = builder._build_steps_from_matches(
            text="task PRM-59. comment: please attach logs and stack trace",
            matches=matches,
        )

        self.assertEqual(unresolved_targets, 0)
        self.assertEqual(missing_required, 0)
        comment_steps = [step for step in steps if step.tool_name == "jira_add_comment"]
        self.assertEqual(len(comment_steps), 1)
        self.assertEqual(comment_steps[0].args.get("external_id"), "PRM-59")
        self.assertEqual(
            comment_steps[0].args.get("comment"),
            "please attach logs and stack trace",
        )

    def test_post_comment_on_task_phrase_extracts_comment_slot(self):
        builder = ActionPlanBuilder()
        interpretation = builder._interpret_deterministic(
            text="post comment on PRM-59: please include exact repro steps",
            visible_capabilities={"workitem_add_comment"},
        )
        matches = builder._extract_action_matches(interpretation)
        steps, unresolved_targets, missing_required = builder._build_steps_from_matches(
            text="post comment on PRM-59: please include exact repro steps",
            matches=matches,
        )

        self.assertEqual(unresolved_targets, 0)
        self.assertEqual(missing_required, 0)
        comment_steps = [step for step in steps if step.tool_name == "jira_add_comment"]
        self.assertEqual(len(comment_steps), 1)
        self.assertEqual(comment_steps[0].args.get("external_id"), "PRM-59")
        self.assertEqual(
            comment_steps[0].args.get("comment"),
            "please include exact repro steps",
        )

    def test_issue_comments_dash_phrase_extracts_comment_slot(self):
        builder = ActionPlanBuilder()
        interpretation = builder._interpret_deterministic(
            text="issue PRM59 comments - attach startup logs and screenshots",
            visible_capabilities={"workitem_add_comment"},
        )
        matches = builder._extract_action_matches(interpretation)
        steps, unresolved_targets, missing_required = builder._build_steps_from_matches(
            text="issue PRM59 comments - attach startup logs and screenshots",
            matches=matches,
        )

        self.assertEqual(unresolved_targets, 0)
        self.assertEqual(missing_required, 0)
        comment_steps = [step for step in steps if step.tool_name == "jira_add_comment"]
        self.assertEqual(len(comment_steps), 1)
        self.assertEqual(comment_steps[0].args.get("external_id"), "PRM-59")
        self.assertEqual(
            comment_steps[0].args.get("comment"),
            "attach startup logs and screenshots",
        )


if __name__ == "__main__":
    unittest.main()
