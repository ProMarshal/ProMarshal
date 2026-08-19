"""LLM-backed team poll question rewriting with deterministic fallback."""

from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.llm import LLMGatewayRequest, LLMMessage, LLMMessageRole, get_shared_llm_gateway
from app.team_poll.schema import normalize_question_text

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")
_WS_RE = re.compile(r"\s+")
_RELATIVE_TIME_PATTERNS = (
    re.compile(r"\b(last|this|next)\s+(week|month|quarter|year)\b", re.IGNORECASE),
    re.compile(r"\b(this|next|last)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.IGNORECASE),
    re.compile(r"\b(today|tomorrow|yesterday)\b", re.IGNORECASE),
    re.compile(r"\bby\s+eod\b", re.IGNORECASE),
)
_MALFORMED_PATTERNS = (
    re.compile(r"\bcheck\s+with\s+you\b", re.IGNORECASE),
    re.compile(r"\bask\s+you\s+if\s+you\b", re.IGNORECASE),
    re.compile(r"\beveryone\b.*\byou\b", re.IGNORECASE),
    re.compile(r"\bteam\b.*\byou\b", re.IGNORECASE),
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
        return "openai/gpt-4o-mini"


def _parse_backoff_schedule_ms(raw: Any) -> list[int]:
    text = str(raw or "").strip()
    if not text:
        return [300, 900]
    values: list[int] = []
    for item in text.split(","):
        token = str(item or "").strip()
        if not token:
            continue
        try:
            value = int(token)
        except Exception:
            continue
        if value > 0:
            values.append(value)
    return values or [300, 900]


def _month_name(month: int) -> str:
    return datetime(2000, month, 1).strftime("%B")


def _quarter(value: datetime) -> int:
    return ((value.month - 1) // 3) + 1


def _weekday_date(*, now_local: datetime, target_weekday: int, week_offset: int = 0) -> str:
    day_delta = target_weekday - now_local.weekday()
    anchor = now_local + timedelta(days=day_delta + (week_offset * 7))
    return anchor.date().isoformat()


def _relative_time_context(*, now_local: datetime, timezone_name: str) -> Dict[str, str]:
    this_month = f"{_month_name(now_local.month)} {now_local.year}"
    prev_month_dt = now_local.replace(day=1) - timedelta(days=1)
    next_month_anchor = (now_local.replace(day=28) + timedelta(days=4)).replace(day=1)
    last_month = f"{_month_name(prev_month_dt.month)} {prev_month_dt.year}"
    next_month = f"{_month_name(next_month_anchor.month)} {next_month_anchor.year}"

    this_year = now_local.year
    last_year = this_year - 1
    next_year = this_year + 1

    this_quarter = f"Q{_quarter(now_local)} {this_year}"
    if _quarter(now_local) == 1:
        last_quarter = f"Q4 {last_year}"
    else:
        last_quarter = f"Q{_quarter(now_local) - 1} {this_year}"
    if _quarter(now_local) == 4:
        next_quarter = f"Q1 {next_year}"
    else:
        next_quarter = f"Q{_quarter(now_local) + 1} {this_year}"

    return {
        "timezone": timezone_name,
        "today_date": now_local.date().isoformat(),
        "tomorrow_date": (now_local + timedelta(days=1)).date().isoformat(),
        "yesterday_date": (now_local - timedelta(days=1)).date().isoformat(),
        "this_week_iso": str(now_local.isocalendar().week),
        "this_month": this_month,
        "last_month": last_month,
        "next_month": next_month,
        "this_quarter": this_quarter,
        "last_quarter": last_quarter,
        "next_quarter": next_quarter,
        "this_monday": _weekday_date(now_local=now_local, target_weekday=0, week_offset=0),
        "this_friday": _weekday_date(now_local=now_local, target_weekday=4, week_offset=0),
        "next_monday": _weekday_date(now_local=now_local, target_weekday=0, week_offset=1),
        "next_friday": _weekday_date(now_local=now_local, target_weekday=4, week_offset=1),
        "this_year": str(this_year),
        "last_year": str(last_year),
        "next_year": str(next_year),
    }


def _tokenize_for_scope(text: str) -> set[str]:
    tokens = re.findall(r"[A-Za-z0-9]+", str(text or "").lower())
    return {tok for tok in tokens if tok and len(tok) > 1}


def _has_relative_time(text: str) -> bool:
    return any(pattern.search(str(text or "")) for pattern in _RELATIVE_TIME_PATTERNS)


def _scope_preserved(*, original: str, rewritten: str) -> bool:
    original_tokens = _tokenize_for_scope(original)
    rewritten_tokens = _tokenize_for_scope(rewritten)
    if not original_tokens:
        return True
    overlap = len(original_tokens.intersection(rewritten_tokens))
    ratio = overlap / float(len(original_tokens))
    text_ratio = SequenceMatcher(
        None,
        _WS_RE.sub(" ", str(original or "").lower()).strip(),
        _WS_RE.sub(" ", str(rewritten or "").lower()).strip(),
    ).ratio()
    return ratio >= 0.6 or text_ratio >= 0.6


def _postprocess_question(question: str, *, max_chars: int) -> str:
    value = _WS_RE.sub(" ", str(question or "").strip())
    value = normalize_question_text(value)
    if len(value) > max_chars:
        value = value[:max_chars].rstrip(" ,.;:!?")
        if not value.endswith("?"):
            value = value + "?"
    if value and not value.endswith("?"):
        value = value + "?"
    return value


def _is_malformed_member_question(text: str) -> bool:
    normalized = _WS_RE.sub(" ", str(text or "").strip())
    if not normalized:
        return True
    return any(pattern.search(normalized) for pattern in _MALFORMED_PATTERNS)


async def _rewrite_with_llm(
    *,
    owner_text: str,
    parsed_question: str,
    parsed_mode: Dict[str, Any],
    project_name: str,
    project_timezone: str,
) -> Optional[Dict[str, Any]]:
    gateway = get_shared_llm_gateway()
    if gateway is None:
        return None

    max_chars = max(40, int(getattr(settings, "team_poll_question_rewrite_max_chars", 220) or 220))
    timezone_name = str(project_timezone or "UTC").strip() or "UTC"
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        timezone_name = "UTC"
        tz = timezone.utc
    now_local = datetime.now(timezone.utc).astimezone(tz)
    time_ctx = _relative_time_context(now_local=now_local, timezone_name=timezone_name)

    system_prompt = (
        "You rewrite an owner's instruction into one clear MEMBER-facing question for direct DM.\n"
        "Return STRICT JSON only with keys: question_text, confidence.\n"
        "Rules:\n"
        "- Preserve intent and scope exactly; do not add or remove asks.\n"
        "- Phrase the question in second person singular (you/your), suitable to ask one member.\n"
        "- Do not include words like 'everyone' or 'team' in the final question.\n"
        "- Keep it natural and concise, as if ProMarshal is asking one teammate directly.\n"
        "- If the input uses relative time (e.g., last month, this week, by EOD), "
        "convert it to explicit absolute time using provided context.\n"
        "- For weekday references (e.g., this Friday, next Monday), use explicit date in YYYY-MM-DD.\n"
        "- Do not invent dates when the input has no time reference.\n"
        f"- question_text length <= {max_chars}.\n"
        "- question_text must end with '?'.\n"
        "- confidence is a float 0..1."
    )
    payload = {
        "project_name": str(project_name or "").strip(),
        "owner_text": str(owner_text or "").strip(),
        "parsed_question": str(parsed_question or "").strip(),
        "response_format": str(parsed_mode.get("response_format") or "free_text"),
        "options": parsed_mode.get("options") or [],
        "deadline_mode": str(parsed_mode.get("deadline_mode") or "default"),
        "time_context_project_tz": time_ctx,
    }
    user_prompt = f"Input JSON:\n{json.dumps(payload, ensure_ascii=True)}"

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
            metadata={"feature": "team_poll_question_rewriter"},
        )
        response = await gateway.generate(request)
        if not response.ok:
            return None
        text = str(response.text or "").strip()
        model = str(response.model or request.model or "")
    except Exception:
        return None

    if not text:
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        match = _JSON_BLOCK_RE.search(text)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return None

    if not isinstance(parsed, dict):
        return None

    question = _postprocess_question(str(parsed.get("question_text") or "").strip(), max_chars=max_chars)
    if not question:
        return None

    confidence_raw = parsed.get("confidence")
    try:
        confidence = float(confidence_raw)
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    # Scope-drift lexical gate intentionally disabled for now.
    # It was rejecting valid paraphrases that preserve intent.
    if _has_relative_time(parsed_question) and not any(ch.isdigit() for ch in question):
        print("team_poll_question_rewriter_rejected: reason=relative_time_not_grounded")
        return None
    if _is_malformed_member_question(question):
        print("team_poll_question_rewriter_rejected: reason=malformed_member_question")
        return None

    return {
        "question_text": question,
        "confidence": confidence,
        "source": "llm",
        "model": model,
    }


async def resolve_poll_question(
    *,
    owner_text: str,
    parsed_question: str,
    parsed_mode: Dict[str, Any],
    project_name: str,
    project_timezone: str,
) -> Dict[str, Any]:
    """
    Determine final poll question with deterministic fallback.

    Returns:
      {
        question_text,
        original_question_text,
        source: deterministic|llm,
        confidence: float,
        model: str|None
      }
    """
    original = normalize_question_text(parsed_question)
    if not original:
        return {
            "question_text": "",
            "original_question_text": "",
            "source": "deterministic",
            "confidence": 0.0,
            "model": None,
        }

    rewrite_enabled = bool(getattr(settings, "team_poll_question_rewrite_enabled", True))
    max_chars = max(40, int(getattr(settings, "team_poll_question_rewrite_max_chars", 220) or 220))
    min_confidence = max(
        0.0,
        min(
            1.0,
            float(
                getattr(settings, "team_poll_question_rewrite_min_confidence", 0.7)
                if getattr(settings, "team_poll_question_rewrite_min_confidence", None) is not None
                else 0.7
            ),
        ),
    )
    max_retries = max(0, int(getattr(settings, "team_poll_question_rewrite_max_retries", 2) or 2))
    retry_jitter_ms = max(0, int(getattr(settings, "team_poll_question_rewrite_retry_jitter_ms", 150) or 150))
    backoff_schedule_ms = _parse_backoff_schedule_ms(
        getattr(settings, "team_poll_question_rewrite_backoff_ms", "300,900")
    )
    if not rewrite_enabled:
        return {
            "question_text": _postprocess_question(original, max_chars=max_chars),
            "original_question_text": original,
            "source": "deterministic",
            "confidence": 1.0,
            "model": None,
        }

    total_attempts = max_retries + 1
    for attempt in range(total_attempts):
        rewritten = await _rewrite_with_llm(
            owner_text=owner_text,
            parsed_question=original,
            parsed_mode=parsed_mode,
            project_name=project_name,
            project_timezone=project_timezone,
        )
        if isinstance(rewritten, dict) and str(rewritten.get("question_text") or "").strip():
            confidence = float(rewritten.get("confidence") or 0.0)
            if confidence >= min_confidence:
                return {
                    "question_text": _postprocess_question(str(rewritten.get("question_text") or "").strip(), max_chars=max_chars),
                    "original_question_text": original,
                    "source": "llm",
                    "confidence": confidence,
                    "model": str(rewritten.get("model") or "").strip() or None,
                }
            print(
                "team_poll_question_rewriter_retry: "
                f"reason=low_confidence attempt={attempt + 1}/{total_attempts} "
                f"confidence={confidence:.3f} min_required={min_confidence:.3f}"
            )
        else:
            print(
                "team_poll_question_rewriter_retry: "
                f"reason=llm_unavailable_or_invalid attempt={attempt + 1}/{total_attempts}"
            )

        if attempt >= max_retries:
            break
        base_delay_ms = backoff_schedule_ms[min(attempt, len(backoff_schedule_ms) - 1)]
        jitter_ms = random.randint(0, retry_jitter_ms) if retry_jitter_ms > 0 else 0
        await asyncio.sleep((base_delay_ms + jitter_ms) / 1000.0)

    print("team_poll_question_rewriter_fallback: reason=llm_retries_exhausted")

    return {
        "question_text": _postprocess_question(original, max_chars=max_chars),
        "original_question_text": original,
        "source": "deterministic",
        "confidence": 1.0,
        "model": None,
    }
