import asyncio
from typing import Optional
from unittest.mock import patch

from app.action_items import title_extractor
from app.action_items.title_extractor import resolve_action_item_candidate
from app.core.config import settings
from app.llm import LLMGatewayRequest, LLMGatewayResponse


class _FakeGateway:
    def __init__(self, response: LLMGatewayResponse):
        self.response = response
        self.calls = 0

    async def generate(self, request: LLMGatewayRequest, *, on_health_event: Optional[object] = None) -> LLMGatewayResponse:
        self.calls += 1
        return self.response


def test_action_item_extractor_uses_gateway_when_valid():
    prev_enabled = settings.action_items_title_rewrite_enabled
    prev_min_conf = settings.action_items_title_rewrite_min_confidence
    try:
        settings.action_items_title_rewrite_enabled = True
        settings.action_items_title_rewrite_min_confidence = 0.6
        fake_gateway = _FakeGateway(
            LLMGatewayResponse(
                ok=True,
                text='{"title":"Send meeting invite for customer tomorrow at 10am","confidence":0.92,"due_type":"by_date","due_date_iso":"2026-03-16"}',
                retry_count=0,
            )
        )
        with patch.object(title_extractor, "get_shared_llm_gateway", return_value=fake_gateway):
            result = asyncio.run(
                resolve_action_item_candidate(
                    raw_text="create action item to send meeting invite for customer tomorrow at 10am and assign to me"
                )
            )
    finally:
        settings.action_items_title_rewrite_enabled = prev_enabled
        settings.action_items_title_rewrite_min_confidence = prev_min_conf

    assert result.source == "llm_gateway"
    assert "tomorrow" in result.title.lower() or "10am" in result.title.lower()
    assert result.due_type == "by_date"
    assert fake_gateway.calls == 1


def test_action_item_extractor_falls_back_when_gateway_invalid_json():
    prev_enabled = settings.action_items_title_rewrite_enabled
    try:
        settings.action_items_title_rewrite_enabled = True
        fake_gateway = _FakeGateway(LLMGatewayResponse(ok=True, text="not-json"))
        with patch.object(title_extractor, "get_shared_llm_gateway", return_value=fake_gateway):
            result = asyncio.run(
                resolve_action_item_candidate(
                    raw_text="create action item to send meeting invite for customer tomorrow at 10am"
                )
            )
    finally:
        settings.action_items_title_rewrite_enabled = prev_enabled

    assert result.source == "deterministic"
    assert "tomorrow" in result.title.lower() or "10am" in result.title.lower()


def test_action_item_extractor_retries_and_falls_back_on_low_confidence():
    prev_enabled = settings.action_items_title_rewrite_enabled
    prev_retries = settings.action_items_title_rewrite_max_retries
    prev_backoff = settings.action_items_title_rewrite_backoff_ms
    prev_jitter = settings.action_items_title_rewrite_retry_jitter_ms
    prev_min_conf = settings.action_items_title_rewrite_min_confidence
    try:
        settings.action_items_title_rewrite_enabled = True
        settings.action_items_title_rewrite_max_retries = 2
        settings.action_items_title_rewrite_backoff_ms = "1,1"
        settings.action_items_title_rewrite_retry_jitter_ms = 0
        settings.action_items_title_rewrite_min_confidence = 0.9
        fake_gateway = _FakeGateway(
            LLMGatewayResponse(
                ok=True,
                text='{"title":"Send meeting invite","confidence":0.2,"due_type":"no_due_date"}',
            )
        )
        with patch.object(title_extractor, "get_shared_llm_gateway", return_value=fake_gateway), patch.object(
            title_extractor.random, "randint", return_value=0
        ):
            result = asyncio.run(
                resolve_action_item_candidate(
                    raw_text="create action item to send meeting invite for customer tomorrow at 10am"
                )
            )
    finally:
        settings.action_items_title_rewrite_enabled = prev_enabled
        settings.action_items_title_rewrite_max_retries = prev_retries
        settings.action_items_title_rewrite_backoff_ms = prev_backoff
        settings.action_items_title_rewrite_retry_jitter_ms = prev_jitter
        settings.action_items_title_rewrite_min_confidence = prev_min_conf

    assert result.source == "deterministic"
    assert fake_gateway.calls == 3
