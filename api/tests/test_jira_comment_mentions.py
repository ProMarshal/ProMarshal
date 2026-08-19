import unittest
from unittest.mock import AsyncMock, patch

from app.integrations.jira.oauth import JiraOAuthService
from app.tasks.providers.jira_adapter import JiraAdapter


def _build_project(*, members=None):
    return {
        "members": members or [],
        "integrations": {
            "jira": {
                "cloud_id": "cloud-1",
                "site_url": "https://example.atlassian.net",
                "access_token": "",
                "refresh_token": None,
            }
        },
    }


class JiraCommentMentionTests(unittest.IsolatedAsyncioTestCase):
    def _build_adapter(self, *, members=None) -> JiraAdapter:
        return JiraAdapter(_build_project(members=members), access_token_override="token")

    async def test_build_comment_mentions_uses_project_member_mapping(self):
        adapter = self._build_adapter(
            members=[
                {
                    "name": "Sridevi",
                    "email": "sridevi@example.com",
                    "status": "active",
                    "integration_ids": {"jira_account_id": "acc-sridevi"},
                }
            ]
        )
        with patch.object(adapter, "get_workspace_users", new=AsyncMock(return_value=[])):
            entities = await adapter._build_comment_mentions("@sridevi please add logs")

        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0]["account_id"], "acc-sridevi")

    async def test_build_comment_mentions_falls_back_to_workspace_user(self):
        adapter = self._build_adapter(members=[])
        with patch.object(
            adapter,
            "get_workspace_users",
            new=AsyncMock(
                return_value=[
                    {
                        "accountId": "acc-workspace-1",
                        "displayName": "Sridevi Raj",
                        "emailAddress": "sridevi@example.com",
                    }
                ]
            ),
        ):
            entities = await adapter._build_comment_mentions("@sridevi please add logs")

        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0]["account_id"], "acc-workspace-1")

    async def test_build_comment_mentions_skips_ambiguous_workspace_match(self):
        adapter = self._build_adapter(members=[])
        with patch.object(
            adapter,
            "get_workspace_users",
            new=AsyncMock(
                return_value=[
                    {
                        "accountId": "acc-1",
                        "displayName": "Sridevi Raj",
                        "emailAddress": "sridevi@example.com",
                    },
                    {
                        "accountId": "acc-2",
                        "displayName": "Sridevi Kumar",
                        "emailAddress": "sridevi2@example.com",
                    },
                ]
            ),
        ):
            entities = await adapter._build_comment_mentions("@sridevi please add logs")

        self.assertEqual(entities, [])

    def test_comment_adf_builder_preserves_plain_text_roundtrip(self):
        service = JiraOAuthService()
        body = service._build_comment_adf_body(
            comment_text="@sridevi please add logs",
            mention_entities=[
                {
                    "start": 0,
                    "end": 8,
                    "account_id": "acc-sridevi",
                    "text": "@sridevi",
                }
            ],
        )
        content = (((body.get("body") or {}).get("content") or [{}])[0].get("content") or [])
        rendered = JiraOAuthService._extract_adf_plain_text(content)
        self.assertEqual(rendered, "@sridevi please add logs")


if __name__ == "__main__":
    unittest.main()

