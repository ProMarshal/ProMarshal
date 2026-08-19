"""Shared Slack ingress security validation helpers."""

from __future__ import annotations

from typing import Mapping

from app.integrations.slack.webhook_verifier import SlackWebhookVerifier

_slack_webhook_verifier = SlackWebhookVerifier()


def enforce_slack_request_auth(
    *,
    raw_body: bytes,
    headers: Mapping[str, str],
    endpoint: str,
) -> None:
    """Backwards-compatible wrapper over the shared verifier framework."""
    _slack_webhook_verifier.verify(raw_body, headers)

