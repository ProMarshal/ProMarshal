"""Shared LLM gateway provider helpers."""

from __future__ import annotations

from typing import Optional

from app.llm.gateway import LLMGateway
from app.llm.litellm_gateway import LiteLLMGateway

_SHARED_GATEWAY: Optional[LLMGateway] = None


def get_shared_llm_gateway() -> LLMGateway:
    global _SHARED_GATEWAY
    if _SHARED_GATEWAY is None:
        _SHARED_GATEWAY = LiteLLMGateway()
    return _SHARED_GATEWAY
