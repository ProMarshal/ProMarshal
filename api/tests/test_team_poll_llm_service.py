import asyncio
from dataclasses import dataclass

from app.core.config import settings
from app.llm import LLMGatewayResponse
from app.team_poll import llm_service


@dataclass
class _FakeGateway:
    response: LLMGatewayResponse

    async def generate(self, _request):  # pragma: no cover - behavior tested via consumer
        return self.response


def test_summarize_team_poll_responses_uses_gateway_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "team_poll_llm_summary_enabled", True, raising=False)
    fake_gateway = _FakeGateway(
        response=LLMGatewayResponse(
            ok=True,
            text='{"heading":"Sprint Pulse","grouped_lines":["Alice, Bob: blockers cleared"]}',
            model="openai/gpt-4o-mini",
        )
    )
    monkeypatch.setattr(llm_service, "get_shared_llm_gateway", lambda: fake_gateway)

    result = asyncio.run(
        llm_service.summarize_team_poll_responses(
            question_text="How is sprint progress?",
            responses=["Alice: blocked", "Bob: blocked"],
        )
    )

    assert result["source"] == "llm"
    assert result["heading"] == "Sprint Pulse"
    assert result["grouped_lines"] == ["Alice, Bob: blockers cleared"]


def test_summarize_team_poll_responses_falls_back_when_gateway_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "team_poll_llm_summary_enabled", True, raising=False)
    monkeypatch.setattr(llm_service, "get_shared_llm_gateway", lambda: None)
    result = asyncio.run(
        llm_service.summarize_team_poll_responses(
            question_text="How is sprint progress?",
            responses=["Alice: blocked", "Bob: blocked"],
        )
    )

    assert result["source"] == "deterministic"
    assert isinstance(result["grouped_lines"], list)
