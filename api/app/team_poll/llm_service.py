"""LLM summarization service for Team Poll owner reports."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from app.core.config import settings
from app.llm import LLMGatewayRequest, LLMMessage, LLMMessageRole, get_shared_llm_gateway

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def _default_model() -> str:
    try:
        from app.cortex.llm.litellm_config import (
            build_litellm_runtime_config,
            to_litellm_model,
        )

        cfg = build_litellm_runtime_config()
        return to_litellm_model(cfg.primary_provider, cfg.primary_model)
    except Exception:
        return "openai/gpt-4o-mini"


def _fallback_grouping(responses: List[str]) -> List[str]:
    grouped: Dict[str, List[str]] = {}
    for item in responses:
        text = str(item or "").strip()
        if not text:
            continue
        if ":" in text:
            name, raw = text.split(":", 1)
            member = name.strip()
            answer = raw.strip()
        else:
            member = "Member"
            answer = text
        key = answer.lower()
        grouped.setdefault(key, []).append(member)

    lines: List[str] = []
    for answer_key, names in grouped.items():
        if not names:
            continue
        original = answer_key
        lines.append(f"{', '.join(names)}: {original}")
    return lines[:3]


def _normalize_grouped_lines(raw: Any) -> List[str]:
    if isinstance(raw, str):
        value = str(raw).strip()
        return [value] if value else []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


async def summarize_team_poll_responses(
    *,
    question_text: str,
    responses: List[str],
) -> Dict[str, Any]:
    cleaned = [str(item or "").strip() for item in responses if str(item or "").strip()]
    fallback_heading = str(question_text or "Team Check").strip()[:80] or "Team Check"
    if not cleaned:
        return {
            "heading": fallback_heading,
            "grouped_lines": ["No member responses were captured."],
            "source": "deterministic",
            "model": None,
        }

    if not bool(getattr(settings, "team_poll_llm_summary_enabled", True)):
        return {
            "heading": fallback_heading,
            "grouped_lines": _fallback_grouping(cleaned) or ["Responses captured."],
            "source": "deterministic",
            "model": None,
        }

    gateway = get_shared_llm_gateway()
    if gateway is None:
        return {
            "heading": fallback_heading,
            "grouped_lines": _fallback_grouping(cleaned) or ["Responses captured."],
            "source": "deterministic",
            "model": None,
        }

    system_prompt = (
        "You summarize team poll responses for a project owner.\n"
        "Return STRICT JSON with keys: heading, grouped_lines.\n"
        "Rules:\n"
        "- heading: short title (3-8 words), no punctuation suffix.\n"
        "- grouped_lines: 1-3 concise lines grouping members by similar outcomes.\n"
        "- Include member names in grouped lines.\n"
        "- Do not output per-person raw transcript lines.\n"
        "- Do not include poll ID or internal metadata.\n"
        "- Keep wording crisp and assistant-like."
    )
    user_payload = {
        "question_text": str(question_text or "").strip(),
        "responses": cleaned[:30],
    }
    user_prompt = f"Input JSON:\n{json.dumps(user_payload, ensure_ascii=True)}"

    try:
        request = LLMGatewayRequest(
            messages=[
                LLMMessage(role=LLMMessageRole.SYSTEM, content=system_prompt),
                LLMMessage(role=LLMMessageRole.USER, content=user_prompt),
            ],
            temperature=0.2,
            max_tokens=220,
            timeout_seconds=8,
            retries=0,
            model=_default_model(),
            metadata={"feature": "team_poll_llm_summary"},
        )
        response = await gateway.generate(request)
        if not response.ok:
            raise RuntimeError("gateway_unavailable")
        text = str(response.text or "").strip()
        model = str(response.model or request.model or "")
    except Exception:
        return {
            "heading": fallback_heading,
            "grouped_lines": _fallback_grouping(cleaned) or ["Responses captured."],
            "source": "deterministic",
            "model": None,
        }

    try:
        parsed = json.loads(text)
    except Exception:
        match = _JSON_BLOCK_RE.search(text or "")
        if not match:
            parsed = {}
        else:
            try:
                parsed = json.loads(match.group(0))
            except Exception:
                parsed = {}

    heading = str((parsed or {}).get("heading") or "").strip() or fallback_heading
    grouped_lines_raw = (parsed or {}).get("grouped_lines") or []
    grouped_lines = _normalize_grouped_lines(grouped_lines_raw)
    if not grouped_lines:
        grouped_lines = _fallback_grouping(cleaned) or ["Responses captured."]

    return {
        "heading": heading[:80],
        "grouped_lines": grouped_lines[:3],
        "source": "llm",
        "model": model,
    }
