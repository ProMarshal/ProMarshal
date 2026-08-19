"""Queue-backed Action Item extraction orchestration (v1 scaffold)."""

from __future__ import annotations

import hashlib
import logging
import json
import os
import re
from datetime import date, datetime
from typing import Any, Dict, Optional

from app.action_items.comment_mutation_event import (
    build_comment_mutation_event,
    normalize_comment_mutation_event,
    validate_comment_mutation_event,
)
from app.action_items.owner_intent import parse_owner_intent
from app.action_items.service import ActionItemService
from app.action_items.title_extractor import resolve_action_item_candidate
from app.core.config import settings
from app.core.identity_resolver import resolve_project_member_identity
from app.core.queue_backends.dramatiq_backend import enqueue as dramatiq_enqueue
from app.core.queue_runtime import (
    QUEUE_ACTION_ITEM_EXTRACTION,
    is_dramatiq_enabled_for_queue,
)
from app.llm import LLMGatewayRequest, LLMMessage, LLMMessageRole, get_shared_llm_gateway

logger = logging.getLogger(__name__)
_SYSTEM_PREFIX_RE = re.compile(
    r"^\s*\[Updated by [^\]]+\]\s*",
    re.IGNORECASE,
)
_STATUS_ONLY_RE = re.compile(
    r"^\s*(?:"
    r"done|completed|fixed(?:\s+in\s+[\w\s-]+)?|resolved|closed|reopened|"
    r"in\s*progress|wip|working on (?:it|this)|testing completed|tested|verified|"
    r"updated|update complete|status updated|"
    r"done from my side|completed from my end|needful done|deployed|"
    r"fyi\s+deployed|deployed\s+to\s+\w+"
    r")\s*[.!]*\s*$",
    re.IGNORECASE,
)
_EXPLICIT_EXTERNAL_ACTION_CUE_RE = re.compile(
    r"\b(?:ask\s+\w+\s+to|assign(?:ed)?\s+(?:to)?|owner(?:\s+is)?|"
    r"follow\s*up(?:\s+with)?|remind|coordinate(?:\s+with)?|handoff|"
    r"nudge|chase|check\s+with|loop\s+in)\b",
    re.IGNORECASE,
)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _json_safe_payload(value: Any) -> Any:
    """Convert queue payload to JSON-serializable primitives."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time()).isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_payload(item) for item in value]
    return value


def _strip_system_comment_prefix(text: str) -> str:
    return _SYSTEM_PREFIX_RE.sub("", _normalize_text(text))


def _looks_like_status_only_comment(text: str) -> bool:
    normalized = _strip_system_comment_prefix(text)
    if not normalized:
        return True
    return bool(_STATUS_ONLY_RE.match(normalized))


def _has_explicit_external_action_cue(text: str) -> bool:
    return bool(_EXPLICIT_EXTERNAL_ACTION_CUE_RE.search(_strip_system_comment_prefix(text)))


async def _llm_is_action_item_comment(text: str) -> Dict[str, Any]:
    """
    Classify if comment implies an external follow-up action item.
    Fail-closed: returns false on any error/invalid output.
    """
    gateway = get_shared_llm_gateway()
    if gateway is None:
        return {"is_action_item": False, "confidence": 0.0, "reason": "gateway_unavailable"}

    cleaned = _strip_system_comment_prefix(text)
    system_prompt = (
        "You classify whether a task comment should create a separate follow-up action item.\n"
        "Action item should be true ONLY when work is external follow-up/handoff/reminder"
        " that cannot be fully tracked by task status/comment alone.\n"
        "Return STRICT JSON with keys: is_action_item, confidence, reason.\n"
        "Rules:\n"
        "- is_action_item=false for status/progress notes (done/completed/fixed/testing/working/update).\n"
        "- is_action_item=true for explicit follow-up intent (ask X to, remind, follow up, assign owner, chase/nudge/check with).\n"
        "- Treat regional variants similarly: 'pls', 'kindly', 'done from my side', 'needful done'.\n"
        "- Multilingual direct follow-up requests should also be true (e.g., Spanish/Portuguese/Hindi/German requests to remind/follow up).\n"
        "- Distinguish task-trackable work vs external coordination. Task-trackable => false.\n"
        "- If ambiguous, return is_action_item=false.\n"
        "- confidence is [0,1]."
        "\nExamples:\n"
        "- 'completed' => false\n"
        "- 'done from my side' => false\n"
        "- 'kindly remind Rahul to share deck by EOD' => true\n"
        "- 'pls chase infra team for access' => true\n"
        "- 'por favor recuerda a juan enviar el reporte' => true\n"
        "- 'kripya rahul ko deck bhejne ke liye yaad dilao' => true\n"
        "- 'fixed and deployed' => false"
    )
    request = LLMGatewayRequest(
        messages=[
            LLMMessage(role=LLMMessageRole.SYSTEM, content=system_prompt),
            LLMMessage(role=LLMMessageRole.USER, content=cleaned),
        ],
        temperature=0.0,
        max_tokens=120,
        timeout_seconds=8,
        retries=0,
        metadata={"feature": "action_items_comment_classifier"},
    )

    result = await gateway.generate(request)
    if not bool(getattr(result, "ok", False)):
        return {"is_action_item": False, "confidence": 0.0, "reason": "classifier_failed"}

    raw_text = _normalize_text(getattr(result, "text", ""))
    payload: Optional[Dict[str, Any]] = None
    try:
        payload = json.loads(raw_text)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if match:
            try:
                payload = json.loads(match.group(0))
            except Exception:
                payload = None
    if not isinstance(payload, dict):
        return {"is_action_item": False, "confidence": 0.0, "reason": "invalid_classifier_output"}

    confidence = 0.0
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    is_action_item = bool(payload.get("is_action_item"))
    reason = _normalize_text(payload.get("reason"))
    return {"is_action_item": is_action_item, "confidence": confidence, "reason": reason or "ok"}


def _resolve_actor_user_id_from_project(project: Dict[str, Any], updated_by: Optional[str]) -> Optional[str]:
    normalized_updated_by = _normalize_text(updated_by).lower()
    if not normalized_updated_by:
        return None

    owner_user_id = _normalize_text(project.get("user_id"))
    if normalized_updated_by == owner_user_id.lower():
        return owner_user_id

    for member in project.get("members") or []:
        if not isinstance(member, dict):
            continue
        if _normalize_text(member.get("status")).lower() != "active":
            continue
        member_user_id = _normalize_text(member.get("user_id"))
        member_email = _normalize_text(member.get("email")).lower()
        if member_user_id and member_user_id.lower() == normalized_updated_by:
            return member_user_id
        if member_email and member_email == normalized_updated_by:
            return member_user_id or None
    return None


def _extract_explicit_owner_hint(text: str, candidate_owner_hint: Optional[str]) -> Optional[str]:
    parsed = parse_owner_intent(text, candidate_owner_hint)
    return _normalize_text(parsed.owner_hint) or None


def _title_with_task_context(title: str, task_key: str) -> str:
    normalized_title = _normalize_text(title)
    normalized_task_key = _normalize_text(task_key)
    if not normalized_title:
        normalized_title = "Follow up"
    if not normalized_task_key:
        return normalized_title
    if normalized_task_key.lower() in normalized_title.lower():
        return normalized_title
    return f"{normalized_title} ({normalized_task_key})"


def _enqueue_or_run(payload: Dict[str, Any]) -> Dict[str, Any]:
    safe_payload = _json_safe_payload(payload)
    enqueue_result = dramatiq_enqueue(
        queue_name=QUEUE_ACTION_ITEM_EXTRACTION,
        actor_name="run_action_item_extraction",
        kwargs={"payload": safe_payload},
    )
    if bool(enqueue_result.get("accepted")):
        job_id = str(enqueue_result.get("job_id") or "")
        logger.info(
            "action_item_extraction_enqueued_runtime=dramatiq source_type=%s project_id=%s task_key=%s actor_user_id=%s job_id=%s",
            _normalize_text(payload.get("source_type")),
            _normalize_text(payload.get("project_id")),
            _normalize_text(payload.get("task_key")),
            _normalize_text(payload.get("actor_user_id")),
            job_id or "none",
        )
        return {"queued": True, "job_id": job_id}

    logger.warning(
        "action_item_extraction_dramatiq_enqueue_failed source_type=%s project_id=%s task_key=%s reason=%s",
        _normalize_text(payload.get("source_type")),
        _normalize_text(payload.get("project_id")),
        _normalize_text(payload.get("task_key")),
        _normalize_text(enqueue_result.get("reason")),
    )

    # Queue-backed only; no synchronous fallback in request paths.
    logger.warning(
        "action_item_extraction_queue_unavailable source_type=%s project_id=%s task_key=%s actor_user_id=%s",
        _normalize_text(payload.get("source_type")),
        _normalize_text(payload.get("project_id")),
        _normalize_text(payload.get("task_key")),
        _normalize_text(payload.get("actor_user_id")),
    )
    return {"queued": False, "reason": "queue_unavailable"}


def enqueue_from_cadence_session_summary(
    *,
    project_id: str,
    session_id: str,
    actor_user_id: str,
    summary: Dict[str, Any],
    reason: str,
) -> Dict[str, Any]:
    payload = {
        "source_type": "cadence_session_summary",
        "project_id": _normalize_text(project_id),
        "session_id": _normalize_text(session_id),
        "actor_user_id": _normalize_text(actor_user_id),
        "reason": _normalize_text(reason),
        "summary": summary or {},
    }
    return _enqueue_or_run(payload)


def enqueue_from_task_comment_mutation(
    *,
    project_id: str,
    task_key: str,
    raw_comment_text: str = "",
    posted_comment_text: str,
    actor_user_id: Optional[str],
    updated_by_user_id: Optional[str] = None,
    provider: str = "jira",
    source: str = "task_service_mutate_task",
    source_event_key: Optional[str] = None,
    mutation_timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    comment_event = build_comment_mutation_event(
        project_id=_normalize_text(project_id),
        task_key=_normalize_text(task_key),
        comment_text=_normalize_text(posted_comment_text),
        actor_user_id=_normalize_text(actor_user_id),
        updated_by_user_id=_normalize_text(updated_by_user_id),
        provider=_normalize_text(provider) or "jira",
        mutation_timestamp=_normalize_text(mutation_timestamp),
        source=_normalize_text(source) or "task_service_mutate_task",
        source_event_key=_normalize_text(source_event_key),
    )
    payload = {
        "source_type": "task_comment_mutation",
        "project_id": _normalize_text(project_id),
        "task_key": _normalize_text(task_key),
        "raw_comment_text": _normalize_text(raw_comment_text),
        "posted_comment_text": _normalize_text(posted_comment_text),
        "actor_user_id": _normalize_text(actor_user_id),
        "mutation_timestamp": _normalize_text(mutation_timestamp),
        "comment_event": comment_event,
    }
    return _enqueue_or_run(payload)


async def process_action_item_extraction_job_async(payload: Dict[str, Any]) -> Dict[str, Any]:
    source_type = _normalize_text((payload or {}).get("source_type")).lower()
    project_id = _normalize_text((payload or {}).get("project_id"))
    actor_user_id = _normalize_text((payload or {}).get("actor_user_id"))
    if not source_type or not project_id:
        logger.warning(
            "action_item_extraction_invalid_payload source_type=%s project_id=%s payload_keys=%s",
            source_type,
            project_id,
            sorted(list((payload or {}).keys())),
        )
        return {
            "ok": False,
            "error": "invalid_payload",
            "retryable": False,
            "error_code": "invalid_payload",
        }

    try:
        from app.core.database import ensure_mongo_ready

        runtime_owner = (
            f"dramatiq_event_loop_thread:{os.getpid()}"
            if is_dramatiq_enabled_for_queue(QUEUE_ACTION_ITEM_EXTRACTION)
            else None
        )
        await ensure_mongo_ready(runtime_owner=runtime_owner)

        logger.info(
            "action_item_extraction_job_started source_type=%s project_id=%s task_key=%s actor_user_id=%s",
            source_type,
            project_id,
            _normalize_text((payload or {}).get("task_key")),
            actor_user_id,
        )
        if source_type == "cadence_session_summary":
            summary = (payload or {}).get("summary") or {}
            key_input = _normalize_text(summary.get("key_input"))
            overall_update = _normalize_text(summary.get("overall_update_input"))
            notable_comments = summary.get("notable_comments") or []
            first_notable = ""
            if isinstance(notable_comments, list) and notable_comments:
                first_item = notable_comments[0] if isinstance(notable_comments[0], dict) else {}
                first_notable = _normalize_text(first_item.get("comment"))
            # Prefer actor-authored overall update over synthesized key_input.
            raw_text = overall_update or key_input or first_notable
            if not raw_text:
                logger.info(
                    "action_item_extraction_skipped source_type=%s project_id=%s reason=no_extractable_text",
                    source_type,
                    project_id,
                )
                return {"ok": True, "created": False, "reason": "no_extractable_text"}
            normalized_text = _strip_system_comment_prefix(raw_text)
            if _looks_like_status_only_comment(normalized_text):
                logger.info(
                    "action_item_extraction_skipped source_type=%s project_id=%s reason=prefilter_status_only",
                    source_type,
                    project_id,
                )
                return {"ok": True, "created": False, "reason": "prefilter_status_only"}

            should_create = False
            eligibility_reason = "classifier_negative"
            confidence = 0.0
            if _has_explicit_external_action_cue(normalized_text):
                should_create = True
                eligibility_reason = "explicit_external_action_cue"
                confidence = 1.0
            else:
                classifier_enabled = bool(
                    getattr(settings, "action_items_comment_classifier_enabled", True)
                )
                if classifier_enabled:
                    decision = await _llm_is_action_item_comment(normalized_text)
                    min_confidence = max(
                        0.0,
                        min(
                            1.0,
                            float(
                                getattr(
                                    settings,
                                    "action_items_comment_classifier_min_confidence",
                                    0.75,
                                )
                                or 0.75
                            ),
                        ),
                    )
                    confidence = float(decision.get("confidence") or 0.0)
                    should_create = bool(decision.get("is_action_item")) and confidence >= min_confidence
                    eligibility_reason = _normalize_text(decision.get("reason")) or (
                        "classifier_positive" if should_create else "classifier_negative"
                    )
                else:
                    eligibility_reason = "classifier_disabled"

            if not should_create:
                logger.info(
                    "action_item_extraction_skipped source_type=%s project_id=%s reason=%s confidence=%.2f",
                    source_type,
                    project_id,
                    eligibility_reason,
                    confidence,
                )
                return {"ok": True, "created": False, "reason": eligibility_reason}

            candidate = await resolve_action_item_candidate(raw_text=raw_text)
            owner_hint = _extract_explicit_owner_hint(raw_text, candidate.owner_hint)
            resolved_owner_user_id: Optional[str] = None
            owner_resolution_status = "unassigned_default"
            if owner_hint:
                project_context = await ActionItemService._resolve_project_context(project_id)
                project_doc = (
                    project_context.get("project_doc")
                    if isinstance(project_context, dict) and isinstance(project_context.get("project_doc"), dict)
                    else {}
                )
                if isinstance(project_doc, dict) and project_doc:
                    owner_resolution = resolve_project_member_identity(
                        project_doc,
                        owner_hint,
                        actor_user_id=actor_user_id,
                    )
                    owner_resolution_status = owner_resolution.status
                    if owner_resolution.status == "resolved":
                        resolved_owner_user_id = _normalize_text(owner_resolution.promarshal_user_id) or None
            logger.info(
                "action_item_extraction_candidate source_type=%s project_id=%s owner_hint=%s owner_resolution_status=%s due_type=%s",
                source_type,
                project_id,
                _normalize_text(owner_hint),
                owner_resolution_status,
                _normalize_text(candidate.due_type),
            )
            stable_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()[:24]
            result = await (
                ActionItemService.create(
                    project_id,
                    {
                        "user_id": actor_user_id,
                        "title": candidate.title,
                        # Do not auto-assign cadence auto-detected items unless owner intent resolves explicitly.
                        "owner_user_id": resolved_owner_user_id,
                        "related_to": candidate.related_to
                        or f"Cadence session { _normalize_text((payload or {}).get('session_id')) }",
                        "related_type": "meeting",
                        "source": "cadence_auto",
                        "raw_text": raw_text,
                        "source_event_key": (
                            f"cadence:{_normalize_text((payload or {}).get('session_id'))}:{stable_hash}"
                        ),
                    },
                )
            )
            logger.info(
                "action_item_extraction_created source_type=%s project_id=%s action_item_id=%s display_key=%s needs_followup=%s",
                source_type,
                project_id,
                _normalize_text(result.get("action_item_id")),
                _normalize_text(result.get("display_key")),
                bool(result.get("needs_followup")),
            )
            return {"ok": True, "created": True, "action_item_id": result.get("action_item_id")}

        if source_type == "task_comment_mutation":
            comment_event = normalize_comment_mutation_event(
                (
                    (payload or {}).get("comment_event")
                    if isinstance((payload or {}).get("comment_event"), dict)
                    else payload
                )
            )
            validation_error = validate_comment_mutation_event(comment_event)
            if validation_error:
                logger.warning(
                    "action_item_extraction_skipped source_type=%s project_id=%s reason=invalid_comment_event validation_error=%s",
                    source_type,
                    project_id,
                    validation_error,
                )
                return {"ok": True, "created": False, "reason": "invalid_comment_event"}
            raw_comment_text = _normalize_text((payload or {}).get("raw_comment_text"))
            text = raw_comment_text or _normalize_text(comment_event.get("comment_text"))
            task_key = _normalize_text(comment_event.get("task_key"))
            mutation_timestamp = _normalize_text(comment_event.get("mutation_timestamp"))
            source_event_key = _normalize_text(comment_event.get("source_event_key"))
            event_actor_user_id = _normalize_text(comment_event.get("actor_user_id"))
            if _normalize_text(comment_event.get("project_id")):
                project_id = _normalize_text(comment_event.get("project_id"))
            if not text:
                logger.info(
                    "action_item_extraction_skipped source_type=%s project_id=%s task_key=%s reason=empty_comment",
                    source_type,
                    project_id,
                    task_key,
                )
                return {"ok": True, "created": False, "reason": "empty_comment"}
            if not event_actor_user_id:
                logger.warning(
                    "action_item_extraction_skipped source_type=%s project_id=%s task_key=%s reason=actor_unresolved",
                    source_type,
                    project_id,
                    task_key,
                )
                return {"ok": True, "created": False, "reason": "actor_unresolved"}

            normalized_text = _strip_system_comment_prefix(text)
            if _looks_like_status_only_comment(normalized_text):
                logger.info(
                    "action_item_extraction_skipped source_type=%s project_id=%s task_key=%s reason=prefilter_status_only",
                    source_type,
                    project_id,
                    task_key,
                )
                return {"ok": True, "created": False, "reason": "prefilter_status_only"}

            should_create = False
            eligibility_reason = "classifier_negative"
            confidence = 0.0
            if _has_explicit_external_action_cue(normalized_text):
                should_create = True
                eligibility_reason = "explicit_external_action_cue"
                confidence = 1.0
            else:
                classifier_enabled = bool(
                    getattr(settings, "action_items_comment_classifier_enabled", True)
                )
                if classifier_enabled:
                    decision = await _llm_is_action_item_comment(normalized_text)
                    min_confidence = max(
                        0.0,
                        min(
                            1.0,
                            float(
                                getattr(
                                    settings,
                                    "action_items_comment_classifier_min_confidence",
                                    0.75,
                                )
                                or 0.75
                            ),
                        ),
                    )
                    confidence = float(decision.get("confidence") or 0.0)
                    should_create = bool(decision.get("is_action_item")) and confidence >= min_confidence
                    eligibility_reason = _normalize_text(decision.get("reason")) or (
                        "classifier_positive" if should_create else "classifier_negative"
                    )
                else:
                    eligibility_reason = "classifier_disabled"

            if not should_create:
                logger.info(
                    "action_item_extraction_skipped source_type=%s project_id=%s task_key=%s reason=%s confidence=%.2f",
                    source_type,
                    project_id,
                    task_key,
                    eligibility_reason,
                    confidence,
                )
                return {"ok": True, "created": False, "reason": eligibility_reason}

            candidate = await resolve_action_item_candidate(raw_text=text)
            owner_hint = _extract_explicit_owner_hint(text, candidate.owner_hint)
            resolved_owner_user_id: Optional[str] = None
            owner_resolution_status = "not_attempted"
            owner_intent_detected = bool(owner_hint)
            owner_resolution = None
            if owner_hint:
                project_context = await ActionItemService._resolve_project_context(project_id)
                project_doc = (
                    project_context.get("project_doc")
                    if isinstance(project_context, dict) and isinstance(project_context.get("project_doc"), dict)
                    else {}
                )
            else:
                project_doc = {}
            if owner_hint and isinstance(project_doc, dict) and project_doc:
                owner_resolution = resolve_project_member_identity(
                    project_doc,
                    owner_hint,
                    actor_user_id=event_actor_user_id,
                )
                owner_resolution_status = owner_resolution.status
                if owner_resolution.status == "resolved":
                    resolved_owner_user_id = _normalize_text(owner_resolution.promarshal_user_id) or None
            else:
                # Auto-detected comment items default to unassigned unless owner resolves explicitly.
                owner_resolution_status = "unassigned_default"
            logger.info(
                "action_item_extraction_candidate source_type=%s provider=%s project_id=%s task_key=%s owner_intent=%s owner_hint=%s owner_resolution_status=%s due_type=%s",
                source_type,
                _normalize_text(comment_event.get("provider")) or "unknown",
                project_id,
                task_key,
                owner_intent_detected,
                _normalize_text(owner_hint),
                owner_resolution_status,
                _normalize_text(candidate.due_type),
            )
            stable_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
            dedupe_anchor = mutation_timestamp or "unknown"
            computed_source_event_key = source_event_key or f"comment:{task_key}:{stable_hash}:{dedupe_anchor}"
            try:
                result = await (
                    ActionItemService.create(
                        project_id,
                        {
                            "user_id": event_actor_user_id,
                            "title": _title_with_task_context(candidate.title, task_key),
                            "owner_user_id": resolved_owner_user_id,
                            "related_to": candidate.related_to or (f"Task {task_key}" if task_key else "Task comment update"),
                            "related_type": "work_item",
                            "related_id": task_key or None,
                            "source": "cortex_comment_auto",
                            "raw_text": text,
                            "source_event_key": computed_source_event_key,
                        },
                    )
                )
            except ValueError as value_error:
                if _normalize_text(value_error).lower() == "project not found":
                    logger.warning(
                        "action_item_extraction_skipped source_type=%s project_id=%s task_key=%s reason=project_not_found",
                        source_type,
                        project_id,
                        task_key,
                    )
                    return {"ok": True, "created": False, "reason": "project_not_found"}
                raise
            logger.info(
                "action_item_extraction_created source_type=%s project_id=%s task_key=%s action_item_id=%s display_key=%s needs_followup=%s owner_user_id=%s",
                source_type,
                project_id,
                task_key,
                _normalize_text(result.get("action_item_id")),
                _normalize_text(result.get("display_key")),
                bool(result.get("needs_followup")),
                _normalize_text(result.get("owner_user_id")),
            )
            return {"ok": True, "created": True, "action_item_id": result.get("action_item_id")}

        logger.info(
            "action_item_extraction_skipped source_type=%s project_id=%s reason=unsupported_source_type",
            source_type,
            project_id,
        )
        return {"ok": True, "created": False, "reason": "unsupported_source_type"}
    except Exception as exc:
        logger.exception(
            "action_item_extraction_failed source_type=%s project_id=%s task_key=%s error=%s",
            source_type,
            project_id,
            _normalize_text((payload or {}).get("task_key")),
            str(exc),
        )
        return {"ok": False, "error": str(exc)}


def process_action_item_extraction_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio

    return asyncio.run(process_action_item_extraction_job_async(payload))


def resolve_actor_user_id_from_project(project: Dict[str, Any], updated_by: Optional[str]) -> Optional[str]:
    """Public helper used by call sites to map comment actor to project member user_id."""
    return _resolve_actor_user_id_from_project(project, updated_by)
