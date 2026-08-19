"""Pending interaction handling for work-item parent clarification flows."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.database import get_brain_collection
from app.core.pending_interactions import (
    create_pending_interaction,
    find_pending_interaction,
    resolve_pending_interaction,
    update_pending_interaction_context,
)
from app.llm import LLMGatewayRequest, LLMMessage, LLMMessageRole, get_shared_llm_gateway
from app.promarshal.prompt_registry import load_overlay, prompt_registry_enabled
from app.tasks.parent_reference_validator import (
    build_parent_reference_not_available_message,
    resolve_parent_reference,
)
from app.tasks.provider_parent_rules import (
    filter_candidates_by_parent_rule,
    resolve_parent_rule_filter,
)


_PENDING_TYPE = "work_item_parent_clarification"
_EXTERNAL_ID_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
_OPTION_RE = re.compile(r"^\s*(?:option\s*)?(\d{1,2})\s*$", re.IGNORECASE)
_YES_RE = re.compile(r"^\s*(?:yes|y|confirm|go ahead|proceed|use (?:first|1|one))\s*$", re.IGNORECASE)
_CANCEL_RE = re.compile(r"\b(?:cancel|stop|never mind|none)\b", re.IGNORECASE)
_SHOW_MORE_RE = re.compile(r"^\s*(?:show\s+more|more|next)\s*$", re.IGNORECASE)
_SHOW_PREVIOUS_RE = re.compile(r"^\s*(?:previous|prev|back)\s*$", re.IGNORECASE)
_SHOW_ALL_RE = re.compile(r"^\s*(?:show|list)\s+all\b", re.IGNORECASE)
_SHOW_TOP_RE = re.compile(r"^\s*(?:top|top\s*3|show\s+top)\s*$", re.IGNORECASE)
_ASSIGNEE_HINT_RE = re.compile(
    r"\b(?:assign(?:ed)?\s+(?:it\s+)?to|set\s+assignee\s+to)\s+([a-z0-9@._\-\s]{2,120})",
    re.IGNORECASE,
)
_NEW_INTENT_RE = re.compile(
    r"(?:^|\b)(?:create|add|new|list|show|update|assign|close|search|analyze|get)\b",
    re.IGNORECASE,
)
_TOP_CANDIDATE_COUNT = 3
_DEFAULT_PAGE_SIZE = 10
_MAX_CANDIDATE_POOL = 40
_MIN_PARENT_RELEVANCE_CONFIDENCE = 0.25
_GENERIC_PENDING_GUIDANCE_MESSAGE = (
    'I still need your confirmation to continue. Reply with a number or key, '
    'say "show more", or say "cancel".'
)
_NO_MORE_PARENT_OPTIONS_MESSAGE = (
    "I don't have more options in this direction.\n"
    'Try "previous", "list all", "top", or "cancel".'
)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _canonical_issue_type_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize_text(value).lower())


def _parent_rank_system_prompt() -> str:
    if not prompt_registry_enabled():
        return ""
    return load_overlay(
        "work_item_parent_resolution_rank",
        fallback="",
    )


def _normalize_candidate(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "external_id": _normalize_text(item.get("external_id")).upper(),
        "title": _normalize_text(item.get("title")),
        "description": _normalize_text(item.get("description")),
        "provider_type": _normalize_text(item.get("provider_type") or item.get("issue_type")),
        "work_item_type": _normalize_text(item.get("work_item_type")).lower() or None,
        "status": _normalize_text(item.get("status")).lower() or None,
        "updated_at": _normalize_text(item.get("updated_at")),
        "parent_external_id": (
            _normalize_text(item.get("parent_external_id") or item.get("parent_key")).upper() or None
        ),
        "reason": _normalize_text(item.get("reason")),
        "primary_tag": _normalize_text(item.get("primary_tag")),
        "confidence": float(item.get("confidence") or 0.0),
    }


def _is_parent_eligible(item: Dict[str, Any]) -> bool:
    work_item_type = _normalize_text(item.get("work_item_type")).lower()
    provider_type = _normalize_text(item.get("provider_type") or item.get("issue_type")).lower()
    if work_item_type == "subtask":
        return False
    if provider_type in {"sub-task", "subtask", "sub task"}:
        return False
    return bool(_normalize_text(item.get("external_id")))


def _get_view_candidates(
    *,
    candidate_pool: List[Dict[str, Any]],
    view_mode: str,
    view_offset: int,
    page_size: int,
) -> List[Dict[str, Any]]:
    if not candidate_pool:
        return []
    if view_mode == "all":
        return list(candidate_pool)
    if view_mode == "paged":
        start = max(0, int(view_offset or 0))
        end = start + max(1, int(page_size or _DEFAULT_PAGE_SIZE))
        return list(candidate_pool[start:end])
    return list(candidate_pool[:_TOP_CANDIDATE_COUNT])


def _view_display_start(*, view_mode: str, view_offset: int) -> int:
    if view_mode == "paged":
        return max(1, int(view_offset or 0) + 1)
    return 1


def _resolve_footer_prompt_line(*, view_mode: str, candidate_pool: List[Dict[str, Any]]) -> str:
    variants = [
        "Pick a number or key so I can proceed.",
        "Share the number or item key to continue.",
        "Tell me which one to use (number or key).",
        "Choose one with number/key and I'll continue.",
        "Send the number or work item key you prefer.",
    ]
    seed_text = "|".join(
        [
            _normalize_text(view_mode).lower(),
            str(len(candidate_pool)),
            ",".join(_normalize_text(item.get("external_id")).upper() for item in candidate_pool[:5]),
        ]
    )
    seed_value = sum(ord(char) for char in seed_text) if seed_text else 0
    return variants[seed_value % len(variants)]


def _title_case_status(value: str) -> str:
    token = _normalize_text(value).replace("_", " ")
    return token.title() if token else "Unknown"


def _resolve_child_item_label(create_payload: Dict[str, Any]) -> str:
    provider_type = _normalize_text(create_payload.get("provider_type"))
    work_item_type = _normalize_text(create_payload.get("work_item_type"))
    token = provider_type or work_item_type or "work item"
    normalized = token.replace("_", " ").replace("-", " ").strip()
    lowered = normalized.lower()
    if lowered == "sub task":
        return "subtask"
    return lowered or "work item"


def _build_candidate_message(
    *,
    candidate_pool: List[Dict[str, Any]],
    view_mode: str,
    view_offset: int,
    page_size: int,
    child_item_label: str = "work item",
) -> str:
    if not candidate_pool:
        return (
            "I can create this work item once you share the parent work item key.\n"
            "Reply with the exact parent work item key."
        )
    candidates = _get_view_candidates(
        candidate_pool=candidate_pool,
        view_mode=view_mode,
        view_offset=view_offset,
        page_size=page_size,
    )
    display_start = _view_display_start(view_mode=view_mode, view_offset=view_offset)
    total = len(candidate_pool)
    child_label = _normalize_text(child_item_label).lower() or "work item"
    if view_mode == "paged":
        header = f"More relevant parent options for this {child_label}:"
    elif view_mode == "all":
        header = f"All parent options for this {child_label}:"
    else:
        header = f"Here are the most relevant parent options for this {child_label}:"
    lines = [
        header,
    ]
    for idx, candidate in enumerate(candidates, start=display_start):
        ext = _normalize_text(candidate.get("external_id"))
        title = _normalize_text(candidate.get("title")) or "(untitled)"
        provider_type = _normalize_text(candidate.get("provider_type")) or "Work item"
        status = _title_case_status(_normalize_text(candidate.get("status")) or "unknown")
        primary_tag = _normalize_text(candidate.get("primary_tag"))
        line = f"{idx}. {ext} ({provider_type}, {status}) - {title}"
        if primary_tag and view_mode == "top3":
            line = f"{line} [{primary_tag}]"
        lines.append(line)
    lines.append("")
    lines.append(_resolve_footer_prompt_line(view_mode=view_mode, candidate_pool=candidate_pool))
    if view_mode == "top3":
        if total > _TOP_CANDIDATE_COUNT:
            lines.append('Need more options? Say "show more", "list all", or "cancel".')
        else:
            lines.append('You can say "list all" to see everything, or "cancel" to stop.')
    elif view_mode == "paged":
        guidance: List[str] = []
        if view_offset + len(candidates) < total:
            guidance.append('"show more"')
        if view_offset > 0:
            guidance.append('"previous"')
        guidance.extend(['"list all"', '"top"', '"cancel"'])
        lines.append(f'You can navigate with {", ".join(guidance[:-1])}, or {guidance[-1]}.')
    elif view_mode == "all" and total > _TOP_CANDIDATE_COUNT:
        lines.append('You can say "top" to jump back, or "cancel" to stop.')
    else:
        lines.append('Say "cancel" if you want to stop here.')
    return "\n".join(lines)


def _parse_json_payload(raw_text: str) -> Optional[dict[str, Any]]:
    payload: Optional[dict[str, Any]] = None
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                payload = None
    return payload


def _extract_explicit_key(text: str) -> Optional[str]:
    match = _EXTERNAL_ID_RE.search(_normalize_text(text).upper())
    if not match:
        return None
    return _normalize_text(match.group(1)).upper() or None


def _extract_requested_assignee_hint(text: str) -> Optional[str]:
    normalized = _normalize_text(text).lower()
    if not normalized:
        return None
    if re.search(r"\bassign(?:ed)?\s+(?:it\s+)?to\s+(?:me|myself)\b", normalized):
        return "me"
    if re.search(r"\bfor\s+me\b", normalized):
        return "me"
    match = _ASSIGNEE_HINT_RE.search(normalized)
    if not match:
        return None
    hint = _normalize_text(match.group(1))
    if not hint:
        return None
    # Trim trailing command words if present in noisy utterances.
    hint = re.split(r"\b(?:with|and|then|also)\b", hint, maxsplit=1)[0].strip()
    return hint or None


async def _rank_parent_candidates_with_llm(
    *,
    ranking_context: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not candidates:
        return []
    system_prompt = _parent_rank_system_prompt()
    if not system_prompt:
        return []
    gateway = get_shared_llm_gateway()
    model_name = str(
        getattr(settings, "work_item_parent_resolution_llm_model", "gpt-4o-mini")
        or "gpt-4o-mini"
    ).strip()
    timeout_seconds = max(
        1,
        int(
            getattr(
                settings,
                "work_item_parent_ranking_llm_timeout_seconds",
                getattr(settings, "work_item_parent_resolution_llm_timeout_seconds", 20),
            )
            or 20
        ),
    )
    user_payload = dict(ranking_context or {})
    response = await gateway.generate(
        LLMGatewayRequest(
            messages=[
                LLMMessage(role=LLMMessageRole.SYSTEM, content=system_prompt),
                LLMMessage(
                    role=LLMMessageRole.USER,
                    content=json.dumps(user_payload, ensure_ascii=True),
                ),
            ],
            model=model_name,
            retries=0,
            timeout_seconds=timeout_seconds,
            metadata={"feature": "work_item_parent_resolution.rank_candidates"},
        )
    )
    if not bool(response.ok):
        print(
            "work_item_parent_ranking_llm_failed: "
            f"error={_normalize_text(getattr(response, 'error', '') or '')} "
            f"text_preview={_normalize_text(response.text)[:240]}"
        )
        return []
    payload = _parse_json_payload(_normalize_text(response.text))
    ranked_items = payload.get("ranked") if isinstance(payload, dict) else None
    if not isinstance(ranked_items, list):
        print(
            "work_item_parent_ranking_parse_failed: "
            f"text_preview={_normalize_text(response.text)[:240]}"
        )
        return []

    by_external_id = {
        _normalize_text(c.get("external_id")).upper(): c for c in candidates
    }
    ranked: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in ranked_items:
        if not isinstance(row, dict):
            continue
        external_id = _normalize_text(row.get("external_id")).upper()
        if not external_id or external_id in seen:
            continue
        base = by_external_id.get(external_id)
        if not isinstance(base, dict):
            continue
        try:
            confidence = float(row.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        ranked.append(
            {
                **base,
                "reason": _normalize_text(row.get("reason")),
                "confidence": max(0.0, min(1.0, confidence)),
            }
        )
        seen.add(external_id)
        if len(ranked) >= _MAX_CANDIDATE_POOL:
            break
    return ranked


def _safe_parse_datetime(value: str) -> Optional[datetime]:
    text = _normalize_text(value)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _recency_bucket(updated_at_text: str) -> str:
    text = _normalize_text(updated_at_text)
    if not text:
        return "unknown"
    parsed = _safe_parse_datetime(text)
    if parsed is None:
        return "unknown"
    age = datetime.now(timezone.utc) - parsed
    days = max(0.0, float(age.total_seconds()) / 86400.0)
    if days <= 7:
        return "last_7_days"
    if days <= 30:
        return "last_30_days"
    return "older"


def _status_category(status_value: str) -> str:
    token = _normalize_text(status_value).lower()
    if not token:
        return "unknown"
    if token in {"done", "closed", "resolved", "completed"}:
        return "done"
    if token in {"in progress", "in_progress", "review", "in review", "testing", "blocked"}:
        return "active"
    if token in {"todo", "to do", "open", "backlog", "new"}:
        return "not_started"
    return "unknown"


def _is_closed_parent_status(status_value: str) -> bool:
    return _status_category(status_value) == "done"


def _title_tokens(value: str) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", _normalize_text(value).lower())
        if len(token) >= 3
    }
    return tokens


def _build_similar_mapping_context(
    *,
    request_title: str,
    request_description: str,
    child_label: str,
    all_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    request_tokens = _title_tokens(
        f"{_normalize_text(request_title)} {_normalize_text(request_description)}"
    )
    if not request_tokens:
        return {
            "top_similar_items": [],
            "parent_type_distribution": {},
            "recent_parent_type_distribution": {},
            "notes": "insufficient_request_tokens",
        }

    by_external_id = {
        _normalize_text(item.get("external_id")).upper(): item
        for item in all_items
        if _normalize_text(item.get("external_id"))
    }
    child_token = _canonical_issue_type_token(child_label)
    scored: List[Dict[str, Any]] = []
    for item in all_items:
        title = _normalize_text(item.get("title"))
        description = _normalize_text(item.get("description"))
        if not (title or description):
            continue
        item_type_token = _canonical_issue_type_token(item.get("provider_type") or item.get("work_item_type"))
        if child_token and item_type_token and item_type_token != child_token:
            continue
        overlap = request_tokens.intersection(_title_tokens(f"{title} {description}"))
        if not overlap:
            continue
        parent_external_id = _normalize_text(item.get("parent_external_id")).upper()
        parent_provider_type = ""
        if parent_external_id:
            parent_doc = by_external_id.get(parent_external_id) or {}
            parent_provider_type = _normalize_text(parent_doc.get("provider_type"))
        scored.append(
            {
                "external_id": _normalize_text(item.get("external_id")).upper(),
                "title": title,
                "description": description,
                "provider_type": _normalize_text(item.get("provider_type")),
                "status": _normalize_text(item.get("status")),
                "updated_at": _normalize_text(item.get("updated_at")),
                "parent_external_id": parent_external_id or None,
                "parent_provider_type": parent_provider_type or None,
                "overlap_score": float(len(overlap)) / float(max(1, len(request_tokens))),
            }
        )

    scored.sort(
        key=lambda row: (
            float(row.get("overlap_score") or 0.0),
            1.0 if _recency_bucket(_normalize_text(row.get("updated_at"))) == "last_7_days" else 0.0,
            1.0 if _recency_bucket(_normalize_text(row.get("updated_at"))) == "last_30_days" else 0.0,
        ),
        reverse=True,
    )
    top = scored[:8]
    parent_type_distribution: Dict[str, int] = {}
    recent_parent_type_distribution: Dict[str, int] = {}
    for row in top:
        parent_type = _normalize_text(row.get("parent_provider_type")) or "unknown"
        parent_type_distribution[parent_type] = int(parent_type_distribution.get(parent_type) or 0) + 1
        if _recency_bucket(_normalize_text(row.get("updated_at"))) in {"last_7_days", "last_30_days"}:
            recent_parent_type_distribution[parent_type] = int(
                recent_parent_type_distribution.get(parent_type) or 0
            ) + 1
    return {
        "top_similar_items": [
            {
                "external_id": _normalize_text(row.get("external_id")),
                "title": _normalize_text(row.get("title")),
                "description": _normalize_text(row.get("description")),
                "provider_type": _normalize_text(row.get("provider_type")),
                "status": _normalize_text(row.get("status")),
                "parent_external_id": _normalize_text(row.get("parent_external_id")) or None,
                "parent_provider_type": _normalize_text(row.get("parent_provider_type")) or None,
                "overlap_score": float(row.get("overlap_score") or 0.0),
            }
            for row in top
        ],
        "parent_type_distribution": parent_type_distribution,
        "recent_parent_type_distribution": recent_parent_type_distribution,
        "notes": "derived_from_project_brain_work_items",
    }


def _build_parent_ranking_context(
    *,
    request_text: str,
    create_payload: Dict[str, Any],
    project: Dict[str, Any],
    parent_rule_filter: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    all_items: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    status_counts: Dict[str, int] = {}
    type_counts: Dict[str, int] = {}
    for item in candidates:
        status = _normalize_text(item.get("status")).lower() or "unknown"
        status_counts[status] = int(status_counts.get(status) or 0) + 1
        provider_type = _normalize_text(item.get("provider_type")) or "work item"
        type_counts[provider_type] = int(type_counts.get(provider_type) or 0) + 1

    child_label = _resolve_child_item_label(create_payload)
    preferred_parent_provider_types: List[str] = []
    deprioritized_parent_provider_types: List[str] = []
    if _canonical_issue_type_token(child_label) == "subtask":
        preferred_parent_provider_types = ["Task", "Story"]
        deprioritized_parent_provider_types = ["Epic"]
    policy_source = _normalize_text((parent_rule_filter or {}).get("source")) or "unknown"
    policy_confidence = "high"
    if "fallback" in policy_source:
        policy_confidence = "medium"
    if policy_source == "unknown":
        policy_confidence = "low"

    return {
        "request_context": {
            "user_message": _normalize_text(request_text),
            "child_title": _normalize_text(create_payload.get("title")),
            "child_description": _normalize_text(create_payload.get("description")),
            "child_provider_type": _normalize_text(create_payload.get("provider_type")),
            "child_work_item_type": _normalize_text(create_payload.get("work_item_type")),
            "assignee_hint": _normalize_text(
                create_payload.get("requested_assignee_hint")
                or create_payload.get("assignee")
            ),
        },
        "project_context": {
            "project_id": _normalize_text(project.get("project_id")),
            "active_sprint_state": None,
            "execution_snapshot": {
                "status_counts": status_counts,
                "provider_type_counts": type_counts,
            },
        },
        "policy_context": {
            "provider": _normalize_text(create_payload.get("provider") or project.get("provider") or "workitem"),
            "parent_rule_filter": parent_rule_filter or {},
            "policy_source": policy_source,
            "policy_confidence": policy_confidence,
            "child_type_policy": {
                "child_type": child_label,
                "parent_required": bool((parent_rule_filter or {}).get("parent_required")),
                "allowed_parent_provider_types": list(
                    (parent_rule_filter or {}).get("allowed_parent_provider_types") or []
                ),
                "preferred_parent_provider_types": preferred_parent_provider_types,
                "deprioritized_parent_provider_types": deprioritized_parent_provider_types,
            },
        },
        "similar_mapping_context": _build_similar_mapping_context(
            request_title=_normalize_text(create_payload.get("title")),
            request_description=_normalize_text(create_payload.get("description")),
            child_label=child_label,
            all_items=list(all_items or []),
        ),
        "candidates": [
            {
                "external_id": _normalize_text(c.get("external_id")),
                "title": _normalize_text(c.get("title")),
                "description": _normalize_text(c.get("description")),
                "provider_type": _normalize_text(c.get("provider_type")),
                "work_item_type": _normalize_text(c.get("work_item_type")),
                "status": _normalize_text(c.get("status")),
                "status_category": _status_category(_normalize_text(c.get("status"))),
                "updated_at": _normalize_text(c.get("updated_at")),
                "recency_bucket": _recency_bucket(_normalize_text(c.get("updated_at"))),
                "is_active_like": _status_category(_normalize_text(c.get("status"))) == "active",
            }
            for c in candidates
        ],
    }


async def _classify_followup_with_llm(
    *,
    reply_text: str,
    candidates: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    gateway = get_shared_llm_gateway()
    model_name = str(
        getattr(settings, "work_item_parent_resolution_llm_model", "gpt-4o-mini")
        or "gpt-4o-mini"
    ).strip()
    timeout_seconds = max(
        1,
        int(getattr(settings, "work_item_parent_resolution_llm_timeout_seconds", 10) or 10),
    )
    system_prompt = (
        "Classify the follow-up response for parent work-item selection.\n"
        "Return STRICT JSON only with shape:\n"
        "{\"intent\":\"select|cancel|new_intent|unclear\","
        "\"selected_external_id\":\"\","
        "\"selected_option_index\":0,"
        "\"confidence\":0.0,"
        "\"reason\":\"...\"}"
    )
    user_payload = {
        "reply_text": _normalize_text(reply_text),
        "candidates": [
            {
                "index": idx,
                "external_id": _normalize_text(c.get("external_id")),
                "title": _normalize_text(c.get("title")),
            }
            for idx, c in enumerate(candidates[:3], start=1)
        ],
    }
    response = await gateway.generate(
        LLMGatewayRequest(
            messages=[
                LLMMessage(role=LLMMessageRole.SYSTEM, content=system_prompt),
                LLMMessage(
                    role=LLMMessageRole.USER,
                    content=json.dumps(user_payload, ensure_ascii=True),
                ),
            ],
            model=model_name,
            retries=0,
            timeout_seconds=timeout_seconds,
            metadata={"feature": "work_item_parent_resolution.classify_followup"},
        )
    )
    if not bool(response.ok):
        return None
    payload = _parse_json_payload(_normalize_text(response.text))
    if not isinstance(payload, dict):
        return None
    return payload


async def queue_work_item_parent_clarification(
    *,
    project: Dict[str, Any],
    actor_user_id: str,
    actor_email: str,
    user_message: str,
    create_payload: Dict[str, Any],
) -> Dict[str, Any]:
    project_id = _normalize_text(project.get("project_id"))
    normalized_actor = _normalize_text(actor_user_id)
    if not project_id or not normalized_actor:
        return {
            "queued": False,
            "response_text": (
                "I need the parent work item key before creating this work item. "
                "Reply with the exact parent work item key."
            ),
            "candidates": [],
        }

    existing = await find_pending_interaction(
        project_id=project_id,
        target_user_id=normalized_actor,
        interaction_type=_PENDING_TYPE,
    )
    requested_assignee_hint = _extract_requested_assignee_hint(user_message)
    current_request_fingerprint = {
        "provider": _normalize_text(create_payload.get("provider") or "jira").lower(),
        "title": _normalize_text(create_payload.get("title")).lower(),
        "provider_type": _canonical_issue_type_token(create_payload.get("provider_type")),
        "work_item_type": _canonical_issue_type_token(create_payload.get("work_item_type")),
        "requested_assignee_hint": _normalize_text(
            create_payload.get("requested_assignee_hint") or requested_assignee_hint
        ).lower(),
    }
    if isinstance(existing, dict):
        existing_context = existing.get("context_payload") if isinstance(existing.get("context_payload"), dict) else {}
        existing_create_payload = (
            existing_context.get("create_payload")
            if isinstance(existing_context.get("create_payload"), dict)
            else {}
        )
        existing_fingerprint = {
            "provider": _normalize_text(existing_create_payload.get("provider") or "jira").lower(),
            "title": _normalize_text(existing_create_payload.get("title")).lower(),
            "provider_type": _canonical_issue_type_token(existing_create_payload.get("provider_type")),
            "work_item_type": _canonical_issue_type_token(existing_create_payload.get("work_item_type")),
            "requested_assignee_hint": _normalize_text(
                existing_create_payload.get("requested_assignee_hint")
                or existing_context.get("requested_assignee_hint")
            ).lower(),
        }
        existing_pool = list(existing_context.get("candidate_pool") or [])
        if existing_pool and existing_fingerprint == current_request_fingerprint:
            existing_view_mode = _normalize_text(existing_context.get("view_mode")).lower() or "top3"
            existing_view_offset = int(existing_context.get("view_offset") or 0)
            existing_page_size = int(existing_context.get("view_page_size") or _DEFAULT_PAGE_SIZE)
            existing_child_item_label = (
                _normalize_text(existing_context.get("child_item_label")).lower() or "work item"
            )
            return {
                "queued": True,
                "interaction_id": _normalize_text(existing.get("interaction_id")),
                "candidates": _get_view_candidates(
                    candidate_pool=existing_pool,
                    view_mode=existing_view_mode,
                    view_offset=existing_view_offset,
                    page_size=existing_page_size,
                ),
                "response_text": _build_candidate_message(
                    candidate_pool=existing_pool,
                    view_mode=existing_view_mode,
                    view_offset=existing_view_offset,
                    page_size=existing_page_size,
                    child_item_label=existing_child_item_label,
                ),
            }

    collection = get_brain_collection(project_id)
    docs = (
        await collection.find(
            {
                "entity_type": "work_item",
                "$or": [{"provider": "jira"}, {"integration_type": "jira"}, {"source": "jira"}],
            },
            {
                "_id": 0,
                "external_id": 1,
                "title": 1,
                "description": 1,
                "provider_type": 1,
                "work_item_type": 1,
                "issue_type": 1,
                "status": 1,
                "updated_at": 1,
                "parent_external_id": 1,
                "parent_key": 1,
            },
        )
        .sort([("updated_at", -1)])
        .limit(80)
        .to_list(length=80)
    )
    normalized_docs = [_normalize_candidate(doc) for doc in docs if isinstance(doc, dict)]
    eligible = [doc for doc in normalized_docs if _is_parent_eligible(doc)]
    parent_rule_filter = await resolve_parent_rule_filter(
        project=project,
        create_payload=create_payload,
    )
    eligible = filter_candidates_by_parent_rule(
        candidates=eligible,
        parent_rule_filter=parent_rule_filter,
    )
    eligible_for_suggestion = [
        item for item in eligible if not _is_closed_parent_status(_normalize_text(item.get("status")))
    ]
    ranking_context = _build_parent_ranking_context(
        request_text=user_message,
        create_payload=create_payload,
        project=project,
        parent_rule_filter=parent_rule_filter,
        candidates=eligible_for_suggestion,
        all_items=normalized_docs,
    )
    llm_ranked = await _rank_parent_candidates_with_llm(
        ranking_context=ranking_context,
        candidates=eligible_for_suggestion,
    )
    if not llm_ranked:
        print(
            "work_item_parent_ranking_fallback_baseline: "
            f"project_id={project_id} actor={normalized_actor}"
        )
        llm_ranked = []
    else:
        print(
            "work_item_parent_ranking_llm_scores: "
            f"project_id={project_id} "
            f"title={_normalize_text(create_payload.get('title'))} "
            f"ranked={[(str(item.get('external_id') or ''), float(item.get('confidence') or 0.0), str(item.get('provider_type') or ''), str(item.get('reason') or '')) for item in llm_ranked]}"
        )
    candidate_pool = [
        item
        for item in llm_ranked
        if float(item.get("confidence") or 0.0) >= _MIN_PARENT_RELEVANCE_CONFIDENCE
    ][: _MAX_CANDIDATE_POOL]
    ranked_ids = {_normalize_text(item.get("external_id")).upper() for item in candidate_pool}
    if len(candidate_pool) < _MAX_CANDIDATE_POOL:
        for item in eligible_for_suggestion:
            ext = _normalize_text(item.get("external_id")).upper()
            if not ext or ext in ranked_ids:
                continue
            candidate_pool.append(item)
            ranked_ids.add(ext)
            if len(candidate_pool) >= _MAX_CANDIDATE_POOL:
                break
    if not candidate_pool:
        candidate_pool = eligible_for_suggestion[:_TOP_CANDIDATE_COUNT]

    print(
        "work_item_parent_ranking_selected_pool: "
        f"project_id={project_id} "
        f"title={_normalize_text(create_payload.get('title'))} "
        f"eligible={len(eligible)} "
        f"eligible_open={len(eligible_for_suggestion)} "
        f"llm_ranked={len(llm_ranked)} "
        f"selected_top3={[(str(item.get('external_id') or ''), float(item.get('confidence') or 0.0), str(item.get('provider_type') or ''), str(item.get('status') or '')) for item in candidate_pool[:_TOP_CANDIDATE_COUNT]]}"
    )

    if isinstance(existing, dict):
        prior_id = _normalize_text(existing.get("interaction_id"))
        if prior_id:
            await resolve_pending_interaction(interaction_id=prior_id, terminal_status="stale_target")

    child_item_label = _resolve_child_item_label(create_payload)
    pending_doc = await create_pending_interaction(
        interaction_type=_PENDING_TYPE,
        project_id=project_id,
        target_user_id=normalized_actor,
        source="cortex_jira_create_work_item",
        context_payload={
            "provider": "jira",
            "project_id": project_id,
            "create_payload": dict(create_payload or {}),
            "parent_rule_filter": parent_rule_filter,
            "actor_email": _normalize_text(actor_email),
            "candidate_pool": candidate_pool[:_MAX_CANDIDATE_POOL],
            "candidate_parents": candidate_pool[:_TOP_CANDIDATE_COUNT],
            "view_mode": "top3",
            "view_offset": 0,
            "view_page_size": _DEFAULT_PAGE_SIZE,
            "child_item_label": child_item_label,
            "requested_user_message": _normalize_text(user_message),
            "requested_assignee_hint": requested_assignee_hint,
            "request_fingerprint": current_request_fingerprint,
        },
    )
    return {
        "queued": True,
        "interaction_id": _normalize_text(pending_doc.get("interaction_id")),
        "candidates": candidate_pool[:_TOP_CANDIDATE_COUNT],
        "response_text": _build_candidate_message(
            candidate_pool=candidate_pool[:_MAX_CANDIDATE_POOL],
            view_mode="top3",
            view_offset=0,
            page_size=_DEFAULT_PAGE_SIZE,
            child_item_label=child_item_label,
        ),
    }


async def queue_subtask_parent_clarification(
    *,
    project: Dict[str, Any],
    actor_user_id: str,
    actor_email: str,
    user_message: str,
    create_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Backwards-compatible wrapper; prefer queue_work_item_parent_clarification."""
    return await queue_work_item_parent_clarification(
        project=project,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        user_message=user_message,
        create_payload=create_payload,
    )


