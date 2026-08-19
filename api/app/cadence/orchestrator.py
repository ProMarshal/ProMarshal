"""Cadence Orchestrator — agenda-based multi-turn DM conversation handler.

Handles natural-language DM replies from users who have an active Cadence
check-in session.  Extracts task status updates and comments from free-text,
updates the session + Jira, then advances the agenda to the next task.

Works alongside the existing button/modal path — both can coexist.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

try:
    from motor.motor_asyncio import AsyncIOMotorDatabase
except Exception:  # pragma: no cover - local test environments may not install motor.
    AsyncIOMotorDatabase = Any  # type: ignore[misc,assignment]

from app.cadence.session_store import (
    SOURCE,
    ENTITY_TYPE,
    complete_session,
    get_session,
    update_task_status,
    update_task_comment,
    move_to_next_task,
    set_overall_input,
    set_overall_input_state,
    set_clarification_state,
    clear_clarification_state,
)
from app.cortex.domain.normalization import resolve_enum_alias
from app.cortex.domain.tasks.query_intent import STATUS_ALIASES
from app.cadence.interaction_handler import (
    send_next_task_message,
    send_session_summary,
    send_overall_update_prompt,
    update_jira_task,
    handle_backlog_show_request,
    handle_backlog_skip_request,
    handle_backlog_pick_start,
)
# _resolve_jira_access_token lives in jira_task_adapters, not interaction_handler.
from app.cadence.action_adapters.providers.jira_task_adapters import _resolve_jira_access_token
from app.cortex.tools.schemas import normalize_external_id
from app.integrations.jira.oauth import jira_oauth_service
from app.promarshal.prompt_registry import (
    compose_identity,
    compose_response_rules,
    compose_scope_policy,
    load_capability_scope_policy,
)
from app.domain.parsing.models import (
    PARSE_STATUS_AMBIGUOUS,
    PARSE_STATUS_INVALID,
    PARSE_STATUS_RESOLVED,
    ParseOutcome,
    clamp_confidence,
)
from app.domain.parsing.registry import message_for_reason_code
from app.domain.parsing.service import (
    ACTION_REJECT,
    build_clarification_payload,
    enforce_mutation_gate,
    parse_with_runtime_adapter,
)
from app.cadence.deterministic_interpreter import (
    interpret_cadence_deterministic as _interpret_shared_deterministic,
)
from app.domain.capabilities.deterministic.models import (
    ActionPatternSpec,
    DeterministicInterpretation,
)
from app.domain.capabilities.deterministic.service import deterministic_interpretation_service
from app.sessions.repository import SessionRepository
from app.agent_runtime.context import AgentContext
from app.agent_runtime.ack import send_ack_best_effort
from app.cadence.agent_tools import cadence_agent_runtime, _build_cadence_tool_descriptors
from app.core.session_lease import get_active_session_lease

logger = logging.getLogger("app.cadence.orchestrator")


# ---------------------------------------------------------------------------
# Active session lookup (by ProMarshal user_id, NOT Slack user ID)
# ---------------------------------------------------------------------------

async def find_active_session(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    project_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the first non-expired, non-completed Cadence session for a user+project."""
    return await SessionRepository(db=db).find_active_session(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        user_id=user_id,
        project_id=project_id,
    )


