import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.integrations.slack.context_footer import (
    append_project_context_footer,
    build_project_context_footer,
)


class SlackContextFooterTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_footer_returns_none_for_single_project_membership(self):
        with patch(
            "app.integrations.slack.context_footer.list_slack_member_projects",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    ok=True,
                    projects=[{"project_id": "proj-1", "project_name": "Alpha"}],
                    active_project_id="proj-1",
                )
            ),
        ):
            footer = await build_project_context_footer(
                db=object(),
                team_id="T1",
                slack_user_id="U1",
                project={"project_id": "proj-1", "project_name": "Alpha"},
            )
        self.assertIsNone(footer)

    async def test_build_footer_returns_template_for_multi_project_membership(self):
        with patch(
            "app.integrations.slack.context_footer.list_slack_member_projects",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    ok=True,
                    projects=[
                        {"project_id": "proj-1", "project_name": "Alpha"},
                        {"project_id": "proj-2", "project_name": "Beta"},
                    ],
                    active_project_id="proj-1",
                )
            ),
        ):
            footer = await build_project_context_footer(
                db=object(),
                team_id="T1",
                slack_user_id="U1",
                project={"project_id": "proj-2", "project_name": "Beta"},
            )
        self.assertEqual(
            footer,
            'Current Project: Beta (proj-2) | To switch, type: "switch project"',
        )

    def test_append_footer_appends_once(self):
        body = "Work item created."
        footer = 'Current Project: Beta (proj-2) | To switch, type: "switch project"'
        with_footer = append_project_context_footer(body, footer)
        self.assertIn(footer, with_footer)
        with_footer_again = append_project_context_footer(with_footer, footer)
        self.assertEqual(with_footer, with_footer_again)


if __name__ == "__main__":
    unittest.main()

