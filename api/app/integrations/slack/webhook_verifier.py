"""Slack webhook verifier implementation."""

from __future__ import annotations

from typing import Mapping

from app.core.config import settings
from app.integrations.base.webhook_verifier import HmacWebhookVerifier, ReplayProtectionMixin


class SlackWebhookVerifier(ReplayProtectionMixin, HmacWebhookVerifier):
    integration_name = "slack"

    def get_secret(self) -> str:
        return str(getattr(settings, "slack_signing_secret", "") or "").strip()

    def extract_signature(self, headers: Mapping[str, str]) -> str:
        return str(headers.get("X-Slack-Signature") or "").strip()

    def build_signing_message(self, raw_body: bytes, headers: Mapping[str, str]) -> bytes:
        timestamp = str(headers.get("X-Slack-Request-Timestamp") or "").strip()
        self.check_replay(timestamp)
        return b"v0:" + timestamp.encode("utf-8") + b":" + raw_body

    def normalize_signature(self, signature: str) -> str:
        value = str(signature or "").strip()
        if value.startswith("v0="):
            return value[3:]
        return value

