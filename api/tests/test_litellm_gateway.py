import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from app.llm.gateway import (
    LLMEmbeddingRequest,
    LLMGatewayRequest,
    LLMMessage,
    LLMMessageRole,
    LLMToolCall,
    LLMToolSpec,
)
from app.llm.litellm_gateway import LiteLLMGateway


class _FakeLiteLLM:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    async def acompletion(self, **kwargs):
        self.calls.append(kwargs)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def aembedding(self, **kwargs):
        self.calls.append(kwargs)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class LiteLLMGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_unavailable_when_litellm_missing(self):
        gateway = LiteLLMGateway()
        with patch("app.llm.litellm_gateway._ensure_litellm", return_value=None):
            resp = await gateway.generate(
                LLMGatewayRequest(messages=[LLMMessage(role=LLMMessageRole.USER, content="hello")])
            )
        self.assertFalse(resp.ok)
        self.assertEqual(resp.error.error_code, "litellm_unavailable")

    async def test_retries_and_falls_back_to_second_model(self):
        success_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='[{"ok":true}]'))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        fake_litellm = _FakeLiteLLM([RuntimeError("rate limit"), success_response])
        gateway = LiteLLMGateway()
        with patch("app.llm.litellm_gateway._ensure_litellm", return_value=fake_litellm):
            resp = await gateway.generate(
                LLMGatewayRequest(
                    messages=[LLMMessage(role=LLMMessageRole.USER, content="hello")],
                    provider="openai",
                    model="gpt-4o-mini",
                    fallback_provider="openai",
                    fallback_model="gpt-4o",
                    retries=1,
                )
            )
        self.assertTrue(resp.ok)
        self.assertEqual(resp.retry_count, 1)
        self.assertEqual(resp.usage.total_tokens, 15)
        self.assertEqual(len(fake_litellm.calls), 2)
        self.assertEqual(fake_litellm.calls[0]["model"], "openai/gpt-4o-mini")
        self.assertEqual(fake_litellm.calls[1]["model"], "openai/gpt-4o")

    async def test_parses_tool_calls_and_finish_reason(self):
        success_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                function=SimpleNamespace(
                                    name="jira_search_work_items",
                                    arguments='{"status":"in_progress"}',
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=3, total_tokens=11),
        )
        fake_litellm = _FakeLiteLLM([success_response])
        gateway = LiteLLMGateway()
        with patch("app.llm.litellm_gateway._ensure_litellm", return_value=fake_litellm):
            resp = await gateway.generate(
                LLMGatewayRequest(
                    messages=[LLMMessage(role=LLMMessageRole.USER, content="list my tasks")],
                    tools=[
                        LLMToolSpec(
                            name="jira_search_work_items",
                            description="Search tasks",
                            json_schema={"type": "object", "properties": {"status": {"type": "string"}}},
                        )
                    ],
                    tool_choice="auto",
                    parallel_tool_calls=False,
                )
            )
        self.assertTrue(resp.ok)
        self.assertEqual(resp.finish_reason, "tool_calls")
        self.assertEqual(len(resp.tool_calls), 1)
        self.assertEqual(resp.tool_calls[0].name, "jira_search_work_items")
        self.assertEqual(resp.tool_calls[0].arguments.get("status"), "in_progress")
        self.assertIsInstance(fake_litellm.calls[0].get("tools"), list)
        self.assertEqual(fake_litellm.calls[0].get("tool_choice"), "auto")
        self.assertEqual(fake_litellm.calls[0].get("parallel_tool_calls"), False)

    async def test_serializes_assistant_message_tool_calls_in_request_history(self):
        success_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="done"))],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, total_tokens=6),
        )
        fake_litellm = _FakeLiteLLM([success_response])
        gateway = LiteLLMGateway()
        with patch("app.llm.litellm_gateway._ensure_litellm", return_value=fake_litellm):
            resp = await gateway.generate(
                LLMGatewayRequest(
                    messages=[
                        LLMMessage(role=LLMMessageRole.USER, content="list my tasks"),
                        LLMMessage(
                            role=LLMMessageRole.ASSISTANT,
                            content="",
                            tool_calls=(
                                LLMToolCall(
                                    call_id="call_1",
                                    name="jira_search_work_items",
                                    arguments_json='{"status":"in_progress"}',
                                ),
                            ),
                        ),
                        LLMMessage(
                            role=LLMMessageRole.TOOL,
                            content='{"items": []}',
                            tool_call_id="call_1",
                        ),
                    ],
                    retries=0,
                    timeout_seconds=5,
                )
            )
        self.assertTrue(resp.ok)
        outbound = fake_litellm.calls[0]["messages"]
        assistant = outbound[1]
        self.assertIn("tool_calls", assistant)
        self.assertEqual(assistant["tool_calls"][0]["id"], "call_1")
        self.assertEqual(assistant["tool_calls"][0]["type"], "function")
        self.assertEqual(assistant["tool_calls"][0]["function"]["name"], "jira_search_work_items")
        self.assertEqual(
            assistant["tool_calls"][0]["function"]["arguments"],
            '{"status":"in_progress"}',
        )

    async def test_hard_timeout_ceiling_returns_timeout_envelope(self):
        class _SlowLiteLLM:
            async def acompletion(self, **kwargs):
                await asyncio.sleep(2.0)

        gateway = LiteLLMGateway()
        with patch("app.llm.litellm_gateway._ensure_litellm", return_value=_SlowLiteLLM()):
            resp = await gateway.generate(
                LLMGatewayRequest(
                    messages=[LLMMessage(role=LLMMessageRole.USER, content="hello")],
                    retries=0,
                    timeout_seconds=1,
                )
            )
        self.assertFalse(resp.ok)
        self.assertIsNotNone(resp.error)
        self.assertEqual(resp.error.error_class.value, "timeout")

    async def test_embedding_success(self):
        success_response = SimpleNamespace(
            data=[
                {"embedding": [0.1, 0.2, 0.3]},
                {"embedding": [0.4, 0.5, 0.6]},
            ],
            usage=SimpleNamespace(prompt_tokens=6, completion_tokens=0, total_tokens=6),
        )
        fake_litellm = _FakeLiteLLM([success_response])
        gateway = LiteLLMGateway()
        with patch("app.llm.litellm_gateway._ensure_litellm", return_value=fake_litellm):
            resp = await gateway.embed(
                LLMEmbeddingRequest(
                    inputs=["one", "two"],
                    provider="openai",
                    model="text-embedding-3-small",
                    retries=0,
                )
            )
        self.assertTrue(resp.ok)
        self.assertEqual(len(resp.vectors), 2)
        self.assertEqual(fake_litellm.calls[0]["model"], "openai/text-embedding-3-small")

    async def test_embedding_empty_input_returns_invalid_request(self):
        gateway = LiteLLMGateway()
        with patch("app.llm.litellm_gateway._ensure_litellm", return_value=_FakeLiteLLM([])):
            resp = await gateway.embed(LLMEmbeddingRequest(inputs=["   "], retries=0))
        self.assertFalse(resp.ok)
        self.assertEqual(resp.error.error_code, "embedding_input_empty")

    async def test_embedding_failure_returns_error_envelope(self):
        fake_litellm = _FakeLiteLLM([RuntimeError("provider failed")])
        gateway = LiteLLMGateway()
        with patch("app.llm.litellm_gateway._ensure_litellm", return_value=fake_litellm):
            resp = await gateway.embed(
                LLMEmbeddingRequest(inputs=["hello"], retries=0, timeout_seconds=5)
            )
        self.assertFalse(resp.ok)
        self.assertEqual(resp.error.error_code, "embedding_call_failed")


if __name__ == "__main__":
    unittest.main()
