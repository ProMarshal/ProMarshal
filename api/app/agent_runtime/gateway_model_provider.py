"""Gateway-aligned Agents model provider for Layer A Cortex migration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
from typing import Any, Optional

from app.cortex.llm.litellm_config import (
    LiteLLMRuntimeConfig,
    build_litellm_runtime_config,
    seed_litellm_api_keys,
    to_litellm_model,
)
from app.llm.gateway import (
    LLMHealthCallback,
    LLMHealthEvent,
    LLMHealthEventType,
    LLMGatewayRequest,
    LLMMessage,
    LLMMessageRole,
    LLMToolCall,
    LLMToolSpec,
)
from app.llm.provider import get_shared_llm_gateway

try:
    from agents.items import ModelResponse
    from agents.models.chatcmpl_converter import Converter as ChatCmplConverter
    from agents.models.interface import Model, ModelProvider
    from agents.model_settings import MCPToolChoice, ModelSettings
    from agents.usage import Usage
    from agents.extensions.models.litellm_provider import LitellmProvider
    from openai.types.chat import ChatCompletionMessage
    from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails
except Exception:  # pragma: no cover - import guard for partial environments
    Model = None
    ModelProvider = object  # type: ignore[assignment]
    ModelResponse = None
    ChatCmplConverter = None
    ModelSettings = None
    Usage = None
    MCPToolChoice = None
    ChatCompletionMessage = None
    InputTokensDetails = None
    OutputTokensDetails = None
    LitellmProvider = None


@dataclass(frozen=True)
class _ModelIdentity:
    provider: str
    model: str
    full_name: str


class _ObservedModel:
    """Thin wrapper around Agents LiteLLM model that emits health callbacks."""

    def __init__(
        self,
        *,
        delegate: Model,
        identity: _ModelIdentity,
        on_health_event: Optional[LLMHealthCallback],
        project_id: Optional[str],
    ) -> None:
        self._delegate = delegate
        self._identity = identity
        self._on_health_event = on_health_event
        self._project_id = project_id

    async def _emit(self, *, event_type: LLMHealthEventType, reason: Optional[str] = None) -> None:
        callback = self._on_health_event
        if callback is None:
            return
        event = LLMHealthEvent(
            event_type=event_type,
            provider=self._identity.provider,
            model=self._identity.model,
            project_id=self._project_id,
            reason=reason,
            details={"model_name": self._identity.full_name},
        )
        maybe_awaitable = callback(event)
        if maybe_awaitable is not None:
            await maybe_awaitable

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        try:
            response = await self._delegate.get_response(*args, **kwargs)
        except Exception as exc:
            await self._emit(
                event_type=LLMHealthEventType.FAILURE,
                reason=str(exc)[:300],
            )
            raise
        await self._emit(event_type=LLMHealthEventType.SUCCESS)
        return response

    def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        async def _stream() -> AsyncIterator[Any]:
            try:
                async for chunk in self._delegate.stream_response(*args, **kwargs):
                    yield chunk
            except Exception as exc:
                await self._emit(
                    event_type=LLMHealthEventType.FAILURE,
                    reason=str(exc)[:300],
                )
                raise
            await self._emit(event_type=LLMHealthEventType.SUCCESS)

        return _stream()


class GatewayModelProvider(ModelProvider):
    """
    Agents SDK ModelProvider that preserves existing LiteLLM behavior
    while exposing shared gateway-aligned health event hooks.
    """

    def __init__(
        self,
        *,
        runtime_config: Optional[LiteLLMRuntimeConfig] = None,
        on_health_event: Optional[LLMHealthCallback] = None,
        project_id: Optional[str] = None,
    ) -> None:
        if LitellmProvider is None or Model is None:
            raise RuntimeError("agents_or_litellm_provider_unavailable")
        self._runtime_config = runtime_config or build_litellm_runtime_config()
        self._delegate_provider = LitellmProvider()
        self._on_health_event = on_health_event
        self._project_id = str(project_id or "").strip() or None
        # Keep provider bootstrap aligned with shared gateway initialization path.
        self._shared_gateway = get_shared_llm_gateway()
        seed_litellm_api_keys(
            provider=self._runtime_config.primary_provider,
            fallback_provider=self._runtime_config.fallback_provider,
        )

    def _resolve_model_name(self, model_name: Optional[str]) -> str:
        candidate = str(model_name or "").strip()
        if candidate:
            return candidate
        return to_litellm_model(
            self._runtime_config.primary_provider,
            self._runtime_config.primary_model,
        )

    def _identity(self, model_name: str) -> _ModelIdentity:
        normalized = str(model_name or "").strip()
        if "/" in normalized:
            provider, model = normalized.split("/", 1)
        else:
            provider = str(self._runtime_config.primary_provider or "openai").strip().lower()
            model = normalized
        return _ModelIdentity(
            provider=provider or "openai",
            model=model or str(self._runtime_config.primary_model or "gpt-4o-mini"),
            full_name=normalized,
        )

    def get_model(self, model_name: Optional[str]) -> Model:
        resolved_name = self._resolve_model_name(model_name)
        delegate = self._delegate_provider.get_model(resolved_name)
        identity = self._identity(resolved_name)
        return _ObservedModel(
            delegate=delegate,
            identity=identity,
            on_health_event=self._on_health_event,
            project_id=self._project_id,
        )


def _normalize_chat_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
        if parts:
            return "\n".join(parts)
    if value is None:
        return ""
    try:
        return json.dumps(value, default=str)
    except Exception:
        return str(value)


def _gateway_role(value: str) -> LLMMessageRole:
    cleaned = str(value or "").strip().lower()
    if cleaned in {"system", "developer"}:
        return LLMMessageRole.SYSTEM
    if cleaned == "assistant":
        return LLMMessageRole.ASSISTANT
    if cleaned == "tool":
        return LLMMessageRole.TOOL
    return LLMMessageRole.USER


def _extract_retry_and_timeout(model_settings: Any) -> tuple[int, int]:
    extra_args = dict(getattr(model_settings, "extra_args", None) or {})
    retries = int(extra_args.get("num_retries", 3) or 3)
    timeout = int(extra_args.get("timeout", 60) or 60)
    return max(0, retries), max(1, timeout)


def _tool_choice_to_gateway(tool_choice: Any) -> Optional[str]:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, MCPToolChoice):
        return None
    if isinstance(tool_choice, str):
        return tool_choice
    return str(tool_choice)


def _build_gateway_tool_specs(tools: list[Any], handoffs: list[Any]) -> list[LLMToolSpec]:
    if ChatCmplConverter is None:
        return []
    specs: list[LLMToolSpec] = []
    for tool in tools or []:
        try:
            item = ChatCmplConverter.tool_to_openai(tool)
        except Exception:
            continue
        function = (item or {}).get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        specs.append(
            LLMToolSpec(
                name=name,
                description=str(function.get("description") or "").strip(),
                json_schema=dict(function.get("parameters") or {}),
            )
        )
    for handoff in handoffs or []:
        try:
            item = ChatCmplConverter.convert_handoff_tool(handoff)
        except Exception:
            continue
        function = (item or {}).get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        specs.append(
            LLMToolSpec(
                name=name,
                description=str(function.get("description") or "").strip(),
                json_schema=dict(function.get("parameters") or {}),
            )
        )
    return specs


def _tool_calls_to_raw(tool_calls: list[LLMToolCall]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for call in tool_calls or []:
        payload.append(
            {
                "id": str(call.call_id or ""),
                "type": "function",
                "function": {
                    "name": str(call.name or ""),
                    "arguments": str(call.arguments_json or "{}"),
                },
            }
        )
    return payload


def _message_tool_calls_to_gateway(value: Any) -> tuple[LLMToolCall, ...]:
    parsed: list[LLMToolCall] = []
    for item in value or []:
        call_id = ""
        name = ""
        arguments_json = "{}"
        if isinstance(item, dict):
            call_id = str(item.get("id") or "").strip()
            function = item.get("function") or {}
            if isinstance(function, dict):
                name = str(function.get("name") or "").strip()
                arguments_json = str(function.get("arguments") or "{}")
        else:
            call_id = str(getattr(item, "id", "") or "").strip()
            function = getattr(item, "function", None)
            name = str(getattr(function, "name", "") or "").strip()
            arguments_json = str(getattr(function, "arguments", "{}") or "{}")
        if not call_id and not name:
            continue
        parsed.append(
            LLMToolCall(
                call_id=call_id,
                name=name,
                arguments_json=arguments_json,
            )
        )
    return tuple(parsed)


class _GatewayToolCallingModel:
    """Agents Model adapter backed by shared LLM gateway tool-calling contracts."""

    def __init__(
        self,
        *,
        model_name: str,
        identity: _ModelIdentity,
        runtime_config: LiteLLMRuntimeConfig,
        on_health_event: Optional[LLMHealthCallback],
        project_id: Optional[str],
        stream_delegate: Model,
    ) -> None:
        self._model_name = model_name
        self._identity = identity
        self._runtime_config = runtime_config
        self._on_health_event = on_health_event
        self._project_id = project_id
        self._gateway = get_shared_llm_gateway()
        self._stream_delegate = stream_delegate

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[Any],
        model_settings: ModelSettings,
        tools: list[Any],
        output_schema: Any,
        handoffs: list[Any],
        tracing: Any,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: Any | None,
    ) -> ModelResponse:
        if ChatCmplConverter is None or ModelResponse is None or Usage is None:
            raise RuntimeError("agents_sdk_unavailable_for_gateway_tool_call_model")
        messages = ChatCmplConverter.items_to_messages(input, model=self._model_name)
        if system_instructions:
            messages.insert(0, {"role": "system", "content": str(system_instructions or "")})
        gateway_messages: list[LLMMessage] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            gateway_messages.append(
                LLMMessage(
                    role=_gateway_role(str(item.get("role") or "user")),
                    content=_normalize_chat_content(item.get("content")),
                    name=str(item.get("name") or "").strip() or None,
                    tool_call_id=str(item.get("tool_call_id") or "").strip() or None,
                    tool_calls=_message_tool_calls_to_gateway(item.get("tool_calls")),
                )
            )
        retries, timeout_seconds = _extract_retry_and_timeout(model_settings)
        request = LLMGatewayRequest(
            messages=gateway_messages,
            provider=self._identity.provider,
            model=self._identity.model,
            fallback_provider=str(self._runtime_config.fallback_provider or "").strip() or None,
            fallback_model=str(self._runtime_config.fallback_model or "").strip() or None,
            max_tokens=max(1, int(model_settings.max_tokens or self._runtime_config.max_tokens or 2048)),
            temperature=float(
                model_settings.temperature
                if model_settings.temperature is not None
                else self._runtime_config.temperature
            ),
            retries=retries,
            timeout_seconds=timeout_seconds,
            tools=_build_gateway_tool_specs(tools, handoffs),
            tool_choice=_tool_choice_to_gateway(model_settings.tool_choice),
            parallel_tool_calls=model_settings.parallel_tool_calls,
            metadata={"project_id": self._project_id or ""},
        )
        response = await self._gateway.generate(
            request,
            on_health_event=self._on_health_event,
        )
        if not response.ok:
            error_message = str((response.error.user_message if response.error else "") or "gateway_call_failed")
            raise RuntimeError(error_message)

        raw_message = dict(response.raw_message or {})
        if not raw_message:
            raw_message = {
                "role": "assistant",
                "content": str(response.text or ""),
            }
        raw_message.setdefault("role", "assistant")
        if response.tool_calls:
            raw_message["tool_calls"] = _tool_calls_to_raw(list(response.tool_calls or []))
        if response.finish_reason:
            raw_message["finish_reason"] = response.finish_reason
        message = ChatCompletionMessage(**raw_message)
        output_items = ChatCmplConverter.message_to_output_items(message)
        usage = Usage(
            requests=1,
            input_tokens=int(getattr(response.usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(response.usage, "output_tokens", 0) or 0),
            total_tokens=int(getattr(response.usage, "total_tokens", 0) or 0),
            input_tokens_details=InputTokensDetails(cached_tokens=0),
            output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
        )
        return ModelResponse(output=output_items, usage=usage, response_id=None)

    def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        return self._stream_delegate.stream_response(*args, **kwargs)


class GatewayToolCallingModelProvider(ModelProvider):
    """Layer B provider: Runner uses shared gateway tool-call contracts."""

    def __init__(
        self,
        *,
        runtime_config: Optional[LiteLLMRuntimeConfig] = None,
        on_health_event: Optional[LLMHealthCallback] = None,
        project_id: Optional[str] = None,
    ) -> None:
        if LitellmProvider is None or Model is None:
            raise RuntimeError("agents_or_litellm_provider_unavailable")
        self._runtime_config = runtime_config or build_litellm_runtime_config()
        self._delegate_provider = LitellmProvider()
        self._on_health_event = on_health_event
        self._project_id = str(project_id or "").strip() or None
        seed_litellm_api_keys(
            provider=self._runtime_config.primary_provider,
            fallback_provider=self._runtime_config.fallback_provider,
        )

    def _resolve_model_name(self, model_name: Optional[str]) -> str:
        candidate = str(model_name or "").strip()
        if candidate:
            return candidate
        return to_litellm_model(
            self._runtime_config.primary_provider,
            self._runtime_config.primary_model,
        )

    def _identity(self, model_name: str) -> _ModelIdentity:
        normalized = str(model_name or "").strip()
        if "/" in normalized:
            provider, model = normalized.split("/", 1)
        else:
            provider = str(self._runtime_config.primary_provider or "openai").strip().lower()
            model = normalized
        return _ModelIdentity(
            provider=provider or "openai",
            model=model or str(self._runtime_config.primary_model or "gpt-4o-mini"),
            full_name=normalized,
        )

    def get_model(self, model_name: Optional[str]) -> Model:
        resolved_name = self._resolve_model_name(model_name)
        identity = self._identity(resolved_name)
        stream_delegate = self._delegate_provider.get_model(resolved_name)
        return _GatewayToolCallingModel(
            model_name=resolved_name,
            identity=identity,
            runtime_config=self._runtime_config,
            on_health_event=self._on_health_event,
            project_id=self._project_id,
            stream_delegate=stream_delegate,
        )
