"""Registry and FastAPI dependency helpers for webhook verifiers."""

from __future__ import annotations

from typing import Dict

from fastapi import Request

from app.integrations.base.webhook_verifier import BaseWebhookVerifier

_registry: Dict[str, BaseWebhookVerifier] = {}


def register_webhook_verifier(name: str, verifier: BaseWebhookVerifier) -> None:
    key = str(name or "").strip().lower()
    if not key:
        raise ValueError("Webhook verifier name is required")
    _registry[key] = verifier


def get_webhook_verifier(name: str) -> BaseWebhookVerifier:
    key = str(name or "").strip().lower()
    verifier = _registry.get(key)
    if verifier is None:
        raise RuntimeError(f"No webhook verifier registered for '{key}'")
    return verifier


def require_webhook_auth(integration: str):
    """FastAPI dependency factory that validates webhook request auth."""

    async def _dependency(request: Request) -> bytes:
        raw_body = await request.body()
        verifier = get_webhook_verifier(integration)
        await verifier.verify(raw_body, request.headers, request=request)
        return raw_body

    return _dependency
