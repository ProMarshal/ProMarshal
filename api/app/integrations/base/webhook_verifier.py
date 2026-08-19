"""Shared webhook verifier abstractions."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from abc import ABC, abstractmethod
from typing import Mapping

from fastapi import HTTPException, Request, status

from app.core.config import settings

logger = logging.getLogger(__name__)


def _is_strict_environment() -> bool:
    env = str(getattr(settings, "environment", "") or "").strip().lower()
    return env not in {"", "development", "dev", "local", "test", "testing"}


def _handle_missing_secret(integration_name: str) -> None:
    """
    Fail-closed in strict environments, warn-and-allow in dev-like environments.
    """
    if _is_strict_environment():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{integration_name} webhook secret is not configured",
        )
    logger.warning(
        "[SECURITY] %s webhook secret missing in dev/local environment; allowing unsigned requests.",
        integration_name,
    )


class BaseWebhookVerifier(ABC):
    """Abstract verifier contract for inbound webhook requests."""

    @property
    @abstractmethod
    def integration_name(self) -> str:
        """Short integration identifier for logs/error messaging."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True when required signer material exists."""

    @abstractmethod
    async def verify(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        request: Request | None = None,
    ) -> None:
        """Verify inbound request and raise HTTPException on failure."""


class HmacWebhookVerifier(BaseWebhookVerifier, ABC):
    """Base verifier for HMAC-SHA256 webhook signatures."""

    @abstractmethod
    def get_secret(self) -> str:
        """Return signing secret for this integration."""

    @abstractmethod
    def extract_signature(self, headers: Mapping[str, str]) -> str:
        """Extract signature value from request headers."""

    @abstractmethod
    def build_signing_message(self, raw_body: bytes, headers: Mapping[str, str]) -> bytes:
        """Build provider-specific preimage used for HMAC digest computation."""

    def normalize_signature(self, signature: str) -> str:
        value = str(signature or "").strip()
        for prefix in ("sha256=", "v0="):
            if value.startswith(prefix):
                return value[len(prefix):]
        return value

    def is_configured(self) -> bool:
        return bool(str(self.get_secret() or "").strip())

    async def verify(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        request: Request | None = None,
    ) -> None:
        secret = str(self.get_secret() or "").strip()
        if not secret:
            _handle_missing_secret(self.integration_name)
            return

        sig_header = str(self.extract_signature(headers) or "").strip()
        if not sig_header:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Missing {self.integration_name} signature header",
            )

        message = self.build_signing_message(raw_body, headers)
        computed = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        provided = self.normalize_signature(sig_header)
        if not hmac.compare_digest(provided, computed):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid {self.integration_name} webhook signature",
            )


class ReplayProtectionMixin:
    """Mixin for timestamp-based replay window checks."""

    replay_window_seconds: int = 300

    def check_replay(self, timestamp_header: str) -> None:
        try:
            ts = int(str(timestamp_header or "").strip())
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid request timestamp",
            ) from exc
        age = abs(time.time() - ts)
        if age > int(self.replay_window_seconds):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Request timestamp outside acceptable window (possible replay)",
            )
