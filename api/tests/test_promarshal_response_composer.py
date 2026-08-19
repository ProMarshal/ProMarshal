import asyncio
from dataclasses import dataclass

from app.core.config import settings
from app.llm import LLMGatewayResponse
from app.promarshal import response_composer


@dataclass
class _FakeGateway:
    response: LLMGatewayResponse

    async def generate(self, _request):  # pragma: no cover - behavior tested via consumer
        return self.response


def test_compose_user_response_uses_gateway_when_enabled(monkeypatch):
    prev_enabled = settings.team_poll_response_composer_enabled
    try:
        settings.team_poll_response_composer_enabled = True
        monkeypatch.setattr(
            response_composer,
            "get_shared_llm_gateway",
            lambda: _FakeGateway(LLMGatewayResponse(ok=True, text="*Result:*\nDone.")),
        )
        result = asyncio.run(
            response_composer.compose_user_response(
                capability="team_poll",
                user_message="close poll",
                outcome_payload={"ok": True},
                fallback_text="Poll closed.",
            )
        )
    finally:
        settings.team_poll_response_composer_enabled = prev_enabled

    assert result == "*Result:*\nDone."


def test_compose_user_response_falls_back_when_gateway_returns_error(monkeypatch):
    prev_enabled = settings.team_poll_response_composer_enabled
    try:
        settings.team_poll_response_composer_enabled = True
        monkeypatch.setattr(
            response_composer,
            "get_shared_llm_gateway",
            lambda: _FakeGateway(LLMGatewayResponse(ok=False, text="", error=None)),
        )
        result = asyncio.run(
            response_composer.compose_user_response(
                capability="team_poll",
                user_message="close poll",
                outcome_payload={"ok": True},
                fallback_text="Poll closed.",
            )
        )
    finally:
        settings.team_poll_response_composer_enabled = prev_enabled

    assert result == "Poll closed."


def test_compose_user_response_enabled_override_decouples_feature_flag(monkeypatch):
    prev_enabled = settings.team_poll_response_composer_enabled
    try:
        settings.team_poll_response_composer_enabled = False
        monkeypatch.setattr(
            response_composer,
            "get_shared_llm_gateway",
            lambda: _FakeGateway(LLMGatewayResponse(ok=True, text="Composed response")),
        )
        result = asyncio.run(
            response_composer.compose_user_response(
                capability="task_assistant_read",
                user_message="list my tasks",
                outcome_payload={"count": 1, "items": [{"external_id": "PRM-1"}]},
                fallback_text="Found 1 task.",
                enabled=True,
            )
        )
    finally:
        settings.team_poll_response_composer_enabled = prev_enabled

    assert result == "Composed response"


def test_compose_user_response_excludes_fallback_draft_when_disabled(monkeypatch):
    prev_enabled = settings.team_poll_response_composer_enabled
    captured = {}

    class _CaptureGateway:
        async def generate(self, request):
            captured["request"] = request
            return LLMGatewayResponse(ok=True, text="Done")

    try:
        settings.team_poll_response_composer_enabled = True
        monkeypatch.setattr(
            response_composer,
            "get_shared_llm_gateway",
            lambda: _CaptureGateway(),
        )
        asyncio.run(
            response_composer.compose_user_response(
                capability="task_assistant_read",
                user_message="list my tasks",
                outcome_payload={"count": 2, "items": [{"external_id": "PRM-1"}]},
                fallback_text="Found 2 tasks: ...",
                include_fallback_in_prompt=False,
            )
        )
    finally:
        settings.team_poll_response_composer_enabled = prev_enabled

    req = captured.get("request")
    assert req is not None
    user_msg = req.messages[1].content
    assert "Fallback draft:" not in user_msg
