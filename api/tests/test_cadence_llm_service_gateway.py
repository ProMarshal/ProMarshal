import asyncio
from dataclasses import dataclass

from app.cadence import llm_service
from app.llm import LLMGatewayResponse


@dataclass
class _FakeGateway:
    response: LLMGatewayResponse

    async def generate(self, _request):  # pragma: no cover - exercised by consumer
        return self.response


def test_call_llm_uses_shared_gateway(monkeypatch):
    fake_gateway = _FakeGateway(
        response=LLMGatewayResponse(
            ok=True,
            text="Next up:",
            provider="openai",
            model="gpt-4o-mini",
        )
    )
    monkeypatch.setattr(llm_service, "get_shared_llm_gateway", lambda: fake_gateway)
    result = asyncio.run(llm_service._call_llm("hello", max_tokens=20))
    assert result == "Next up:"


def test_call_llm_returns_none_on_gateway_error(monkeypatch):
    fake_gateway = _FakeGateway(
        response=LLMGatewayResponse(
            ok=False,
            text="",
        )
    )
    monkeypatch.setattr(llm_service, "get_shared_llm_gateway", lambda: fake_gateway)
    result = asyncio.run(llm_service._call_llm("hello", max_tokens=20))
    assert result is None

