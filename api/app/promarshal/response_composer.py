"""Global ProMarshal response composition helpers."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Dict

from app.core.config import settings
from app.llm import LLMGatewayRequest, LLMMessage, LLMMessageRole, get_shared_llm_gateway
from app.promarshal.prompt_registry import (
    compose_identity,
    compose_response_rules,
    compose_scope_policy,
)


def _default_model() -> str:
    try:
        from app.cortex.llm.litellm_config import (
            build_litellm_runtime_config,
            to_litellm_model,
        )

        cfg = build_litellm_runtime_config()
        return to_litellm_model(cfg.primary_provider, cfg.primary_model)
    except Exception:
        return "groq/llama-3.3-70b-versatile"


async def compose_user_response(
    *,
    capability: str,
    user_message: str,
    outcome_payload: Dict[str, Any],
    fallback_text: str,
    max_tokens: int = 220,
    temperature: float = 0.2,
    enabled: bool | None = None,
    include_fallback_in_prompt: bool = True,
) -> str:
    if enabled is None:
        enabled = bool(getattr(settings, "team_poll_response_composer_enabled", False))
    if not enabled:
        return str(fallback_text or "").strip()

    fallback = str(fallback_text or "").strip()
    if not fallback:
        return ""

    gateway = get_shared_llm_gateway()
    if gateway is None:
        return fallback

    identity = compose_identity(
        capability=capability,
        legacy_identity="You are ProMarshal, a project operations assistant.",
        legacy_overlay="",
    )
    scope_policy = compose_scope_policy(legacy_policy="", capability=capability)
    response_rules = compose_response_rules(capability=capability, legacy_rules="")
    system_prompt = "\n\n".join(
        [
            identity,
            scope_policy,
            response_rules,
            (
                "Composer rules:\n"
                "- Rewrite the final user-facing message naturally and clearly.\n"
                "- Do not expose internal function/tool names.\n"
                "- Preserve factual details from outcome payload.\n"
                "- If no results were found, say that clearly and briefly.\n"
                "- Use Slack-friendly formatting: bold section headings only in single-star form (*Heading:*).\n"
                "- Do not use double-asterisk markdown (**...**) for normal body text or list items.\n"
                "- Return plain text only."
            ),
        ]
    ).strip()

    try:
        serialized_outcome = json.dumps(outcome_payload or {}, ensure_ascii=True)
    except Exception:
        serialized_outcome = "{}"

    user_prompt_lines = [
        f"User request:\n{str(user_message or '').strip()}",
        f"Outcome payload (JSON):\n{serialized_outcome}",
    ]
    if include_fallback_in_prompt:
        user_prompt_lines.append(f"Fallback draft:\n{fallback}")
    user_prompt_lines.append("Rewrite the final response now.")
    user_prompt = "\n\n".join(user_prompt_lines)

    try:
        compose_started = perf_counter()
        request = LLMGatewayRequest(
            messages=[
                LLMMessage(role=LLMMessageRole.SYSTEM, content=system_prompt),
                LLMMessage(role=LLMMessageRole.USER, content=user_prompt),
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=6,
            retries=0,
            model=_default_model(),
            metadata={"feature": "promarshal_response_composer", "capability": capability},
        )
        print(
            "[promarshal/response_composer] compose_start "
            f"capability={capability} model={request.model} "
            f"timeout={request.timeout_seconds}s max_tokens={request.max_tokens} "
            f"user_chars={len(str(user_message or ''))} payload_chars={len(serialized_outcome)} "
            f"fallback_chars={len(fallback)}"
        )
        response = await gateway.generate(request)
        compose_elapsed = int((perf_counter() - compose_started) * 1000)
        if not response.ok:
            print(
                "[promarshal/response_composer] compose_failed "
                f"capability={capability} latency_ms={compose_elapsed} "
                f"error_class={getattr(response.error, 'error_class', None)} "
                f"error_code={getattr(response.error, 'error_code', None)}"
            )
            return fallback
        text = str(response.text or "").strip()
        print(
            "[promarshal/response_composer] compose_success "
            f"capability={capability} latency_ms={compose_elapsed} "
            f"response_chars={len(text)}"
        )
        return text or fallback
    except Exception as exc:
        print(f"[promarshal/response_composer] Compose call failed: {exc}")
        return fallback
