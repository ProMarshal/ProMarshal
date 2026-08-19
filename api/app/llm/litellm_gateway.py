"""Concrete shared LLM gateway implementation backed by LiteLLM."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from app.cortex.llm.litellm_config import (
    build_litellm_runtime_config,
    seed_litellm_api_keys,
    to_litellm_model,
)
from app.llm.gateway import (
    LLMEmbeddingRequest,
    LLMEmbeddingResponse,
    LLMErrorClass,
    LLMErrorEnvelope,
    LLMGateway,
    LLMGatewayRequest,
    LLMGatewayResponse,
    LLMHealthCallback,
    LLMHealthEvent,
    LLMHealthEventType,
    LLMMessageRole,
    LLMToolCall,
    LLMToolSpec,
    LLMUsage,
)

_LITELLM = None
_LITELLM_INIT_ATTEMPTED = False


def _ensure_litellm() -> Any:
    global _LITELLM
    global _LITELLM_INIT_ATTEMPTED
    if _LITELLM is not None or _LITELLM_INIT_ATTEMPTED:
        return _LITELLM
    _LITELLM_INIT_ATTEMPTED = True
    try:
        import litellm as _ll  # type: ignore

        _LITELLM = _ll
        return _LITELLM
    except Exception:
        _LITELLM = None
        return None


def _classify_exception(exc: Exception) -> tuple[LLMErrorClass, bool]:
    if isinstance(exc, asyncio.TimeoutError):
        return (LLMErrorClass.TIMEOUT, True)
    text = str(exc or "").lower()
    if "auth" in text or "api key" in text or "unauthorized" in text:
        return (LLMErrorClass.AUTH, False)
    if "rate" in text or "429" in text:
        return (LLMErrorClass.RATE_LIMIT, True)
    if "timeout" in text:
        return (LLMErrorClass.TIMEOUT, True)
    if "network" in text or "connection" in text:
        return (LLMErrorClass.NETWORK, True)
    if "invalid" in text or "bad request" in text or "400" in text:
        return (LLMErrorClass.INVALID_REQUEST, False)
    return (LLMErrorClass.PROVIDER, True)


def _extract_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message") or {}
            if isinstance(message, dict):
                return str(message.get("content") or "").strip()
            return str(getattr(message, "content", "") or "").strip()
        message = getattr(first, "message", None)
        if isinstance(message, dict):
            return str(message.get("content") or "").strip()
        return str(getattr(message, "content", "") or "").strip()
    return ""


def _extract_first_choice(response: Any) -> Any:
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        return choices[0]
    return None


def _extract_message(choice: Any) -> Any:
    if choice is None:
        return None
    if isinstance(choice, dict):
        return choice.get("message")
    return getattr(choice, "message", None)


def _extract_finish_reason(choice: Any) -> Optional[str]:
    if choice is None:
        return None
    if isinstance(choice, dict):
        reason = choice.get("finish_reason")
    else:
        reason = getattr(choice, "finish_reason", None)
    return str(reason).strip() if reason is not None else None


def _extract_raw_message_dict(message: Any) -> dict[str, Any]:
    if message is None:
        return {}
    if isinstance(message, dict):
        return dict(message)
    if hasattr(message, "model_dump"):
        try:
            dumped = message.model_dump()
            if isinstance(dumped, dict):
                return dict(dumped)
        except Exception:
            return {}
    return {}


def _parse_tool_specs(tools: list[LLMToolSpec]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in tools:
        name = str(item.name or "").strip()
        if not name:
            continue
        payload.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(item.description or "").strip(),
                    "parameters": dict(item.json_schema or {}),
                },
            }
        )
    return payload


def _parse_tool_choice(value: Optional[str]) -> Any:
    choice = str(value or "").strip()
    if not choice:
        return None
    lowered = choice.lower()
    if lowered in {"auto", "none", "required"}:
        return lowered
    return {"type": "function", "function": {"name": choice}}


def _extract_tool_calls(message: Any) -> list[LLMToolCall]:
    raw_tool_calls: Any = []
    if isinstance(message, dict):
        raw_tool_calls = message.get("tool_calls") or []
    else:
        raw_tool_calls = getattr(message, "tool_calls", None) or []

    parsed: list[LLMToolCall] = []
    for item in raw_tool_calls or []:
        if isinstance(item, dict):
            call_id = str(item.get("id") or "").strip()
            function = item.get("function") or {}
            name = str((function or {}).get("name") or "").strip()
            arguments_json = str((function or {}).get("arguments") or "{}")
        else:
            call_id = str(getattr(item, "id", "") or "").strip()
            function = getattr(item, "function", None)
            name = str(getattr(function, "name", "") or "").strip()
            arguments_json = str(getattr(function, "arguments", "{}") or "{}")
        if not call_id and not name:
            continue
        try:
            parsed_args = json.loads(arguments_json) if arguments_json else {}
            if not isinstance(parsed_args, dict):
                parsed_args = {}
        except Exception:
            parsed_args = {}
        parsed.append(
            LLMToolCall(
                call_id=call_id,
                name=name,
                arguments_json=arguments_json,
                arguments=parsed_args,
            )
        )
    return parsed


def _serialize_message_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in tool_calls or []:
        if isinstance(item, LLMToolCall):
            call_id = str(item.call_id or "").strip()
            name = str(item.name or "").strip()
            arguments_json = str(item.arguments_json or "{}")
        elif isinstance(item, dict):
            call_id = str(item.get("call_id") or item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            arguments_json = str(item.get("arguments_json") or item.get("arguments") or "{}")
        else:
            call_id = str(getattr(item, "call_id", "") or getattr(item, "id", "") or "").strip()
            name = str(getattr(item, "name", "") or "").strip()
            arguments_json = str(
                getattr(item, "arguments_json", "") or getattr(item, "arguments", "") or "{}"
            )
        if not call_id and not name:
            continue
        payload.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments_json,
                },
            }
        )
    return payload


def _extract_usage(response: Any) -> LLMUsage:
    usage_raw = getattr(response, "usage", None)
    if usage_raw is None:
        return LLMUsage()
    if isinstance(usage_raw, dict):
        return LLMUsage(
            input_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
            total_tokens=int(usage_raw.get("total_tokens", 0) or 0),
        )
    return LLMUsage(
        input_tokens=int(getattr(usage_raw, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage_raw, "completion_tokens", 0) or 0),
        total_tokens=int(getattr(usage_raw, "total_tokens", 0) or 0),
    )


async def _emit_health_event(
    callback: Optional[LLMHealthCallback],
    *,
    event_type: LLMHealthEventType,
    provider: str,
    model: Optional[str],
    project_id: Optional[str],
    reason: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    if callback is None:
        return
    payload = LLMHealthEvent(
        event_type=event_type,
        provider=provider or "unknown",
        model=model,
        project_id=project_id,
        reason=reason,
        details=details or {},
    )
    maybe_awaitable = callback(payload)
    if maybe_awaitable is not None and asyncio.iscoroutine(maybe_awaitable):
        await maybe_awaitable


class LiteLLMGateway(LLMGateway):
    """Shared gateway implementation used by non-Cortex features."""

    def __init__(self) -> None:
        self._runtime = build_litellm_runtime_config()
        seed_litellm_api_keys(
            provider=self._runtime.primary_provider,
            fallback_provider=self._runtime.fallback_provider,
        )

    async def generate(
        self,
        request: LLMGatewayRequest,
        *,
        on_health_event: Optional[LLMHealthCallback] = None,
    ) -> LLMGatewayResponse:
        litellm = _ensure_litellm()
        project_id = str((request.metadata or {}).get("project_id") or "").strip() or None
        if litellm is None:
            await _emit_health_event(
                on_health_event,
                event_type=LLMHealthEventType.FAILURE,
                provider="litellm",
                model=None,
                project_id=project_id,
                reason="litellm_unavailable",
            )
            return LLMGatewayResponse(
                ok=False,
                error=LLMErrorEnvelope(
                    ok=False,
                    error_code="litellm_unavailable",
                    error_class=LLMErrorClass.UNAVAILABLE,
                    retryable=False,
                    http_status=None,
                    user_message="LLM gateway unavailable.",
                ),
            )

        primary_provider = str(request.provider or self._runtime.primary_provider or "openai").strip().lower()
        primary_model = str(request.model or self._runtime.primary_model or "gpt-4o-mini").strip()
        fallback_provider = str(request.fallback_provider or self._runtime.fallback_provider or primary_provider).strip().lower()
        fallback_model = str(request.fallback_model or self._runtime.fallback_model or primary_model).strip()

        primary = to_litellm_model(primary_provider, primary_model)
        fallback = to_litellm_model(fallback_provider, fallback_model)
        candidates = [item for item in [primary, fallback] if item]
        deduped: list[str] = []
        for item in candidates:
            if item not in deduped:
                deduped.append(item)
        candidates = deduped or ["openai/gpt-4o-mini"]

        messages = []
        for msg in request.messages:
            payload: dict[str, Any] = {
                "role": LLMMessageRole(msg.role).value,
                "content": str(msg.content or ""),
            }
            if msg.name:
                payload["name"] = str(msg.name)
            if msg.tool_call_id:
                payload["tool_call_id"] = str(msg.tool_call_id)
            if msg.tool_calls:
                payload["tool_calls"] = _serialize_message_tool_calls(msg.tool_calls)
            messages.append(payload)
        tool_payload = _parse_tool_specs(list(request.tools or []))
        tool_choice_payload = _parse_tool_choice(request.tool_choice)

        max_attempts = max(1, int(request.retries if request.retries is not None else self._runtime.retries) + 1)
        retry_count = 0
        last_exc: Optional[Exception] = None
        last_provider = primary_provider
        last_model = primary_model

        for attempt in range(max_attempts):
            index = min(attempt, len(candidates) - 1)
            chosen = candidates[index]
            provider, model = (chosen.split("/", 1) + [""])[:2] if "/" in chosen else (primary_provider, chosen)
            last_provider = provider or last_provider
            last_model = model or last_model
            try:
                timeout_seconds = max(1, int(request.timeout_seconds))
                response = await asyncio.wait_for(
                    litellm.acompletion(
                        model=chosen,
                        messages=messages,
                        temperature=float(request.temperature),
                        max_tokens=max(1, int(request.max_tokens)),
                        timeout=timeout_seconds,
                        tools=tool_payload or None,
                        tool_choice=tool_choice_payload,
                        response_format=(dict(request.response_format) if request.response_format else None),
                        parallel_tool_calls=(
                            bool(request.parallel_tool_calls)
                            if request.parallel_tool_calls is not None
                            else None
                        ),
                    ),
                    timeout=timeout_seconds,
                )
                text = _extract_text(response)
                usage = _extract_usage(response)
                first_choice = _extract_first_choice(response)
                first_message = _extract_message(first_choice)
                finish_reason = _extract_finish_reason(first_choice)
                tool_calls = _extract_tool_calls(first_message)
                raw_message = _extract_raw_message_dict(first_message)
                await _emit_health_event(
                    on_health_event,
                    event_type=LLMHealthEventType.SUCCESS,
                    provider=provider or "unknown",
                    model=model or None,
                    project_id=project_id,
                    details={"retry_count": retry_count},
                )
                return LLMGatewayResponse(
                    ok=True,
                    text=text,
                    provider=provider or None,
                    model=model or None,
                    usage=usage,
                    retry_count=retry_count,
                    finish_reason=finish_reason,
                    tool_calls=tool_calls,
                    raw_message=raw_message,
                )
            except Exception as exc:
                last_exc = exc
                retry_count += 1
                error_class, retryable = _classify_exception(exc)
                if attempt + 1 < max_attempts:
                    await _emit_health_event(
                        on_health_event,
                        event_type=LLMHealthEventType.RETRY,
                        provider=provider or "unknown",
                        model=model or None,
                        project_id=project_id,
                        reason=str(exc)[:300],
                        details={"attempt": attempt + 1, "max_attempts": max_attempts},
                    )
                    if len(candidates) > 1 and index == 0:
                        next_provider, next_model = (
                            (candidates[1].split("/", 1) + [""])[:2] if "/" in candidates[1] else (fallback_provider, candidates[1])
                        )
                        await _emit_health_event(
                            on_health_event,
                            event_type=LLMHealthEventType.PROVIDER_SWITCH,
                            provider=next_provider or "unknown",
                            model=next_model or None,
                            project_id=project_id,
                            reason="fallback_after_failure",
                        )
                    continue

                await _emit_health_event(
                    on_health_event,
                    event_type=LLMHealthEventType.FAILURE,
                    provider=provider or "unknown",
                    model=model or None,
                    project_id=project_id,
                    reason=str(exc)[:300],
                    details={"retryable": retryable},
                )
                return LLMGatewayResponse(
                    ok=False,
                    provider=provider or None,
                    model=model or None,
                    retry_count=max(0, retry_count - 1),
                    error=LLMErrorEnvelope(
                        ok=False,
                        error_code="gateway_call_failed",
                        error_class=error_class,
                        retryable=retryable,
                        http_status=None,
                        user_message="LLM generation failed.",
                        provider=provider or None,
                        model=model or None,
                        details={"message": str(exc)[:300]},
                    ),
                )

        return LLMGatewayResponse(
            ok=False,
            provider=last_provider or None,
            model=last_model or None,
            retry_count=max(0, retry_count - 1),
            error=LLMErrorEnvelope(
                ok=False,
                error_code="gateway_call_failed",
                error_class=LLMErrorClass.INTERNAL,
                retryable=False,
                http_status=None,
                user_message="LLM generation failed.",
                provider=last_provider or None,
                model=last_model or None,
                details={"message": str(last_exc)[:300] if last_exc else "unknown_error"},
            ),
        )

    async def embed(
        self,
        request: LLMEmbeddingRequest,
        *,
        on_health_event: Optional[LLMHealthCallback] = None,
    ) -> LLMEmbeddingResponse:
        litellm = _ensure_litellm()
        project_id = str((request.metadata or {}).get("project_id") or "").strip() or None
        if litellm is None:
            await _emit_health_event(
                on_health_event,
                event_type=LLMHealthEventType.FAILURE,
                provider="litellm",
                model=None,
                project_id=project_id,
                reason="litellm_unavailable",
            )
            return LLMEmbeddingResponse(
                ok=False,
                error=LLMErrorEnvelope(
                    ok=False,
                    error_code="litellm_unavailable",
                    error_class=LLMErrorClass.UNAVAILABLE,
                    retryable=False,
                    http_status=None,
                    user_message="LLM gateway unavailable.",
                ),
            )

        primary_provider = str(request.provider or self._runtime.primary_provider or "openai").strip().lower()
        primary_model = str(request.model or "text-embedding-3-small").strip()
        fallback_provider = str(request.fallback_provider or self._runtime.fallback_provider or primary_provider).strip().lower()
        fallback_model = str(request.fallback_model or primary_model).strip()

        primary = to_litellm_model(primary_provider, primary_model)
        fallback = to_litellm_model(fallback_provider, fallback_model)
        candidates = [item for item in [primary, fallback] if item]
        deduped: list[str] = []
        for item in candidates:
            if item not in deduped:
                deduped.append(item)
        candidates = deduped or ["openai/text-embedding-3-small"]

        normalized_inputs = [str(item or "").strip() for item in (request.inputs or []) if str(item or "").strip()]
        if not normalized_inputs:
            return LLMEmbeddingResponse(
                ok=False,
                error=LLMErrorEnvelope(
                    ok=False,
                    error_code="embedding_input_empty",
                    error_class=LLMErrorClass.INVALID_REQUEST,
                    retryable=False,
                    http_status=400,
                    user_message="Embedding input is empty.",
                ),
            )

        max_attempts = max(1, int(request.retries if request.retries is not None else 2) + 1)
        retry_count = 0
        last_exc: Optional[Exception] = None
        last_provider = primary_provider
        last_model = primary_model

        for attempt in range(max_attempts):
            index = min(attempt, len(candidates) - 1)
            chosen = candidates[index]
            provider, model = (chosen.split("/", 1) + [""])[:2] if "/" in chosen else (primary_provider, chosen)
            last_provider = provider or last_provider
            last_model = model or last_model
            try:
                timeout_seconds = max(1, int(request.timeout_seconds))
                response = await asyncio.wait_for(
                    litellm.aembedding(
                        model=chosen,
                        input=normalized_inputs,
                        timeout=timeout_seconds,
                    ),
                    timeout=timeout_seconds,
                )
                vectors: list[list[float]] = []
                data_items = getattr(response, "data", None)
                if not isinstance(data_items, list):
                    data_items = []
                for item in data_items:
                    if isinstance(item, dict):
                        emb = item.get("embedding")
                    else:
                        emb = getattr(item, "embedding", None)
                    if isinstance(emb, list) and emb:
                        try:
                            vectors.append([float(value) for value in emb])
                        except Exception:
                            vectors.append([])
                if len(vectors) != len(normalized_inputs) or any(not vec for vec in vectors):
                    raise RuntimeError("embedding_response_invalid")

                usage = _extract_usage(response)
                await _emit_health_event(
                    on_health_event,
                    event_type=LLMHealthEventType.SUCCESS,
                    provider=provider or "unknown",
                    model=model or None,
                    project_id=project_id,
                    details={"retry_count": retry_count, "embedding_count": len(vectors)},
                )
                return LLMEmbeddingResponse(
                    ok=True,
                    vectors=tuple(tuple(v) for v in vectors),
                    provider=provider or None,
                    model=model or None,
                    retry_count=retry_count,
                    usage=usage,
                    metadata={"embedding_count": len(vectors)},
                )
            except Exception as exc:
                last_exc = exc
                retry_count += 1
                error_class, retryable = _classify_exception(exc)
                if attempt + 1 < max_attempts:
                    await _emit_health_event(
                        on_health_event,
                        event_type=LLMHealthEventType.RETRY,
                        provider=provider or "unknown",
                        model=model or None,
                        project_id=project_id,
                        reason=str(exc)[:300],
                        details={"attempt": attempt + 1, "max_attempts": max_attempts, "kind": "embedding"},
                    )
                    if len(candidates) > 1 and index == 0:
                        next_provider, next_model = (
                            (candidates[1].split("/", 1) + [""])[:2] if "/" in candidates[1] else (fallback_provider, candidates[1])
                        )
                        await _emit_health_event(
                            on_health_event,
                            event_type=LLMHealthEventType.PROVIDER_SWITCH,
                            provider=next_provider or "unknown",
                            model=next_model or None,
                            project_id=project_id,
                            reason="embedding_fallback_after_failure",
                        )
                    continue

                await _emit_health_event(
                    on_health_event,
                    event_type=LLMHealthEventType.FAILURE,
                    provider=provider or "unknown",
                    model=model or None,
                    project_id=project_id,
                    reason=str(exc)[:300],
                    details={"retryable": retryable, "kind": "embedding"},
                )
                return LLMEmbeddingResponse(
                    ok=False,
                    provider=provider or None,
                    model=model or None,
                    retry_count=max(0, retry_count - 1),
                    error=LLMErrorEnvelope(
                        ok=False,
                        error_code="embedding_call_failed",
                        error_class=error_class,
                        retryable=retryable,
                        http_status=None,
                        user_message="Embedding generation failed.",
                        provider=provider or None,
                        model=model or None,
                        details={"message": str(exc)[:300]},
                    ),
                )

        return LLMEmbeddingResponse(
            ok=False,
            provider=last_provider or None,
            model=last_model or None,
            retry_count=max(0, retry_count - 1),
            error=LLMErrorEnvelope(
                ok=False,
                error_code="embedding_call_failed",
                error_class=LLMErrorClass.INTERNAL,
                retryable=False,
                http_status=None,
                user_message="Embedding generation failed.",
                provider=last_provider or None,
                model=last_model or None,
                details={"message": str(last_exc)[:300] if last_exc else "unknown_error"},
            ),
        )