def _parse_selection_from_reply(
    *,
    text: str,
    candidates: List[Dict[str, Any]],
    candidate_pool: Optional[List[Dict[str, Any]]] = None,
    option_index_start: int = 1,
) -> Dict[str, Any]:
    normalized = _normalize_text(text)
    if _SHOW_MORE_RE.match(normalized):
        return {"intent": "show_more"}
    if _SHOW_PREVIOUS_RE.match(normalized):
        return {"intent": "show_previous"}
    if _SHOW_ALL_RE.match(normalized):
        return {"intent": "show_all"}
    if _SHOW_TOP_RE.match(normalized):
        return {"intent": "show_top"}
    explicit_key = _extract_explicit_key(normalized)
    if explicit_key:
        return {"intent": "select", "parent_external_id": explicit_key}
    option_match = _OPTION_RE.match(normalized)
    if option_match:
        index = int(option_match.group(1))
        pool = list(candidate_pool or [])
        if 1 <= index <= len(pool):
            selected = _normalize_text(pool[index - 1].get("external_id")).upper()
            if selected:
                return {"intent": "select", "parent_external_id": selected}
        return {"intent": "invalid_option", "selected_option_index": index}
    if _YES_RE.match(normalized):
        selected = _normalize_text((candidates[0] if candidates else {}).get("external_id")).upper()
        if selected:
            return {"intent": "select", "parent_external_id": selected}
        return {"intent": "unclear"}
    if _CANCEL_RE.search(normalized):
        return {"intent": "cancel"}
    if _NEW_INTENT_RE.search(normalized):
        return {"intent": "new_intent"}
    return {"intent": "unclear"}


