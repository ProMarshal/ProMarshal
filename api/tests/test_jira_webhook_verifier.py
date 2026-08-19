import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.integrations.jira.webhook_verifier import JiraWebhookVerifier


class JiraWebhookVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_verify_accepts_valid_project_token(self):
        verifier = JiraWebhookVerifier()
        fake_db = SimpleNamespace(
            projects=SimpleNamespace(
                find_one=AsyncMock(
                    return_value={"integrations": {"jira": {"webhook_token": "token-123"}}}
                )
            )
        )
        fake_request = SimpleNamespace(
            path_params={"project_id": "proj-1111-2222"},
            query_params={"token": "token-123"},
        )

        with patch("app.integrations.jira.webhook_verifier.get_database", return_value=fake_db):
            await verifier.verify(b"{}", {}, request=fake_request)

    async def test_verify_rejects_missing_token(self):
        verifier = JiraWebhookVerifier()
        fake_request = SimpleNamespace(path_params={"project_id": "proj-1111-2222"}, query_params={})
        with self.assertRaises(HTTPException) as ctx:
            await verifier.verify(b"{}", {}, request=fake_request)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("Missing jira webhook token", str(ctx.exception.detail))

    async def test_verify_rejects_invalid_token(self):
        verifier = JiraWebhookVerifier()
        fake_db = SimpleNamespace(
            projects=SimpleNamespace(
                find_one=AsyncMock(
                    return_value={"integrations": {"jira": {"webhook_token": "token-123"}}}
                )
            )
        )
        fake_request = SimpleNamespace(
            path_params={"project_id": "proj-1111-2222"},
            query_params={"token": "token-999"},
        )
        with patch("app.integrations.jira.webhook_verifier.get_database", return_value=fake_db):
            with self.assertRaises(HTTPException) as ctx:
                await verifier.verify(b"{}", {}, request=fake_request)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("Invalid jira webhook token", str(ctx.exception.detail))