async def find_active_sessions_by_identity(
    db: AsyncIOMotorDatabase,
    *,
    provider: str,
    workspace_id: str,
    external_user_id: str,
    channel_id: Optional[str] = None,
    limit: int = 2,
) -> list[Dict[str, Any]]:
    """Return active cadence sessions for provider/workspace/external user identity."""
    return await SessionRepository(db=db).list_active_sessions_by_identity(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        provider=provider,
        workspace_id=workspace_id,
        external_user_id=external_user_id,
        channel_id=channel_id,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Shared deterministic interpretation (R1-R3)
# ---------------------------------------------------------------------------

def interpret_cadence_deterministic(
    *,
    text: str,
    provider: str = "jira",
    capability_ids: Optional[list[str]] = None,
) -> DeterministicInterpretation:
    """Use the canonical deterministic interpretation service for Cadence inputs."""
    return _interpret_shared_deterministic(
        text=text,
        provider=provider,
        capability_ids=capability_ids,
    )


# ---------------------------------------------------------------------------
# NL reply extraction  — uses LLM to parse free-text into status+comment
# ---------------------------------------------------------------------------

def _build_promarshal_extraction_system_prompt() -> str:
    identity = compose_identity(
        capability="cadence",
        legacy_identity="You are ProMarshal extracting task status updates from check-in replies.",
        legacy_overlay="",
    )
    scope_policy = compose_scope_policy(legacy_policy="", capability="cadence")
    response_rules = compose_response_rules(capability="cadence", legacy_rules="")
    extraction_rules = """Given a user's reply about a task, extract TWO things:

1. STATUS — did the user indicate a new status?  Valid statuses: "to do", "in progress", "in review", "done", or "no change"
2. COMMENT — any additional context, notes, or blockers the user mentioned.  Return empty string if none.

Respond ONLY in this exact JSON format (no markdown, no explanation):
{"status": "<status>", "comment": "<comment>"}

RULES:
- If the user says "done", "finished", "completed" → status = "done"
- If the user says "working on it", "started" → status = "in progress"
- If the user says "on hold", "paused", "blocked", or "waiting" → status = "no change" and keep that context in comment
- If the user says "skip", "next", "no update", "nothing" → status = "no change"
- Default to "no change" if unclear
- Keep comment concise (under 100 chars)

Examples:
User: "Done! Wrapped up the API integration yesterday"
→ {"status": "done", "comment": "Wrapped up the API integration yesterday"}

User: "Still working on it, ran into some auth issues"
→ {"status": "in progress", "comment": "Ran into some auth issues"}

User: "Skip"
→ {"status": "no change", "comment": ""}

User: "Nothing new here"
→ {"status": "no change", "comment": ""}
"""
    parts = [
        identity.strip(),
        scope_policy.strip(),
        response_rules.strip(),
        extraction_rules.strip(),
    ]
    return "\n\n".join([part for part in parts if part])


_EXTRACTION_SYSTEM_PROMPT = _build_promarshal_extraction_system_prompt()

_CADENCE_STATUS_SIGNAL_PATTERNS = [
    ActionPatternSpec(
        action_type="cadence_status_done",
        capability_id="workitem_update_status",
        tool_name="jira_update_status",
        regex=re.compile(r"\b(?P<status>done|finished|completed|shipped|closed)\b", re.IGNORECASE),
        field_extractors={"status": "status"},
        required_fields=["status"],
    ),
    ActionPatternSpec(
        action_type="cadence_status_in_progress",
        capability_id="workitem_update_status",
        tool_name="jira_update_status",
        regex=re.compile(r"\b(?P<status>working on|in progress|started|wip)\b", re.IGNORECASE),
        field_extractors={"status": "status"},
        required_fields=["status"],
    ),
    ActionPatternSpec(
        action_type="cadence_status_in_review",
        capability_id="workitem_update_status",
        tool_name="jira_update_status",
        regex=re.compile(r"\b(?P<status>review|pr ready|ready for review)\b", re.IGNORECASE),
        field_extractors={"status": "status"},
        required_fields=["status"],
    ),
    ActionPatternSpec(
        action_type="cadence_status_no_change",
        capability_id="workitem_update_status",
        tool_name="jira_update_status",
        regex=re.compile(
            r"\b(?P<status>on hold|paused|blocked|waiting|skip|next|nothing|no update|pass)\b",
            re.IGNORECASE,
        ),
        field_extractors={"status": "status"},
        required_fields=["status"],
    ),
]

_CADENCE_STATUS_ACTION_TO_VALUE = {
    "cadence_status_done": "done",
    "cadence_status_in_progress": "in progress",
    "cadence_status_in_review": "in review",
    "cadence_status_no_change": "no change",
}


async def extract_status_and_comment(
    text: str,
    **_kwargs: Any,
) -> ParseOutcome:
    """Extract and validate status update from a free-text DM reply.

    LLM output is treated as candidate parse only. The deterministic ParseOutcome
    validator runs for both LLM and heuristic paths.
    """
    # Try LLM extraction first.
    try:
        from app.cadence.llm_service import _call_llm

        raw = await _call_llm(
            text,
            max_tokens=100,
            temperature=0.0,
            system_prompt=_EXTRACTION_SYSTEM_PROMPT,
        )
        if raw:
            parsed = json.loads(str(raw).strip())
            candidate = {
                "status": str(parsed.get("status", "no change")).strip().lower(),
                "comment": str(parsed.get("comment", "")).strip(),
            }
            return _build_status_parse_outcome(
                text=text,
                candidate_status=str(candidate.get("status") or ""),
                comment=str(candidate.get("comment") or ""),
                source="llm",
            )
    except Exception as exc:
        logger.warning("cadence_llm_extraction_failed_using_heuristic error=%s", exc)

    # Heuristic fallback
    fallback = _heuristic_extract(text)
    return _build_status_parse_outcome(
        text=text,
        candidate_status=str(fallback.get("status") or ""),
        comment=str(fallback.get("comment") or ""),
        source="heuristic",
    )


def _heuristic_extract(text: str) -> Dict[str, str]:
    """Simple keyword-based extraction when LLM is unavailable."""
    lower = text.lower().strip()

    # Shared deterministic engine (R1-R3) is the first fallback path for Cadence.
    status = _deterministic_status_from_shared_service(text)

    # Legacy keyword fallback remains for parity when deterministic status is unmatched.
    if not status:
        if any(kw in lower for kw in ("done", "finished", "completed", "shipped")):
            status = "done"
        elif any(kw in lower for kw in ("working on", "in progress", "started", "wip")):
            status = "in progress"
        elif any(kw in lower for kw in ("on hold", "paused", "blocked", "waiting")):
            status = "no change"
        elif any(kw in lower for kw in ("review", "pr ready", "ready for review")):
            status = "in review"
        elif any(kw in lower for kw in ("skip", "next", "nothing", "no update", "pass")):
            status = "no change"
        else:
            status = "no change"

    # Comment: use the full text unless it's just a status keyword
    comment = "" if lower in ("skip", "next", "done", "pass") else text.strip()

    return {"status": status, "comment": comment}


def _deterministic_status_from_shared_service(text: str) -> Optional[str]:
    interpretation = deterministic_interpretation_service.interpret(
        text=text,
        pattern_specs=_CADENCE_STATUS_SIGNAL_PATTERNS,
    )
    if not interpretation.matches:
        return None
    best_match = sorted(
        interpretation.matches,
        key=lambda item: (item.span_len(), len(item.extracted_slots or {})),
        reverse=True,
    )[0]
    status_value = _CADENCE_STATUS_ACTION_TO_VALUE.get(str(best_match.action_type or "").strip().lower())
    if status_value:
        return status_value
    raw_status = str((best_match.extracted_slots or {}).get("status") or "").strip()
    normalized = _normalize_cadence_status(raw_status)
    if normalized == "in_progress":
        return "in progress"
    if normalized == "in_review":
        return "in review"
    if normalized == "no_change":
        return "no change"
    return normalized


_BACKLOG_SHOW_KEYWORDS = {
    "show", "view", "list", "show me", "yes", "yep", "yeah", "sure", "ok", "okay", "go ahead"
}
_BACKLOG_LATER_KEYWORDS = {
    "later", "not now", "skip", "pass", "no", "nope", "nah", "i'm good", "im good", "no thanks", "maybe later"
}
_OVERALL_SKIP_KEYWORDS = {
    "skip",
    "pass",
    "none",
    "na",
    "n/a",
    "nothing",
    "nothing else",
    "nothing to add",
    "no update",
    "no updates",
    "no additional update",
    "no additional updates",
    "no other update",
    "no other updates",
    "thats all",
    "that's all",
    "all good",
}

_CADENCE_STATUS_EXTRA_ALIASES = {
    # Cadence keeps in-review distinct from generic in-progress parsing.
    "inreview": "in_review",
    "in_review": "in_review",
    "in-review": "in_review",
    "in review": "in_review",
    "review": "in_review",
    # Cadence also supports explicit no-change outcomes.
    "no_change": "no_change",
    "no-change": "no_change",
    "no change": "no_change",
    "skip": "no_change",
    "pass": "no_change",
}

_STATUS_TERMINAL_PHRASES = {"done", "finished", "completed", "complete", "closed", "shipped"}
_STATUS_ACTIVE_PHRASES = {"working on", "in progress", "started", "wip", "in review", "review", "testing", "qa"}
_STATUS_BLOCKED_PHRASES = {"on hold", "paused", "blocked", "waiting"}
_STATUS_NON_TERMINAL_PHRASES = _STATUS_ACTIVE_PHRASES | _STATUS_BLOCKED_PHRASES

_BACKLOG_ALLOWED_INTENTS = {"show_backlog", "skip_backlog", "pick_task"}
_STATUS_CLARIFICATION_STATE = "awaiting_status_clarification"
_BACKLOG_CLARIFICATION_STATE = "awaiting_backlog_clarification"
_CLARIFICATION_MAX_RETRIES = 2


def _emit_cadence_parse_telemetry(
    *,
    event: str,
    session_id: str,
    reason_code: str,
    parse_status: str,
    requires_clarification: bool,
    clarification_kind: Optional[str] = None,
    retry_count: Optional[int] = None,
    source: Optional[str] = None,
    deterministic_confidence: Optional[float] = None,
    deterministic_match_count: Optional[int] = None,
) -> None:
    """Emit structured parser telemetry for cadence hardening paths."""
    payload: Dict[str, Any] = {
        "event": str(event or "").strip().lower() or "parse_event",
        "session_id": str(session_id or "").strip(),
        "reason_code": str(reason_code or "").strip().lower() or "unknown",
        "parse_status": str(parse_status or "").strip().lower() or "unknown",
        "requires_clarification": bool(requires_clarification),
    }
    if clarification_kind:
        payload["clarification_kind"] = str(clarification_kind).strip().lower()
    if retry_count is not None:
        payload["retry_count"] = int(retry_count)
    if source:
        payload["source"] = str(source).strip().lower()
    if deterministic_confidence is not None:
        payload["deterministic_confidence"] = float(deterministic_confidence)
    if deterministic_match_count is not None:
        payload["deterministic_match_count"] = int(deterministic_match_count)
    logger.info("cadence_parse_telemetry %s", json.dumps(payload, ensure_ascii=True))


def _normalize_user_text(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _contains_any_phrase(text: str, phrases: set[str]) -> bool:
    normalized = _normalize_user_text(text)
    if not normalized:
        return False
    if normalized in phrases:
        return True
    return any(f" {phrase} " in f" {normalized} " for phrase in phrases)


def _normalize_cadence_status(value: Any) -> Optional[str]:
    alias_map = dict(STATUS_ALIASES)
    alias_map.update(_CADENCE_STATUS_EXTRA_ALIASES)
    mapped = resolve_enum_alias(str(value or ""), alias_map)
    if mapped in {"todo", "in_progress", "in_review", "done", "no_change"}:
        return mapped
    return None


def _has_mixed_terminal_non_terminal_signals(text: str) -> bool:
    return _contains_any_phrase(text, _STATUS_TERMINAL_PHRASES) and _contains_any_phrase(
        text, _STATUS_NON_TERMINAL_PHRASES
    )


def _status_display(value: str) -> str:
    token = str(value or "").strip().lower()
    return token.replace("_", " ")


def _build_status_parse_outcome(
    *,
    text: str,
    candidate_status: str,
    comment: str,
    source: str,
) -> ParseOutcome:
    normalized_status = _normalize_cadence_status(candidate_status)
    normalized_comment = str(comment or "").strip()
    normalized_source = str(source or "").strip().lower() or "unknown"
    has_terminal_signal = _contains_any_phrase(text, _STATUS_TERMINAL_PHRASES)
    has_non_terminal_signal = _contains_any_phrase(text, _STATUS_NON_TERMINAL_PHRASES)

    if _has_mixed_terminal_non_terminal_signals(text):
        return ParseOutcome(
            status=PARSE_STATUS_AMBIGUOUS,
            reason_code="ambiguous_mixed_signals",
            confidence=clamp_confidence(0.2),
            requires_clarification=True,
            normalized_payload={
                "status": normalized_status or "no_change",
                "comment": normalized_comment,
                "source": normalized_source,
            },
        )

    if normalized_status is None:
        return ParseOutcome(
            status=PARSE_STATUS_INVALID,
            reason_code="invalid_input",
            confidence=clamp_confidence(0.0),
            requires_clarification=True,
            normalized_payload={
                "status": "no_change",
                "comment": normalized_comment,
                "source": normalized_source,
            },
        )

    if has_terminal_signal and not has_non_terminal_signal and normalized_status in {"todo", "in_progress", "in_review"}:
        return ParseOutcome(
            status=PARSE_STATUS_AMBIGUOUS,
            reason_code="ambiguous_mixed_signals",
            confidence=clamp_confidence(0.2),
            requires_clarification=True,
            normalized_payload={
                "status": normalized_status,
                "comment": normalized_comment,
                "source": normalized_source,
            },
        )

    if has_non_terminal_signal and not has_terminal_signal and normalized_status == "done":
        return ParseOutcome(
            status=PARSE_STATUS_AMBIGUOUS,
            reason_code="ambiguous_mixed_signals",
            confidence=clamp_confidence(0.2),
            requires_clarification=True,
            normalized_payload={
                "status": normalized_status,
                "comment": normalized_comment,
                "source": normalized_source,
            },
        )

    base_confidence = 0.9 if normalized_source == "llm" else 0.75
    return ParseOutcome(
        status=PARSE_STATUS_RESOLVED,
        reason_code="resolved",
        confidence=clamp_confidence(base_confidence),
        requires_clarification=False,
        normalized_payload={
            "status": normalized_status,
            "comment": normalized_comment,
            "source": normalized_source,
        },
    )


def _build_status_clarification_text(
    *,
    outcome: ParseOutcome,
    candidate_statuses: Optional[list[str]] = None,
) -> str:
    reason_code = str(outcome.reason_code or "clarification_required").strip().lower()
    status = str((outcome.normalized_payload or {}).get("status") or "").strip()
    detail = _reason_message(
        reason_code,
        context={
            "requested_status": status,
            "available_statuses": candidate_statuses or [],
        },
    )
    if status:
        return f"{detail} I currently interpreted it as `{_status_display(status)}`."
    return detail


def _reason_message(
    reason_code: str,
    *,
    fallback: str = "I need a clarification before I proceed.",
    context: Optional[Dict[str, Any]] = None,
) -> str:
    normalized = str(reason_code or "").strip().lower()
    return message_for_reason_code(normalized, fallback=fallback, context=context)


async def _available_jira_statuses_for_task(
    *,
    db: AsyncIOMotorDatabase,
    session: Dict[str, Any],
    task_key: str,
) -> list[str]:
    session_task_provider = str(session.get("task_provider") or "jira").strip().lower() or "jira"
    if session_task_provider != "jira":
        return []
    project_id = str(session.get("project_id") or "").strip()
    if not project_id or not task_key:
        return []
    project = await db.projects.find_one({"project_id": project_id})
    jira_integration = ((project or {}).get("integrations") or {}).get("jira")
    if not isinstance(jira_integration, dict):
        return []
    cloud_id = str(jira_integration.get("cloud_id") or "").strip()
    if not cloud_id:
        return []
    try:
        access_token = await _resolve_jira_access_token(
            db=db,
            project_id=project_id,
            jira_integration=jira_integration,
        )
        transitions = await jira_oauth_service.get_available_transitions(
            access_token=access_token,
            cloud_id=cloud_id,
            task_key=str(task_key or "").strip(),
        )
    except Exception as exc:
        print(f"[cadence] Jira transitions lookup failed for {task_key}: {exc}")
        return []

    names: list[str] = []
    for transition in transitions or []:
        candidate = str(((transition or {}).get("to") or {}).get("name") or "").strip()
        if candidate and candidate not in names:
            names.append(candidate)
    return names


def _provider_status_unavailable(
    *,
    requested_status: str,
    available_statuses: list[str],
) -> bool:
    normalized_requested = str(requested_status or "").strip().lower()
    if normalized_requested in {"", "no_change"}:
        return False
    try:
        target_names = jira_oauth_service._status_mapping_for_target().get(normalized_requested, [])
    except Exception:
        target_names = []
    if not target_names:
        return False
    available_lookup = {str(item or "").strip().lower() for item in available_statuses}
    expected_lookup = {str(item or "").strip().lower() for item in target_names}
    return bool(available_lookup) and available_lookup.isdisjoint(expected_lookup)


async def _classify_backlog_intent(text: str, **_kwargs: Any) -> ParseOutcome:
    has_show = _contains_any_phrase(text, _BACKLOG_SHOW_KEYWORDS)
    has_later = _contains_any_phrase(text, _BACKLOG_LATER_KEYWORDS)
    if has_show and has_later:
        return ParseOutcome(
            status=PARSE_STATUS_AMBIGUOUS,
            reason_code="ambiguous_target",
            confidence=clamp_confidence(0.2),
            requires_clarification=True,
            normalized_payload={"intent": "unclear"},
        )
    if has_show:
        return ParseOutcome(
            status=PARSE_STATUS_RESOLVED,
            reason_code="resolved",
            confidence=clamp_confidence(0.95),
            requires_clarification=False,
            normalized_payload={"intent": "show_backlog"},
        )
    if has_later:
        return ParseOutcome(
            status=PARSE_STATUS_RESOLVED,
            reason_code="resolved",
            confidence=clamp_confidence(0.95),
            requires_clarification=False,
            normalized_payload={"intent": "skip_backlog"},
        )

    llm_intent = await _classify_backlog_intent_with_llm(text)
    if llm_intent in _BACKLOG_ALLOWED_INTENTS:
        return ParseOutcome(
            status=PARSE_STATUS_RESOLVED,
            reason_code="resolved",
            confidence=clamp_confidence(0.8),
            requires_clarification=False,
            normalized_payload={"intent": llm_intent},
        )

    return ParseOutcome(
        status=PARSE_STATUS_AMBIGUOUS,
        reason_code="ambiguous_target",
        confidence=clamp_confidence(0.2),
        requires_clarification=True,
        normalized_payload={"intent": "unclear"},
    )


def _parse_overall_update_input(text: str) -> Dict[str, Any]:
    normalized = _normalize_user_text(text)
    if not normalized:
        return {"skipped": True, "text": ""}
    if normalized in _OVERALL_SKIP_KEYWORDS:
        return {"skipped": True, "text": ""}
    return {"skipped": False, "text": str(text or "").strip()}


def _extract_backlog_index(text: str, backlog_count: int) -> Optional[int]:
    raw = str(text or "").strip()
    if not raw:
        return None

    if raw.isdigit():
        idx = int(raw) - 1
        return idx if 0 <= idx < backlog_count else None

    import re

    verb_num = re.search(r"\b(?:pick|choose|start|take|do)\s+#?(\d{1,3})\b", raw, flags=re.IGNORECASE)
    if verb_num:
        idx = int(verb_num.group(1)) - 1
        return idx if 0 <= idx < backlog_count else None
    return None


def _resolve_backlog_index_from_text(
    *,
    text: str,
    backlog_tasks: list[Dict[str, Any]],
) -> Optional[int]:
    if not backlog_tasks:
        return None

    by_index = _extract_backlog_index(text, len(backlog_tasks))
    if by_index is not None:
        return by_index

    import re

    raw = str(text or "").strip()
    normalized_ids: Dict[str, int] = {}
    for idx, task in enumerate(backlog_tasks):
        ext = str(task.get("external_id") or "").strip()
        if not ext:
            continue
        try:
            canonical = normalize_external_id(ext)
        except Exception:
            canonical = ext.upper()
        normalized_ids[canonical] = idx

    explicit_candidates = re.findall(r"\b[A-Za-z][A-Za-z0-9]*-\d+\b", raw)
    compact_candidates = re.findall(r"\b([A-Za-z][A-Za-z0-9]*)[\s_-]?(\d{1,8})\b", raw)
    candidates: list[str] = []
    for candidate in explicit_candidates:
        candidates.append(candidate)
    for key_part, num_part in compact_candidates:
        candidates.append(f"{key_part}-{num_part}")

    for candidate in candidates:
        try:
            canonical_candidate = normalize_external_id(candidate)
        except Exception:
            continue
        if canonical_candidate in normalized_ids:
            return normalized_ids[canonical_candidate]
    return None


def _build_backlog_classifier_system_prompt() -> str:
    identity = compose_identity(
        capability="cadence",
        legacy_identity="You are ProMarshal classifying check-in backlog replies.",
        legacy_overlay="",
    )
    scope_policy = compose_scope_policy(legacy_policy="", capability="cadence")
    response_rules = compose_response_rules(capability="cadence", legacy_rules="")
    classifier_rules = (
        "You are a strict classifier. Output one label only.\n"
        "Allowed labels: show_backlog, skip_backlog, pick_task, unclear."
    )
    parts = [
        identity.strip(),
        scope_policy.strip(),
        response_rules.strip(),
        classifier_rules.strip(),
    ]
    return "\n\n".join([part for part in parts if part])


_BACKLOG_CLASSIFIER_SYSTEM_PROMPT = _build_backlog_classifier_system_prompt()
_RUNTIME_PARSERS_REGISTERED = False


def _ensure_runtime_parser_adapters_registered() -> None:
    global _RUNTIME_PARSERS_REGISTERED
    if _RUNTIME_PARSERS_REGISTERED:
        return
    from app.cadence.parsing_adapter import register_cadence_runtime_adapters

    register_cadence_runtime_adapters(
        status_parser=extract_status_and_comment,
        backlog_parser=_classify_backlog_intent,
    )
    _RUNTIME_PARSERS_REGISTERED = True


async def _classify_backlog_intent_with_llm(text: str) -> str:
    """
    Return one of: show_backlog, skip_backlog, pick_task, unclear.
    """
    try:
        from app.cadence.llm_service import _call_llm

        prompt = (
            "Classify this user reply in a backlog follow-up flow. "
            "Return exactly one label: show_backlog, skip_backlog, pick_task, unclear.\n\n"
            "show_backlog: user wants to see backlog list.\n"
            "skip_backlog: user wants to defer/skip for now.\n"
            "pick_task: user selects or asks to start a specific backlog task.\n"
            "unclear: none of the above.\n\n"
            f"Reply: {text}"
        )
        raw = await _call_llm(
            prompt,
            temperature=0.0,
            max_tokens=8,
            system_prompt=_BACKLOG_CLASSIFIER_SYSTEM_PROMPT,
        )
        if raw:
            lowered = str(raw).strip().lower()
            label = lowered.split()[0].strip().strip("`")
            if label in {"show_backlog", "skip_backlog", "pick_task", "unclear"}:
                return label
    except Exception as exc:
        print(f"[cadence/orchestrator] Backlog intent LLM classifier failed: {exc}")
    return "unclear"


async def _send_dm_text_best_effort(*, slack_client: Any, channel: str, text: str) -> None:
    if not slack_client:
        return
    try:
        await slack_client.chat_postMessage(channel=channel, text=text)
    except Exception as exc:
        print(f"[cadence/orchestrator] Failed to send DM guidance: {exc}")


async def _handle_backlog_pick(
    db: AsyncIOMotorDatabase,
    *,
    session: Dict[str, Any],
    selection_index: int,
    slack_client: Any,
    channel: str,
) -> bool:
    session_id = str(session.get("session_id") or "")
    try:
        pick_result = await handle_backlog_pick_start(
            db,
            session_id,
            backlog_index=selection_index,
        )
    except Exception as exc:
        print(f"[cadence/orchestrator] Backlog pick failed: session={session_id} error={exc}")
        await _send_dm_text_best_effort(
            slack_client=slack_client,
            channel=channel,
            text="I couldn't start that backlog task right now. Please try again.",
        )
        return True

    if not pick_result.get("ok"):
        await _send_dm_text_best_effort(
            slack_client=slack_client,
            channel=channel,
            text="I couldn't start that backlog task right now. Please try again.",
        )
        return True

    external_id = str(pick_result.get("external_id") or "task")
    remaining = int(pick_result.get("remaining_backlog") or 0)
    already_progressed = bool(pick_result.get("already_progressed"))
    if remaining > 0:
        lead = f"Moved `{external_id}` to In Progress."
        if already_progressed:
            lead = f"`{external_id}` was already moved forward."
        await _send_dm_text_best_effort(
            slack_client=slack_client,
            channel=channel,
            text=(
                f"{lead} "
                f"You still have {remaining} backlog task(s). "
                "Reply `show` to pick another or `later` to close for now."
            ),
        )
    else:
        lead = f"Moved `{external_id}` to In Progress."
        if already_progressed:
            lead = f"`{external_id}` was already moved forward."
        await _send_dm_text_best_effort(
            slack_client=slack_client,
            channel=channel,
            text=f"{lead} No remaining backlog tasks.",
        )
        await send_session_summary(db, session_id)
    return True


def _build_backlog_clarification_payload(
    *,
    reason_code: str,
    text: str,
    return_state: str,
    retry_count: int = 0,
) -> Dict[str, Any]:
    return build_clarification_payload(
        reason_code=str(reason_code or "clarification_required"),
        original_user_text=str(text or ""),
        parser_understanding={"intent": "unclear"},
        retry_count=int(retry_count),
        max_retries=_CLARIFICATION_MAX_RETRIES,
        clarification_kind="backlog",
        extra={"return_state": str(return_state or "awaiting_backlog_decision").strip().lower()},
    )


def _build_backlog_clarification_text() -> str:
    detail = _reason_message(
        "ambiguous_target",
        context={"target_kind": "backlog"},
    )
    return (
        f"{detail} "
        "Reply `show` to see backlog tasks, `later` to skip, or provide a task number/key to start one."
    )


async def _handle_backlog_clarification_state(
    db: AsyncIOMotorDatabase,
    *,
    session: Dict[str, Any],
    text: str,
    slack_client: Any,
    channel: str,
) -> bool:
    _ensure_runtime_parser_adapters_registered()
    payload = dict(session.get("clarification_payload") or {})
    retry_count = int(payload.get("retry_count") or 0)
    max_retries = int(payload.get("max_retries") or _CLARIFICATION_MAX_RETRIES)
    return_state = str(payload.get("return_state") or "awaiting_backlog_decision").strip().lower()

    backlog_tasks = list(session.get("backlog_tasks") or [])
    pick_idx = _resolve_backlog_index_from_text(text=text, backlog_tasks=backlog_tasks)
    if pick_idx is not None:
        await clear_clarification_state(db, str(session.get("session_id") or ""), next_status=return_state)
        return await _handle_backlog_pick(
            db,
            session=session,
            selection_index=pick_idx,
            slack_client=slack_client,
            channel=channel,
        )

    outcome = await parse_with_runtime_adapter(
        "cadence.backlog_intent",
        text=text,
        parser_context={
            "session_id": str(session.get("session_id") or ""),
            "state": "awaiting_backlog_clarification",
        },
    )
    gate = enforce_mutation_gate(outcome)
    if gate.action == ACTION_REJECT:
        await clear_clarification_state(db, str(session.get("session_id") or ""), next_status=return_state)
        await _send_dm_text_best_effort(
            slack_client=slack_client,
            channel=channel,
            text=_reason_message(gate.reason_code),
        )
        return True
    if outcome.status == PARSE_STATUS_RESOLVED:
        intent = str((outcome.normalized_payload or {}).get("intent") or "").strip().lower()
        await clear_clarification_state(db, str(session.get("session_id") or ""), next_status=return_state)
        _emit_cadence_parse_telemetry(
            event="clarification_resolved",
            session_id=str(session.get("session_id") or ""),
            reason_code=str(outcome.reason_code or "resolved"),
            parse_status=outcome.status,
            requires_clarification=False,
            clarification_kind="backlog",
            retry_count=retry_count,
            source="classifier",
        )
        if intent == "show_backlog":
            await handle_backlog_show_request(db, str(session.get("session_id") or ""))
            return True
        if intent == "skip_backlog":
            await handle_backlog_skip_request(db, str(session.get("session_id") or ""))
            return True
        if intent == "pick_task":
            await _send_dm_text_best_effort(
                slack_client=slack_client,
                channel=channel,
                text="Reply with the task number or key to start it (for example: `1` or `SCRUM-4`).",
            )
            return True

    if retry_count + 1 >= max_retries:
        await clear_clarification_state(db, str(session.get("session_id") or ""), next_status=return_state)
        _emit_cadence_parse_telemetry(
            event="clarification_max_retry_fallback",
            session_id=str(session.get("session_id") or ""),
            reason_code=str(outcome.reason_code or "ambiguous_target"),
            parse_status=outcome.status,
            requires_clarification=True,
            clarification_kind="backlog",
            retry_count=retry_count + 1,
            source="classifier",
        )
        await _send_dm_text_best_effort(
            slack_client=slack_client,
            channel=channel,
            text=(
                f"{_reason_message(outcome.reason_code or 'ambiguous_target', context={'target_kind': 'backlog'})} "
                "No backlog change made. Reply `show` or `later`."
            ),
        )
        return True

    updated_payload = _build_backlog_clarification_payload(
        reason_code=outcome.reason_code or "ambiguous_target",
        text=text,
        return_state=return_state,
        retry_count=retry_count + 1,
    )
    await set_clarification_state(
        db,
        str(session.get("session_id") or ""),
        status=_BACKLOG_CLARIFICATION_STATE,
        payload=updated_payload,
    )
    _emit_cadence_parse_telemetry(
        event="clarification_retry",
        session_id=str(session.get("session_id") or ""),
        reason_code=str(outcome.reason_code or "ambiguous_target"),
        parse_status=outcome.status,
        requires_clarification=True,
        clarification_kind="backlog",
        retry_count=int(updated_payload.get("retry_count") or 0),
        source="classifier",
    )
    await _send_dm_text_best_effort(
        slack_client=slack_client,
        channel=channel,
        text=_build_backlog_clarification_text(),
    )
    return True


async def _handle_backlog_decision_state(
    db: AsyncIOMotorDatabase,
    *,
    session: Dict[str, Any],
    text: str,
    slack_client: Any,
    channel: str,
) -> bool:
    session_id = str(session.get("session_id") or "")
    _ensure_runtime_parser_adapters_registered()
    backlog_tasks = list(session.get("backlog_tasks") or [])
    if not backlog_tasks:
        await handle_backlog_skip_request(db, session_id)
        return True

    # Support direct pick even from decision state (e.g., "start SCRUM-4").
    pick_idx = _resolve_backlog_index_from_text(text=text, backlog_tasks=backlog_tasks)
    if pick_idx is not None:
        return await _handle_backlog_pick(
            db,
            session=session,
            selection_index=pick_idx,
            slack_client=slack_client,
            channel=channel,
        )

    outcome = await parse_with_runtime_adapter(
        "cadence.backlog_intent",
        text=text,
        parser_context={
            "session_id": session_id,
            "state": "awaiting_backlog_decision",
        },
    )
    gate = enforce_mutation_gate(outcome)
    if gate.action == ACTION_REJECT:
        await _send_dm_text_best_effort(
            slack_client=slack_client,
            channel=channel,
            text=_reason_message(gate.reason_code),
        )
        return True
    intent = str((outcome.normalized_payload or {}).get("intent") or "").strip().lower()
    if outcome.status == PARSE_STATUS_RESOLVED:
        if intent == "show_backlog":
            await handle_backlog_show_request(db, session_id)
            return True
        if intent == "skip_backlog":
            await handle_backlog_skip_request(db, session_id)
            return True
        if intent == "pick_task":
            await _send_dm_text_best_effort(
                slack_client=slack_client,
                channel=channel,
                text="Reply with the task number or key to start it (for example: `1` or `SCRUM-4`).",
            )
            return True

    if gate.requires_clarification():
        payload = _build_backlog_clarification_payload(
            reason_code=outcome.reason_code or "ambiguous_target",
            text=text,
            return_state="awaiting_backlog_decision",
        )
        await set_clarification_state(db, session_id, status=_BACKLOG_CLARIFICATION_STATE, payload=payload)
        _emit_cadence_parse_telemetry(
            event="clarification_started",
            session_id=session_id,
            reason_code=str(outcome.reason_code or "ambiguous_target"),
            parse_status=outcome.status,
            requires_clarification=True,
            clarification_kind="backlog",
            retry_count=0,
            source="classifier",
        )
        await _send_dm_text_best_effort(
            slack_client=slack_client,
            channel=channel,
            text=_build_backlog_clarification_text(),
        )
        return True

    await _send_dm_text_best_effort(
        slack_client=slack_client,
        channel=channel,
        text="Reply `show` to see backlog tasks, or `later` to skip for now.",
    )
    return True


async def _handle_backlog_pick_state(
    db: AsyncIOMotorDatabase,
    *,
    session: Dict[str, Any],
    text: str,
    slack_client: Any,
    channel: str,
) -> bool:
    session_id = str(session.get("session_id") or "")
    _ensure_runtime_parser_adapters_registered()
    backlog_tasks = list(session.get("backlog_tasks") or [])
    if not backlog_tasks:
        await handle_backlog_skip_request(db, session_id)
        return True

    pick_idx = _resolve_backlog_index_from_text(text=text, backlog_tasks=backlog_tasks)
    if pick_idx is not None:
        return await _handle_backlog_pick(
            db,
            session=session,
            selection_index=pick_idx,
            slack_client=slack_client,
            channel=channel,
        )

    outcome = await parse_with_runtime_adapter(
        "cadence.backlog_intent",
        text=text,
        parser_context={
            "session_id": session_id,
            "state": "awaiting_backlog_pick",
        },
    )
    gate = enforce_mutation_gate(outcome)
    if gate.action == ACTION_REJECT:
        await _send_dm_text_best_effort(
            slack_client=slack_client,
            channel=channel,
            text=_reason_message(gate.reason_code),
        )
        return True
    intent = str((outcome.normalized_payload or {}).get("intent") or "").strip().lower()
    if outcome.status == PARSE_STATUS_RESOLVED:
        if intent == "skip_backlog":
            await handle_backlog_skip_request(db, session_id)
            return True
        if intent == "show_backlog":
            await handle_backlog_show_request(db, session_id)
            return True

    if gate.requires_clarification():
        payload = _build_backlog_clarification_payload(
            reason_code=outcome.reason_code or "ambiguous_target",
            text=text,
            return_state="awaiting_backlog_pick",
        )
        await set_clarification_state(db, session_id, status=_BACKLOG_CLARIFICATION_STATE, payload=payload)
        _emit_cadence_parse_telemetry(
            event="clarification_started",
            session_id=session_id,
            reason_code=str(outcome.reason_code or "ambiguous_target"),
            parse_status=outcome.status,
            requires_clarification=True,
            clarification_kind="backlog",
            retry_count=0,
            source="classifier",
        )
        await _send_dm_text_best_effort(
            slack_client=slack_client,
            channel=channel,
            text=_build_backlog_clarification_text(),
        )
        return True

    await _send_dm_text_best_effort(
        slack_client=slack_client,
        channel=channel,
        text="Please reply with a task number or key (for example: `1` or `SCRUM-4`), or reply `later`.",
    )
    return True


def _build_status_clarification_payload(
    *,
    outcome: ParseOutcome,
    text: str,
    task_index: int,
    retry_count: int = 0,
    candidate_statuses: Optional[list[str]] = None,
) -> Dict[str, Any]:
    clean_candidates = [str(item).strip() for item in (candidate_statuses or []) if str(item).strip()]
    return build_clarification_payload(
        reason_code=str(outcome.reason_code or "clarification_required"),
        original_user_text=str(text or ""),
        parser_understanding=dict(outcome.normalized_payload or {}),
        retry_count=int(retry_count),
        max_retries=_CLARIFICATION_MAX_RETRIES,
        clarification_kind="status",
        candidate_statuses=clean_candidates,
        extra={"task_index": int(task_index)},
    )


async def _advance_after_task_update(
    db: AsyncIOMotorDatabase,
    *,
    session: Dict[str, Any],
    task: Dict[str, Any],
    task_index: int,
    new_status: str,
    comment: str,
    status_changed: bool,
    has_comment: bool,
) -> bool:
    session_id = str(session.get("session_id") or "")
    task_key = task.get("external_id", "N/A")
    current_status = _normalize_cadence_status(task.get("status")) or "todo"

    if status_changed:
        await update_task_status(db, session_id, task_index, new_status)
    if has_comment:
        await update_task_comment(db, session_id, task_index, comment)

    if status_changed or has_comment:
        updated_session = await get_session(db, session_id)
        if updated_session:
            await update_jira_task(db, updated_session, task_index)

    previous_context = {
        "task_key": task_key,
        "title": task.get("title", ""),
        "new_status": new_status if status_changed else current_status,
        "comment": comment,
        "status_changed": status_changed,
        "has_comment": has_comment,
    }

    is_last_agenda_task = (task_index + 1) >= len(session.get("tasks", []))
    if is_last_agenda_task:
        await set_overall_input_state(db, session_id)
        print(f"Cadence state transition: session={session_id} status=awaiting_overall_input")
        await send_overall_update_prompt(db, session_id)
        return True

    has_more = await move_to_next_task(db, session_id)
    if has_more:
        await send_next_task_message(db, session_id, previous_context)
    else:
        print(f"[cadence] Session completed via DM | session={session_id}")
        await send_session_summary(db, session_id)
    return True


async def _handle_status_clarification_state(
    db: AsyncIOMotorDatabase,
    *,
    session: Dict[str, Any],
    text: str,
    slack_client: Any,
    channel: str,
) -> bool:
    _ensure_runtime_parser_adapters_registered()
    payload = dict(session.get("clarification_payload") or {})
    retry_count = int(payload.get("retry_count") or 0)
    candidate_statuses = [
        str(item).strip()
        for item in (payload.get("candidate_statuses") or [])
        if str(item).strip()
    ]
    task_index = int(payload.get("task_index") or session.get("current_task_index") or 0)
    tasks = list(session.get("tasks") or [])
    if task_index < 0 or task_index >= len(tasks):
        await clear_clarification_state(db, str(session.get("session_id") or ""), next_status="awaiting_status")
        await _send_dm_text_best_effort(
            slack_client=slack_client,
            channel=channel,
            text="I couldn't continue that clarification because task context changed. Please resend your update.",
        )
        return True

    outcome = await parse_with_runtime_adapter(
        "cadence.status_update",
        text=text,
        parser_context={
            "session_id": str(session.get("session_id") or ""),
            "state": "awaiting_status_clarification",
        },
    )
    gate = enforce_mutation_gate(outcome)
    if gate.action == ACTION_REJECT:
        await clear_clarification_state(db, str(session.get("session_id") or ""), next_status="awaiting_status")
        await _send_dm_text_best_effort(
            slack_client=slack_client,
            channel=channel,
            text=_reason_message(gate.reason_code),
        )
        return True
    if gate.allows_write():
        await clear_clarification_state(db, str(session.get("session_id") or ""), next_status="awaiting_status")
        normalized_status = str((outcome.normalized_payload or {}).get("status") or "no_change").strip().lower()
        normalized_comment = str((outcome.normalized_payload or {}).get("comment") or "").strip()
        current_status = _normalize_cadence_status(tasks[task_index].get("status")) or "todo"
        status_changed = normalized_status not in ("no_change", "") and normalized_status != current_status
        has_comment = bool(normalized_comment.strip())
        task_key = str(tasks[task_index].get("external_id") or "").strip()
        if status_changed:
            available_statuses = await _available_jira_statuses_for_task(
                db=db,
                session=session,
                task_key=task_key,
            )
            if _provider_status_unavailable(
                requested_status=normalized_status,
                available_statuses=available_statuses,
            ):
                provider_outcome = ParseOutcome(
                    status=PARSE_STATUS_AMBIGUOUS,
                    reason_code="clarification_required",
                    confidence=clamp_confidence(0.2),
                    requires_clarification=True,
                    normalized_payload={
                        "status": normalized_status,
                        "comment": normalized_comment,
                        "source": str((outcome.normalized_payload or {}).get("source") or "unknown"),
                    },
                )
                updated_payload = _build_status_clarification_payload(
                    outcome=provider_outcome,
                    text=text,
                    task_index=task_index,
                    retry_count=retry_count + 1 if "retry_count" in payload else 0,
                    candidate_statuses=available_statuses,
                )
                await set_clarification_state(
                    db,
                    str(session.get("session_id") or ""),
                    status=_STATUS_CLARIFICATION_STATE,
                    payload=updated_payload,
                )
                _emit_cadence_parse_telemetry(
                    event="clarification_retry",
                    session_id=str(session.get("session_id") or ""),
                    reason_code="clarification_required",
                    parse_status=provider_outcome.status,
                    requires_clarification=True,
                    clarification_kind="status",
                    retry_count=int(updated_payload.get("retry_count") or 0),
                    source=str((provider_outcome.normalized_payload or {}).get("source") or "unknown"),
                )
                await _send_dm_text_best_effort(
                    slack_client=slack_client,
                    channel=channel,
                    text=_build_status_clarification_text(
                        outcome=provider_outcome,
                        candidate_statuses=available_statuses,
                    ),
                )
                return True
        _emit_cadence_parse_telemetry(
            event="clarification_resolved",
            session_id=str(session.get("session_id") or ""),
            reason_code=str(outcome.reason_code or "resolved"),
            parse_status=outcome.status,
            requires_clarification=False,
            clarification_kind="status",
            retry_count=retry_count,
            source=str((outcome.normalized_payload or {}).get("source") or "unknown"),
        )
        return await _advance_after_task_update(
            db,
            session=session,
            task=tasks[task_index],
            task_index=task_index,
            new_status=normalized_status,
            comment=normalized_comment,
            status_changed=status_changed,
            has_comment=has_comment,
        )

    max_retries = int(payload.get("max_retries") or _CLARIFICATION_MAX_RETRIES)
    if retry_count + 1 >= max_retries:
        await clear_clarification_state(db, str(session.get("session_id") or ""), next_status="awaiting_status")
        _emit_cadence_parse_telemetry(
            event="clarification_max_retry_fallback",
            session_id=str(session.get("session_id") or ""),
            reason_code=str(outcome.reason_code or "clarification_required"),
            parse_status=outcome.status,
            requires_clarification=True,
            clarification_kind="status",
            retry_count=retry_count + 1,
            source=str((outcome.normalized_payload or {}).get("source") or "unknown"),
        )
        await _send_dm_text_best_effort(
            slack_client=slack_client,
            channel=channel,
            text=(
                f"{_reason_message(outcome.reason_code or 'clarification_required')} "
                "I'll keep this task as `no change` and continue."
            ),
        )
        return await _advance_after_task_update(
            db,
            session=session,
            task=tasks[task_index],
            task_index=task_index,
            new_status="no_change",
            comment="",
            status_changed=False,
            has_comment=False,
        )

    updated_payload = _build_status_clarification_payload(
        outcome=outcome,
        text=text,
        task_index=task_index,
        retry_count=retry_count + 1,
        candidate_statuses=candidate_statuses,
    )
    await set_clarification_state(
        db,
        str(session.get("session_id") or ""),
        status=_STATUS_CLARIFICATION_STATE,
        payload=updated_payload,
    )
    _emit_cadence_parse_telemetry(
        event="clarification_retry",
        session_id=str(session.get("session_id") or ""),
        reason_code=str(outcome.reason_code or "clarification_required"),
        parse_status=outcome.status,
        requires_clarification=True,
        clarification_kind="status",
        retry_count=int(updated_payload.get("retry_count") or 0),
        source=str((outcome.normalized_payload or {}).get("source") or "unknown"),
    )
    await _send_dm_text_best_effort(
        slack_client=slack_client,
        channel=channel,
        text=_build_status_clarification_text(
            outcome=outcome,
            candidate_statuses=candidate_statuses,
        ),
    )
    return True


# ---------------------------------------------------------------------------
# Cadence agent system prompt builder
# ---------------------------------------------------------------------------

def _build_cadence_agent_system_prompt(
    *,
    session: Dict[str, Any],
    current_task: Dict[str, Any],
    task_index: int,
) -> str:
    """Build the system prompt for the Cadence SharedAgentRuntime LLM call.

    Injects session context, current task details, and tool usage guidelines so
    the LLM can process a free-text DM reply without hardcoded state branching.
    """
    identity = compose_identity(
        capability="cadence",
        legacy_identity="You are ProMarshal managing an active task check-in session.",
        legacy_overlay="",
    )
    scope_policy = load_capability_scope_policy("cadence")
    response_rules = compose_response_rules(capability="cadence", legacy_rules="")

    tasks = list(session.get("tasks") or [])
    total_tasks = len(tasks)
    session_status = str(session.get("status") or "").strip().lower()

    task_key = str(current_task.get("external_id") or "N/A").strip()
    task_title = str(current_task.get("title") or "").strip()
    task_status = str(current_task.get("status") or "unknown").strip()
    task_updated_status = str(current_task.get("updated_status") or "").strip()
    task_priority = str(current_task.get("priority") or "").strip()
    task_transitions = list(current_task.get("available_transitions") or [])
    transition_names = ", ".join(t["name"] for t in task_transitions if t.get("name"))

    remaining = total_tasks - task_index - 1

    session_section = f"""[Active Check-in Session]
session_status: {session_status}
current_task_index: {task_index}
total_tasks: {total_tasks}
remaining_tasks_after_current: {remaining}

[Current Task]
task_key: {task_key}
title: {task_title}
current_status: {task_status}
updated_status: {task_updated_status or "none"}
available_transitions: {transition_names or "unknown"}
priority: {task_priority}"""

    tool_guidance = """[Tool Usage Guidelines]

--- Session Scope Rule ---
You are inside an active check-in session. Your role is to collect status updates and comments for the current task.

ALWAYS IN SCOPE — process with tools, never redirect:
- Status updates for the current task (done, in progress, etc.)
- Any comment, note, reason, or context about the current task — including vague phrases like "on hold", "waiting", "stuck", "priority changed", "blocked by something"
- Anything the user says that relates to this specific task

OUT OF SCOPE — reply ONLY: "Let's finish the check-in first! Once we wrap up, I'll be happy to help with that."
- Requests to see other tasks or task lists
- Questions about the project, team, or analytics
- General ProMarshal feature requests unrelated to this task
- General knowledge or advice questions clearly not about the current task

You have access to these tools for this check-in session:

- cadence_update_task_status: Call when the user provides a status update. Pass the resolved status value and the user's original phrase.
- cadence_add_comment: Call when the user provides notes, context, or blockers for the task.
- cadence_advance_task: Call after recording status (and optionally comment) to move to the next task.
- cadence_ask_comment_followup: Call when the user gave a status but no comment and a note would be valuable.
- cadence_complete_session: Call only after all tasks are reviewed and the session is done.

--- Status Update Rule ---
Check `available_transitions` for the current task in the session context above.

If available_transitions lists specific values (e.g., "In Progress, Done, To Do"):
- Only call cadence_update_task_status when the user explicitly requests one of those EXACT transition names.
- Pass the transition name exactly as listed (case-sensitive) as the status argument.
- Anything not matching a listed transition → call cadence_add_comment instead.

If available_transitions is "unknown":
- Default to cadence_add_comment for all input. Do not attempt a status update.

In all cases:
- no_change (same, no change, no update, unchanged, leave it, still the same) → skip update entirely.
- If the user's phrase case-insensitively matches one of the available_transitions (e.g., user says "on hold" and "On Hold" is listed), treat it as a status update.
- Contextual phrases ("stuck", "waiting", "paused", "priority changed") that do NOT match any available_transition are comments, not status updates.

--- Processing Order ---
0. If updated_status is already set (not "none") → status already recorded. Skip cadence_update_task_status. Go to step 3.
1. Does the input explicitly name one of the listed available_transitions?
   - Yes → call cadence_update_task_status with status=<exact transition name> and user_phrase=<user's original words>.
   - No  → treat as comment, go to step 3.
2. If input is no_change → skip cadence_update_task_status entirely; go to step 3.
3. If the user provided context, reason, situation, or any note → call cadence_add_comment.
4. If no comment was given and a note would be useful → call cadence_ask_comment_followup (once per task only).
5. After recording updates → call cadence_advance_task to move to the next task.
6. When all tasks are done → call cadence_complete_session.

--- Handling Ambiguous Inputs ---
IMPORTANT: Never ask a yes/no confirmation question. Each DM is processed independently with no conversation history — the user's next reply will have no memory of your question.
If the input is truly unclassifiable (neither a transition name, no_change, nor a comment) → treat as no_change, advance the task, and tell the user: "Couldn't quite catch that — moving on. You can update this task anytime."

Do NOT call cadence_advance_task before at least one tool is called for the current task (status update, comment, or explicit no_change skip).
Do NOT complete the session mid-agenda.
Keep your response text brief and conversational — one or two sentences max."""

    parts = [
        identity.strip(),
        scope_policy.strip(),
        response_rules.strip(),
        session_section.strip(),
        tool_guidance.strip(),
    ]
    return "\n\n".join([part for part in parts if part])


# ---------------------------------------------------------------------------
# Main DM handler — called from integrations/router.py
# ---------------------------------------------------------------------------

async def handle_cadence_dm(
    db: AsyncIOMotorDatabase,
    *,
    session: Dict[str, Any],
    text: str,
    slack_client: Any,
    channel: str,
    actor_slack_user_id: Optional[str] = None,
) -> bool:
    """Process a DM as a Cadence check-in reply.

    Args:
        db: MongoDB database
        session: Active Cadence session document
        text: The user's message text
        slack_client: Slack client for sending responses
        channel: Slack DM channel ID

    Returns:
        True if handled successfully
    """
    session_id = session["session_id"]
    session_status = str(session.get("status") or "").strip().lower()

    # --- State routing for post-agenda states (overall update, backlog) ---
    # These deterministic flows must run before the LLM agent path.
    if session_status == "awaiting_overall_input":
        parsed = _parse_overall_update_input(text)
        await set_overall_input(
            db, session_id,
            text=str(parsed.get("text") or ""),
            skipped=bool(parsed.get("skipped")),
        )
        has_backlog = bool(session.get("has_backlog") and session.get("backlog_tasks"))
        if has_backlog:
            from app.cadence.interaction_handler import _send_backlog_nudge
            await _send_backlog_nudge(db, session_id)
            return True
        await complete_session(db, session_id, reason="agenda_complete")
        await send_session_summary(db, session_id)
        return True

    if session_status == "awaiting_backlog_decision":
        return await _handle_backlog_decision_state(
            db, session=session, text=text,
            slack_client=slack_client, channel=channel,
        )

    if session_status == "awaiting_backlog_clarification":
        return await _handle_backlog_clarification_state(
            db, session=session, text=text,
            slack_client=slack_client, channel=channel,
        )

    if session_status == "awaiting_backlog_pick":
        return await _handle_backlog_pick_state(
            db, session=session, text=text,
            slack_client=slack_client, channel=channel,
        )

    if session_status == "awaiting_status_clarification":
        return await _handle_status_clarification_state(
            db, session=session, text=text,
            slack_client=slack_client, channel=channel,
        )

    # --- SLICE 4: SharedAgentRuntime for awaiting_status state ---

    # Quick ack — sent immediately so the user sees feedback within ~100ms.
    import asyncio
    asyncio.create_task(
        send_ack_best_effort(slack_client, channel_id=channel, thread_ts=None, source="cadence")
    )

    # Build session context for the LLM.
    task_index = session.get("current_task_index", 0)
    tasks = list(session.get("tasks") or [])
    current_task = tasks[task_index] if task_index < len(tasks) else {}

    logger.info(
        "cadence_dm_agent session=%s task=%s task_index=%s",
        session_id,
        current_task.get("external_id", "N/A"),
        task_index,
    )
    active_lease = get_active_session_lease()
    lease_token = int(active_lease.lease_token) if active_lease is not None else None

    agent_context = AgentContext(
        user_message=text,
        project_id=str(session.get("project_id") or ""),
        user_id=str(session.get("user_id") or ""),
        session_key=session_id,
        source="cadence",
        feature_context={
            "session_id": session_id,
            "session_status": str(session.get("status") or ""),
            "current_task": current_task,
            "task_index": task_index,
            "total_tasks": len(tasks),
            "actor_slack_user_id": str(actor_slack_user_id or "").strip() or None,
            "lease_token": lease_token,
        },
    )

    system_prompt = _build_cadence_agent_system_prompt(
        session=session,
        current_task=current_task,
        task_index=task_index,
    )

    tool_descriptors = _build_cadence_tool_descriptors()

    result = await cadence_agent_runtime.run(
        context=agent_context,
        system_prompt=system_prompt,
        tools=tool_descriptors,
    )

    raw = result.raw_response or {}
    logger.info(
        "cadence_agent_run_complete session=%s task_index=%s tool_calls=%s tools=%s",
        session_id,
        task_index,
        raw.get("tool_calls_count", 0),
        raw.get("tool_names_called", []),
    )

    # NOTE: Do not send result.output_text here. The LLM's natural language reply
    # (e.g. "Got it, moving on!") duplicates the next-task card sent by post-run,
    # causing a triple-reply (ack + LLM text + next-task card). The ack already
    # provides immediate feedback; the post-run next-task card handles transition.
    # The LLM text is only sent when no follow-up message will be delivered (no_advance).
    agent_text = result.output_text

    # Post-run: check whether the agent advanced the session and send the appropriate
    # follow-up (next task card or individual session wrap-up). The agent runtime only
    # updates DB state via tool calls — the caller is responsible for presenting the
    # next step to the user.
    try:
        fresh = await get_session(db, session_id)
        if not fresh:
            # print(f"[cadence_post_run] session_missing session={session_id} task_index={task_index}")
            logger.warning(
                "cadence_post_run_session_missing session=%s task_index=%s",
                session_id,
                task_index,
            )
        else:
            new_index = int(fresh.get("current_task_index") or 0)
            new_status = str(fresh.get("status") or "")
            all_tasks = list(fresh.get("tasks") or [])

            # print(f"[cadence_post_run] session={session_id} task_index={task_index} new_index={new_index} new_status={new_status} total_tasks={len(all_tasks)}")

            if new_status == "completed":
                # Session was marked completed by cadence_advance_task calling
                # move_to_next_task for the last task. Route through overall
                # update prompt (blockers/risks) before generating summary.
                await set_overall_input_state(db, session_id)
                await send_overall_update_prompt(db, session_id)
            elif new_index > task_index:
                if new_index < len(all_tasks):
                    # print(f"[cadence_post_run] sending_next_task session={session_id} new_index={new_index}")
                    # Build previous_context from the just-completed task so
                    # the LLM generates a proper transition message (not "first task").
                    prev_task = all_tasks[task_index]
                    previous_context = {
                        "task_key": prev_task.get("external_id", "N/A"),
                        "title": prev_task.get("title", ""),
                        "new_status": prev_task.get("updated_status") or prev_task.get("status", ""),
                        "comment": prev_task.get("comment", ""),
                        "status_changed": bool(prev_task.get("updated_status")),
                        "has_comment": bool(prev_task.get("comment")),
                    }
                    await send_next_task_message(db, session_id, previous_context)
                else:
                    # Advanced past the last task — go to overall update prompt
                    # (blockers/risks) before session summary, matching the old flow.
                    await set_overall_input_state(db, session_id)
                    await send_overall_update_prompt(db, session_id)
            elif new_index == task_index and new_status == "awaiting_status":
                # Safety net: the LLM processed the user's input (status/comment)
                # but did not call cadence_advance_task. Auto-advance so the
                # session doesn't get stuck on the same task.
                # print(f"[cadence_post_run] auto_advance session={session_id} task_index={task_index}")
                logger.info(
                    "cadence_post_run_auto_advance session=%s task_index=%s",
                    session_id,
                    task_index,
                )
                has_more = await move_to_next_task(db, session_id)
                if has_more:
                    prev_task = all_tasks[task_index]
                    previous_context = {
                        "task_key": prev_task.get("external_id", "N/A"),
                        "title": prev_task.get("title", ""),
                        "new_status": prev_task.get("updated_status") or prev_task.get("status", ""),
                        "comment": prev_task.get("comment", ""),
                        "status_changed": bool(prev_task.get("updated_status")),
                        "has_comment": bool(prev_task.get("comment")),
                    }
                    await send_next_task_message(db, session_id, previous_context)
                else:
                    # Last task auto-advanced — go to overall update prompt.
                    await set_overall_input_state(db, session_id)
                    await send_overall_update_prompt(db, session_id)
            else:
                # No advance happened — send the LLM's text reply if any.
                if agent_text:
                    try:
                        await slack_client.chat_postMessage(channel=channel, text=agent_text)
                    except Exception as exc:
                        logger.error("cadence_agent_response_delivery_failed session=%s error=%s", session_id, exc)
                # print(f"[cadence_post_run] no_advance session={session_id} task_index={task_index} new_index={new_index}")
    except Exception as exc:
        logger.error("cadence_post_run_followup_failed session=%s error=%s", session_id, exc)

    return result.ok

    # ---------------------------------------------------------------------------
    # COMMENTED OUT — Slice 4 migration (2026-02-22)
    # Old state-routing if/elif block — replaced by AgentContext injection above.
    # Kept for reference during rollout. Remove in Slice 5 cleanup.
    # ---------------------------------------------------------------------------
    #
    # session_status = str(session.get("status") or "").strip().lower()
    # if session_status == _STATUS_CLARIFICATION_STATE:
    #     return await _handle_status_clarification_state(
    #         db, session=session, text=text, slack_client=slack_client, channel=channel,
    #     )
    # if session_status == _BACKLOG_CLARIFICATION_STATE:
    #     return await _handle_backlog_clarification_state(
    #         db, session=session, text=text, slack_client=slack_client, channel=channel,
    #     )
    # if session_status == "awaiting_backlog_decision":
    #     return await _handle_backlog_decision_state(
    #         db, session=session, text=text, slack_client=slack_client, channel=channel,
    #     )
    # if session_status == "awaiting_backlog_pick":
    #     return await _handle_backlog_pick_state(
    #         db, session=session, text=text, slack_client=slack_client, channel=channel,
    #     )
    # if session_status == "awaiting_overall_input":
    #     parsed = _parse_overall_update_input(text)
    #     await set_overall_input(db, session_id, text=str(parsed.get("text") or ""), skipped=bool(parsed.get("skipped")))
    #     has_backlog = bool(session.get("has_backlog") and session.get("backlog_tasks"))
    #     if has_backlog:
    #         from app.cadence.interaction_handler import _send_backlog_nudge
    #         await _send_backlog_nudge(db, session_id)
    #         return True
    #     await complete_session(db, session_id, reason="agenda_complete")
    #     await send_session_summary(db, session_id)
    #     return True
    #
    # ---------------------------------------------------------------------------
    # COMMENTED OUT — Old parse_with_runtime_adapter + enforce_mutation_gate chain.
    # Replaced by cadence_agent_runtime.run() above.
    # ---------------------------------------------------------------------------
    #
    # task_index = session.get("current_task_index", 0)
    # tasks = session.get("tasks", [])
    # if task_index >= len(tasks):
    #     logger.warning("cadence_session_exhausted session=%s task_index=%s", session_id, task_index)
    #     return False
    # task = tasks[task_index]
    # task_key = task.get("external_id", "N/A")
    # current_status = _normalize_cadence_status(task.get("status")) or "todo"
    # logger.info("cadence_dm_reply session=%s task=%s", session_id, task_key)
    # deterministic_snapshot = interpret_cadence_deterministic(
    #     text=text, provider="jira", capability_ids=["workitem_update_status", "workitem_add_comment"],
    # )
    # _ensure_runtime_parser_adapters_registered()
    # outcome = await parse_with_runtime_adapter(
    #     "cadence.status_update", text=text,
    #     parser_context={"session_id": session_id, "state": session_status or "awaiting_status"},
    # )
    # gate = enforce_mutation_gate(outcome)
    # ... (full parse chain removed — see git history)
