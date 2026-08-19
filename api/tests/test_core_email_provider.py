import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core import email as email_module


class _FakeAsyncClient:
    def __init__(self, recorder):
        self._recorder = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self._recorder["url"] = url
        self._recorder["headers"] = headers or {}
        self._recorder["json"] = json or {}
        return SimpleNamespace(status_code=202, text='{"message":"queued"}')


class EmailProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_zeptomail_delivery_uses_expected_header_and_payload(self):
        recorder = {}
        with patch("app.core.email.settings.email_provider", "zeptomail"), patch(
            "app.core.email.settings.zeptomail_api_url",
            "https://api.zeptomail.com/v1.1/email",
        ), patch("app.core.email.settings.zeptomail_api_key", "test-token"), patch(
            "app.core.email.settings.email_from",
            "ProMarshal <support@promarshal.ai>",
        ), patch("app.core.email.httpx.AsyncClient", side_effect=lambda *args, **kwargs: _FakeAsyncClient(recorder)):
            sent = await email_module._send_email(
                to_email="alice@example.com",
                subject="Subject",
                html_content="<p>Hello</p>",
                text_content="Hello",
            )

        self.assertTrue(sent)
        self.assertEqual(recorder["url"], "https://api.zeptomail.com/v1.1/email")
        self.assertEqual(recorder["headers"]["Authorization"], "Zoho-enczapikey test-token")
        self.assertEqual(recorder["json"]["from"]["address"], "support@promarshal.ai")
        self.assertEqual(recorder["json"]["from"]["name"], "ProMarshal")
        self.assertEqual(
            recorder["json"]["to"][0]["email_address"]["address"],
            "alice@example.com",
        )
        self.assertEqual(recorder["json"]["subject"], "Subject")

    async def test_send_invite_email_routes_through_shared_send_helper(self):
        with patch("app.core.email._send_email", new=AsyncMock(return_value=True)) as send_mock:
            sent = await email_module.send_invite_email(
                to_email="bob@example.com",
                project_name="Project X",
                inviter_name="Alice",
                invite_token="token-123",
                role="member",
            )

        self.assertTrue(sent)
        kwargs = send_mock.await_args.kwargs
        self.assertEqual(kwargs["to_email"], "bob@example.com")
        self.assertEqual(kwargs["subject"], "You're invited to join Project X on ProMarshal")
        self.assertIn("/invite/token-123", kwargs["html_content"])
        self.assertIn("Alice", kwargs["text_content"])


if __name__ == "__main__":
    unittest.main()
