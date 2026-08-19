"""Pending Team Poll reply relevance evaluation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.core.config import settings
from app.core.redis_queue import get_redis_connection
from app.llm import (
    LLMEmbeddingRequest,
    LLMGatewayRequest,
    LLMMessage,
    LLMMessageRole,
    get_shared_llm_gateway,
)
from app.team_poll.orchestrator import classify_owner_free_text_intent

_WORKITEM_KEY_RE = re.compile(r"\b[a-z][a-z0-9_]+-\d+\b", re.IGNORECASE)
_WORKITEM_MUTATION_HINTS_RE = re.compile(
    r"\b(assign|reassign|add|post|update|set|move|change|close|resolve|comment|edit|delete|remove)\b",
    re.IGNORECASE,
)
_WORKITEM_ENTITY_HINTS_RE = re.compile(
    r"\b(task|tasks|issue|ticket|story|epic|backlog|sprint|status|comment)\b",
    re.IGNORECASE,
)
_SKIP_LIKE_RE = re.compile(r"^\s*(skip|later|not now|no response)\s*$", re.IGNORECASE)
_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")

_QUESTION_EMBED_CACHE: dict[str, tuple[datetime, list[float]]] = {}
_REPLY_EMBED_CACHE: dict[str, tuple[datetime, list[float]]] = {}


@dataclass(frozen=True)
class PollRelevanceDecision:
    action: str  # accept_poll_response | remind_pending_poll | pass_to_cortex
    is_relevant: bool
    score: float
    reason: str
    path: str
    mutation_bypass: bool = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _cache_ttl_seconds() -> int:
    return max(60, int(getattr(settings, "team_poll_relevance_cache_ttl_seconds", 3600) or 3600))


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _hash_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _redis_get_vector(key: str) -> Optional[list[float]]:
    conn = get_redis_connection()
    if conn is None:
        return None
    try:
        raw = conn.get(key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        data = json.loads(str(raw))
        if isinstance(data, list) and data:
            return [float(v) for v in data]
    except Exception:
        return None
    return None


def _redis_set_vector(key: str, vector: list[float]) -> None:
    conn = get_redis_connection()
    if conn is None:
        return
    try:
        conn.set(key, json.dumps(vector), ex=_cache_ttl_seconds())
    except Exception:
        return


def _memory_get(cache: dict[str, tuple[datetime, list[float]]], key: str) -> Optional[list[float]]:
    row = cache.get(key)
    if not row:
        return None
    expires_at, vector = row
    if expires_at <= _utcnow():
        cache.pop(key, None)
        return None
    return list(vector)


def _memory_set(cache: dict[str, tuple[datetime, list[float]]], key: str, vector: list[float]) -> None:
    cache[key] = (_utcnow() + timedelta(seconds=_cache_ttl_seconds()), list(vector))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += float(x) * float(y)
        norm_a += float(x) * float(x)
        norm_b += float(y) * float(y)
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return float(dot / (math.sqrt(norm_a) * math.sqrt(norm_b)))


def _is_high_confidence_workitem_mutation(text: str) -> bool:
    normalized = _normalize_text(text).lower()
    if not normalized:
        return False
    has_key = bool(_WORKITEM_KEY_RE.search(normalized))
    has_mutation = bool(_WORKITEM_MUTATION_HINTS_RE.search(normalized))
    if has_key and has_mutation:
        return True
    return bool(has_mutation and _WORKITEM_ENTITY_HINTS_RE.search(normalized))


def _is_owner_control_bypass(text: str, *, is_owner: bool) -> bool:
    if not is_owner:
        return False
    normalized = _normalize_text(text).lower()
    if normalized in {"status", "close", "skip"}:
        return True
    intent = classify_owner_free_text_intent(text)
    return intent in {"status", "close"} or bool(_SKIP_LIKE_RE.match(text))


async def _embed_text(text: str, *, model: str, cache_key: str, cache: dict[str, tuple[datetime, list[float]]]) -> Optional[list[float]]:
    vector = _memory_get(cache, cache_key)
    if vector:
        return vector

    redis_vector = _redis_get_vector(cache_key)
    if redis_vector:
        _memory_set(cache, cache_key, redis_vector)
        return redis_vector

    gateway = get_shared_llm_gateway()
    response = await gateway.embed(
        LLMEmbeddingRequest(
            inputs=[text],
            provider="openai",
            model=model,
            retries=1,
            timeout_seconds=20,
            metadata={"feature": "team_poll_relevance"},
        )
    )
    if not bool(response.ok) or not response.vectors:
        return None
    raw = list(response.vectors[0] or [])
    if not raw:
        return None
    vector = [float(v) for v in raw]
    _memory_set(cache, cache_key, vector)
    _redis_set_vector(cache_key, vector)
    return vector


async def _llm_tiebreak_relevance(*, question_text: str, reply_text: str) -> Optional[dict[str, Any]]:
    gateway = get_shared_llm_gateway()
    prompt = (
        "Classify whether the user reply is related to the pending team poll question.\n"
        "Do NOT judge correctness. Only judge if this could reasonably be a response to the question.\n"
        "Return STRICT JSON only with this shape:\n"
        "{\"possible_answer\": true|false, \"likely_new_intent\": true|false, \"confidence\": number, \"reason\": string}\n"
        "Use likely_new_intent=true when the reply is a separate operational command (task mutation/assignment/comment).\n"
    )
    req = LLMGatewayRequest(
        messages=[
            LLMMessage(role=LLMMessageRole.SYSTEM, content=prompt),
            LLMMessage(
                role=LLMMessageRole.USER,
                content=json.dumps(
                    {"poll_question": question_text, "user_reply": reply_text},
                    ensure_ascii=True,
                ),
            ),
        ],
        provider="openai",
        model="gpt-4o-mini",
        retries=0,
        timeout_seconds=12,
        metadata={"feature": "team_poll_relevance_tiebreak"},
    )
    response = await gateway.generate(req)
    if not bool(response.ok):
        return None
    text = _normalize_text(response.text)
    if not text:
        return None
    payload: Optional[dict[str, Any]] = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:
        match = _JSON_BLOCK_RE.search(text)
        if match:
            try:
                parsed = json.loads(str(match.group(0) or "").strip())
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                payload = None
    if not isinstance(payload, dict):
        return None
    confidence_raw = payload.get("confidence")
    try:
        confidence = float(confidence_raw) if confidence_raw is not None else 0.0
    except Exception:
        confidence = 0.0
    possible_answer = payload.get("possible_answer")
    likely_new_intent = payload.get("likely_new_intent")
    return {
        "possible_answer": bool(possible_answer) if isinstance(possible_answer, bool) else str(possible_answer).strip().lower() in {"true", "1", "yes"},
        "likely_new_intent": bool(likely_new_intent) if isinstance(likely_new_intent, bool) else str(likely_new_intent).strip().lower() in {"true", "1", "yes"},
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": str(payload.get("reason") or "").strip(),
    }


async def evaluate_pending_poll_relevance(
    *,
    question_text: str,
    reply_text: str,
    poll_id: str,
    is_owner: bool,
) -> PollRelevanceDecision:
    normalized_question = _normalize_text(question_text)
    normalized_reply = _normalize_text(reply_text)
    if not normalized_reply:
        return PollRelevanceDecision(
            action="remind_pending_poll",
            is_relevant=False,
            score=0.0,
            reason="empty_reply",
            path="deterministic",
        )

    if _is_owner_control_bypass(normalized_reply, is_owner=is_owner):
        return PollRelevanceDecision(
            action="accept_poll_response",
            is_relevant=True,
            score=1.0,
            reason="owner_control_bypass",
            path="deterministic",
        )

    if _is_high_confidence_workitem_mutation(normalized_reply):
        return PollRelevanceDecision(
            action="pass_to_cortex",
            is_relevant=False,
            score=0.0,
            reason="workitem_mutation_bypass",
            path="deterministic",
            mutation_bypass=True,
        )

    if not bool(getattr(settings, "team_poll_relevance_enabled", True)):
        return PollRelevanceDecision(
            action="accept_poll_response",
            is_relevant=True,
            score=0.0,
            reason="relevance_disabled",
            path="disabled",
        )

    model = str(getattr(settings, "team_poll_relevance_model", "text-embedding-3-small") or "text-embedding-3-small").strip()
    question_cache_key = f"team_poll:embed:q:{_normalize_text(poll_id) or _hash_text(normalized_question)}"
    reply_cache_key = f"team_poll:embed:r:{_normalize_text(poll_id)}:{_hash_text(normalized_reply)}"
    question_vector = await _embed_text(
        normalized_question,
        model=model,
        cache_key=question_cache_key,
        cache=_QUESTION_EMBED_CACHE,
    )
    reply_vector = await _embed_text(
        normalized_reply,
        model=model,
        cache_key=reply_cache_key,
        cache=_REPLY_EMBED_CACHE,
    )

    score = 0.0
    threshold = float(getattr(settings, "team_poll_relevance_threshold", 0.62) or 0.62)
    gray_band = max(0.0, float(getattr(settings, "team_poll_relevance_gray_band", 0.08) or 0.08))
    upper = min(1.0, threshold + gray_band)
    if question_vector and reply_vector:
        score = _cosine_similarity(question_vector, reply_vector)
        if score >= upper:
            return PollRelevanceDecision(
                action="accept_poll_response",
                is_relevant=True,
                score=score,
                reason="embedding_above_upper_threshold",
                path="embedding",
            )

    if not bool(getattr(settings, "team_poll_relevance_llm_fallback_enabled", True)):
        return PollRelevanceDecision(
            action="remind_pending_poll",
            is_relevant=False,
            score=score,
            reason="llm_fallback_disabled",
            path="embedding_fallback",
        )

    llm_eval = await _llm_tiebreak_relevance(question_text=normalized_question, reply_text=normalized_reply)
    min_conf = float(getattr(settings, "team_poll_relevance_llm_min_confidence", 0.65) or 0.65)
    if isinstance(llm_eval, dict):
        confidence = float(llm_eval.get("confidence") or 0.0)
        reason = str(llm_eval.get("reason") or "").strip()
        if bool(llm_eval.get("possible_answer")) and confidence >= min_conf:
            return PollRelevanceDecision(
                action="accept_poll_response",
                is_relevant=True,
                score=score,
                reason=reason or "llm_possible_answer",
                path="llm_fallback",
            )
        return PollRelevanceDecision(
            action="remind_pending_poll",
            is_relevant=False,
            score=score,
            reason=reason or "llm_not_confident",
            path="llm_fallback",
        )

    return PollRelevanceDecision(
        action="remind_pending_poll",
        is_relevant=False,
        score=score,
        reason="llm_unavailable",
        path="llm_fallback",
    )