def _build_resume_message(
    *,
    create_payload: Dict[str, Any],
    parent_external_id: str,
    requested_user_message: str,
) -> str:
    title = _normalize_text(create_payload.get("title")) or "Untitled"
    provider_type = _normalize_text(create_payload.get("provider_type")) or "work item"
    priority = _normalize_text(create_payload.get("priority"))
    description = _normalize_text(create_payload.get("description"))
    requested_assignee_hint = (
        _normalize_text(create_payload.get("requested_assignee_hint"))
        or _extract_requested_assignee_hint(requested_user_message)
    )

    provider_fragment = provider_type.lower()
    if provider_fragment in {"sub task", "sub-task"}:
        provider_fragment = "subtask"
    base = f'create a {provider_fragment} "{title}" under {parent_external_id}'
    if requested_assignee_hint:
        base = f"{base} and assign to {requested_assignee_hint}"

    fragments = [base]
    if description:
        fragments.append(f"description: {description}")
    if priority:
        fragments.append(f"priority: {priority}")
    return ". ".join(fragments).strip()


async def handle_pending_work_item_parent_clarification(
    *,
    project: Dict[str, Any],
    actor_user_id: str,
    text: str,
) -> Dict[str, Any]:
    project_id = _normalize_text(project.get("project_id"))
    normalized_actor = _normalize_text(actor_user_id)
    if not project_id or not normalized_actor:
        return {"handled": False}

    pending = await find_pending_interaction(
        project_id=project_id,
        target_user_id=normalized_actor,
        interaction_type=_PENDING_TYPE,
    )
    if not isinstance(pending, dict):
        return {"handled": False}

    interaction_id = _normalize_text(pending.get("interaction_id"))
    context_payload = pending.get("context_payload") or {}
    create_payload = context_payload.get("create_payload") or {}
    candidate_pool = [
        _normalize_candidate(item)
        for item in (
            context_payload.get("candidate_pool")
            or context_payload.get("candidate_parents")
            or []
        )
        if isinstance(item, dict)
    ][:_MAX_CANDIDATE_POOL]
    view_mode = _normalize_text(context_payload.get("view_mode")).lower() or "top3"
    if view_mode not in {"top3", "paged", "all"}:
        view_mode = "top3"
    view_offset = max(0, int(context_payload.get("view_offset") or 0))
    page_size = max(1, int(context_payload.get("view_page_size") or _DEFAULT_PAGE_SIZE))
    child_item_label = _normalize_text(context_payload.get("child_item_label")) or _resolve_child_item_label(create_payload)
    candidates = _get_view_candidates(
        candidate_pool=candidate_pool,
        view_mode=view_mode,
        view_offset=view_offset,
        page_size=page_size,
    )
    option_index_start = _view_display_start(view_mode=view_mode, view_offset=view_offset)

    decision = _parse_selection_from_reply(
        text=text,
        candidates=candidates,
        candidate_pool=candidate_pool,
        option_index_start=option_index_start,
    )
    if decision.get("intent") == "invalid_option":
        try:
            invalid_idx = int(decision.get("selected_option_index") or 0)
        except Exception:
            invalid_idx = 0
        max_option = len(candidate_pool)
        response_text = (
            f"I couldn't find option {invalid_idx}. "
            f"Please choose a number between 1 and {max_option}, or reply with the exact work item key."
        )
        return {"handled": True, "response_text": response_text}

    if decision.get("intent") == "unclear":
        llm_decision = await _classify_followup_with_llm(reply_text=text, candidates=candidates)
        if isinstance(llm_decision, dict):
            llm_intent = _normalize_text(llm_decision.get("intent")).lower()
            if llm_intent in {"cancel", "new_intent"}:
                decision = {"intent": llm_intent}

    intent = _normalize_text(decision.get("intent")).lower()
    if intent == "new_intent":
        return {"handled": False}
    if intent == "cancel":
        if interaction_id:
            await resolve_pending_interaction(interaction_id=interaction_id, terminal_status="cancelled")
        return {"handled": True, "response_text": "Cancelled work-item creation."}
    if intent in {"show_more", "show_previous", "show_all", "show_top"}:
        if intent == "show_all":
            view_mode = "all"
            view_offset = 0
        elif intent == "show_top":
            view_mode = "top3"
            view_offset = 0
        elif intent == "show_previous":
            if view_mode == "top3":
                view_mode = "top3"
                view_offset = 0
            else:
                view_mode = "paged"
                step = page_size if view_mode == "paged" else _TOP_CANDIDATE_COUNT
                view_offset = max(0, view_offset - max(1, step))
        else:  # show_more
            if view_mode == "top3":
                if len(candidate_pool) <= _TOP_CANDIDATE_COUNT:
                    return {
                        "handled": True,
                        "response_text": _NO_MORE_PARENT_OPTIONS_MESSAGE,
                    }
                view_mode = "paged"
                view_offset = _TOP_CANDIDATE_COUNT
            elif view_mode == "paged":
                next_offset = view_offset + page_size
                if next_offset >= len(candidate_pool):
                    return {
                        "handled": True,
                        "response_text": _NO_MORE_PARENT_OPTIONS_MESSAGE,
                    }
                view_offset = next_offset
            else:
                view_mode = "all"
                view_offset = 0
        context_payload["view_mode"] = view_mode
        context_payload["view_offset"] = view_offset
        context_payload["view_page_size"] = page_size
        context_payload["candidate_pool"] = candidate_pool
        context_payload["candidate_parents"] = candidate_pool[:_TOP_CANDIDATE_COUNT]
        if interaction_id:
            await update_pending_interaction_context(
                interaction_id=interaction_id,
                context_payload=context_payload,
            )
        return {
            "handled": True,
            "response_text": _build_candidate_message(
                candidate_pool=candidate_pool,
                view_mode=view_mode,
                view_offset=view_offset,
                page_size=page_size,
                child_item_label=child_item_label,
            ),
        }

    parent_external_id = _normalize_text(decision.get("parent_external_id")).upper()
    if intent == "unclear":
        return {
            "handled": True,
            "response_text": _GENERIC_PENDING_GUIDANCE_MESSAGE,
        }
    if intent != "select" or not parent_external_id:
        return {
            "handled": True,
            "response_text": _build_candidate_message(
                candidate_pool=candidate_pool,
                view_mode=view_mode,
                view_offset=view_offset,
                page_size=page_size,
                child_item_label=child_item_label,
            ),
        }
    create_provider = _normalize_text(create_payload.get("provider")).lower()
    has_jira_integration = isinstance((project.get("integrations") or {}).get("jira"), dict)
    should_validate_parent = create_provider == "jira" or has_jira_integration
    if should_validate_parent:
        parent_resolution = await resolve_parent_reference(
            project=project,
            parent_external_id=parent_external_id,
        )
        if not bool((parent_resolution or {}).get("exists")):
            not_available = build_parent_reference_not_available_message(parent_external_id)
            guidance = _build_candidate_message(
                candidate_pool=candidate_pool,
                view_mode=view_mode,
                view_offset=view_offset,
                page_size=page_size,
                child_item_label=child_item_label,
            )
            return {
                "handled": True,
                "response_text": f"{not_available}\n\n{guidance}",
            }

    requested_user_message = _normalize_text(context_payload.get("requested_user_message"))
    requested_assignee_hint = _normalize_text(context_payload.get("requested_assignee_hint"))
    if requested_assignee_hint and not _normalize_text(create_payload.get("requested_assignee_hint")):
        create_payload["requested_assignee_hint"] = requested_assignee_hint
    if not _normalize_text(create_payload.get("requested_assignee_hint")) and requested_user_message:
        extracted_hint = _extract_requested_assignee_hint(requested_user_message)
        if extracted_hint:
            create_payload["requested_assignee_hint"] = extracted_hint
    create_payload["parent_external_id"] = parent_external_id
    resume_message = _build_resume_message(
        create_payload=create_payload,
        parent_external_id=parent_external_id,
        requested_user_message=requested_user_message,
    )
    if interaction_id:
        await resolve_pending_interaction(interaction_id=interaction_id, terminal_status="resolved")
    return {
        "handled": False,
        "resume_to_cortex": True,
        "resume_message_text": resume_message,
        "resume_target_refs": [parent_external_id],
    }
