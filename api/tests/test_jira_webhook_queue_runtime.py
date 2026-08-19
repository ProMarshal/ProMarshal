import importlib
import unittest
from unittest.mock import AsyncMock, patch
import json


jira_webhooks_module = importlib.import_module("app.routes.jira_webhooks")


class JiraWebhookQueueRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_dramatiq_when_enabled(self):
        payload = {"issue": {"key": "ABC-1"}}
        raw_body = json.dumps(payload).encode("utf-8")

        with patch.object(
            jira_webhooks_module,
            "dramatiq_enqueue",
            return_value={"accepted": True, "job_id": "dramatiq-msg-1"},
        ) as dramatiq_mock:
            result = await jira_webhooks_module.receive_jira_webhook(
                project_id="proj-1111-2222",
                raw_body=raw_body,
            )

        self.assertEqual(result, {"ok": True})
        dramatiq_mock.assert_called_once()
        queued_kwargs = dramatiq_mock.call_args.kwargs["kwargs"]
        self.assertEqual(queued_kwargs["project_id"], "proj-1111-2222")
        self.assertEqual(queued_kwargs["payload"], payload)

    async def test_falls_back_to_inline_when_dramatiq_enqueue_fails_and_rq_unavailable(self):
        payload = {"issue": {"key": "ABC-2"}}
        raw_body = json.dumps(payload).encode("utf-8")

        with patch.object(
            jira_webhooks_module,
            "dramatiq_enqueue",
            return_value={"accepted": False, "reason": "enqueue_failed"},
        ), patch.object(
            jira_webhooks_module,
            "process_jira_webhook",
            new=AsyncMock(return_value=None),
        ) as inline_mock:
            result = await jira_webhooks_module.receive_jira_webhook(
                project_id="proj-3333-4444",
                raw_body=raw_body,
            )

        self.assertEqual(result, {"ok": True})
        inline_mock.assert_awaited_once_with(payload, project_id="proj-3333-4444")


if __name__ == "__main__":
    unittest.main()
