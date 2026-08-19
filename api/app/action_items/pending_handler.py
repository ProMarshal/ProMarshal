"""Pending interaction handling for action-item clarification flows."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.action_items.owner_intent import parse_owner_intent
from app.action_items.service import ActionItemService
from app.core.database import get_brain_collection
from app.core.identity_resolver import (
    build_identity_followup_message,
    resolve_project_member_identity,
)
from app.core.config import settings
from app.core.pending_interactions import (
    find_pending_interaction,
    resolve_pending_interaction,
)
from app.llm import LLMGatewayRequest, LLMMessage, LLMMessageRole, get_shared_llm_gateway


logger = logging.getLogger(__name__)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _extract_owner_hint(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    parsed = parse_owner_intent(normalized)
    return _normalize_text(parsed.owner_hint)


_NEW_INTENT_RE = re.compile(
    r"(?:^|\b)(?:create|add|new|list|show|view|close|cancel|done|update)\b.*\b(?:action\s*item|task)\b",
    re.IGNORECASE,
)
_ASSIGNMENT_INTENT_RE = re.compile(
    r"(?:^|\b)(?:assign(?:\s+to)?|owner(?:\s+is)?|set\s+owner(?:\s+to)?|give\s+it\s+to|to\s+me|for\s+me|myself)\b",
    re.IGNORECASE,
)


def _looks_like_new_intent(text: str) -> bool:
    return bool(_NEW_INTENT_RE.search(_normalize_text(text)))


def _looks_like_assignment_intent(text: str) -> bool:
    return bool(_ASSIGNMENT_INTENT_RE.search(_normalize_text(text)))


def _build_member_roster(project: Dict[str, Any], reply_text: str, limit: int = 25) -> tuple[list[dict[str, str]], bool]:
    members = project.get("members") or []
    normalized_reply = _normalize_text(reply_text).lower()
    rows: list[tuple[float, str, dict[str, str]]] = []
    for member in members:
        if not isinstance(member, dict):
            continue
        status = _normalize_text(member.get("status")).lower()
        if status and status not in {"active", "invited"}:
            continue
        user_id = _normalize_text(member.get("user_id"))
        if not user_id:
            continue
        label = (
            _normalize_text(member.get("name"))
            or _normalize_text(member.get("email")).split("@")[0]
            or user_id
        )
        if not label:
            continue
        label_lower = label.lower()
        # Deterministic proximity score to prioritize likely owner mentions.
        score = 0.0
        if normalized_reply == label_lower:
            score += 10.0
        elif normalized_reply and label_lower.startswith(normalized_reply):
            score += 4.0
        if normalized_reply and normalized_reply in label_lower:
            score += 3.0
        if normalized_reply and label_lower in normalized_reply:
            score += 2.0
        rows.append((score, label_lower, {"user_id": user_id, "label": label}))

    rows.sort(key=lambda row: (-row[0], row[1]))
    truncated = len(rows) > max(1, int(limit))
    selected = [row[2] for row in rows[: max(1, int(limit))]]
    return selected, truncated


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


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes"}


async def _llm_classify_inconclusive_reply(
    *,
    project: Dict[str, Any],
    action_item_display_key: str,
    action_item_title: str,
    reply_text: str,
    assignment_intent_detected: bool,
    owner_hint_detected: bool,
    new_intent_detected: bool,
) -> Optional[dict[str, Any]]:
    if not bool(getattr(settings, "action_item_pending_relevance_enabled", True)):
        return None
    if not bool(getattr(settings, "action_item_pending_relevance_llm_fallback_enabled", True)):
        return None

    gateway = get_shared_llm_gateway()
    roster, roster_truncated = _build_member_roster(project, reply_text)
    system_prompt = (
        "Classify whether the user reply is intended to clarify the pending action item owner.\n"
        "Return STRICT JSON only with shape:\n"
        "{\"is_owner_clarification_reply\": true|false, \"likely_new_intent\": true|false, \"confidence\": number, \"reason\": string}\n"
        "Set likely_new_intent=true if the message is a separate command unrelated to resolving pending owner.\n"
    )
    context_payload = {
        "reply_text": _normalize_text(reply_text),
        "action_item_display_key": _normalize_text(action_item_display_key),
        "action_item_title": _normalize_text(action_item_title),
        "expected_field": "owner_user_id",
        "project_member_roster": roster,
        "roster_truncated": bool(roster_truncated),
        "deterministic_signals": {
            "assignment_intent_detected": bool(assignment_intent_detected),
            "owner_hint_detected": bool(owner_hint_detected),
            "new_intent_detected": bool(new_intent_detected),
        },
    }
    model_name = str(
        getattr(settings, "action_item_pending_relevance_llm_model", "gpt-4o-mini")
        or "gpt-4o-mini"
    ).strip()
    timeout_seconds = max(
        1,
        int(
            getattr(settings, "action_item_pending_relevance_llm_timeout_seconds", 8)
            or 8
        ),
    )
    request = LLMGatewayRequest(
        messages=[
            LLMMessage(role=LLMMessageRole.SYSTEM, content=system_prompt),
            LLMMessage(
                role=LLMMessageRole.USER,
                content=json.dumps(context_payload, ensure_ascii=True),
            ),
        ],
        model=model_name,
        retries=0,
        timeout_seconds=timeout_seconds,
        metadata={"feature": "pending_relevance.action_item.llm_fallback"},
    )
    response = await gateway.generate(request)
    if not bool(response.ok):
        logger.info("action_item_pending_relevance llm_unavailable")
        return None
    payload = _parse_json_payload(_normalize_text(response.text))
    if not isinstance(payload, dict):
        return None
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    return {
        "is_owner_clarification_reply": _to_bool(payload.get("is_owner_clarification_reply")),
        "likely_new_intent": _to_bool(payload.get("likely_new_intent")),
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": _normalize_text(payload.get("reason")) or "llm_classified",
    }


async def handle_pending_action_item_clarification(
    *,
    project: Dict[str, Any],
    actor_user_id: str,
    text: str,
) -> Dict[str, Any]:
    """
    Resolve pending action-item owner clarification for an actor+project context.

    Returns:
      {
        "handled": bool,
        "response_text": Optional[str],
      }
    """
    custom_project_id = _normalize_text(project.get("project_id"))
    normalized_actor = _normalize_text(actor_user_id)
    if not custom_project_id or not normalized_actor:
        return {"handled": False}

    pending = await find_pending_interaction(
        project_id=custom_project_id,
        target_user_id=normalized_actor,
        interaction_type="action_item_clarification",
    )
    if not isinstance(pending, dict):
        return {"handled": False}

    interaction_id = _normalize_text(pending.get("interaction_id"))
    context_payload = pending.get("context_payload") or {}
    action_item_id = _normalize_text(context_payload.get("action_item_id"))
    display_key = _normalize_text(context_payload.get("display_key")) or action_item_id or "action item"

    collection = get_brain_collection(custom_project_id)
    item = await collection.find_one(
        {"entity_type": "action_item", "action_item_id": action_item_id}
    )
    if not isinstance(item, dict):
        if interaction_id:
            await resolve_pending_interaction(
                interaction_id=interaction_id,
                terminal_status="stale_target",
            )
        return {
            "handled": True,
            "response_text": f"{display_key} is no longer available. I closed this pending clarification.",
        }

    status = _normalize_text(item.get("status")).lower()
    if status in {"done", "cancelled"}:
        if interaction_id:
            await resolve_pending_interaction(
                interaction_id=interaction_id,
                terminal_status="stale_target",
            )
        return {
            "handled": True,
            "response_text": f"{display_key} is already {status}. Clarification is no longer needed.",
        }

    if _looks_like_new_intent(text):
        return {"handled": False}
    assignment_intent_detected = _looks_like_assignment_intent(text)
    owner_hint = _extract_owner_hint(text) if assignment_intent_detected else ""
    if not assignment_intent_detected:
        llm_eval = await _llm_classify_inconclusive_reply(
            project=project,
            action_item_display_key=display_key,
            action_item_title=_normalize_text(item.get("title")),
            reply_text=text,
            assignment_intent_detected=False,
            owner_hint_detected=False,
            new_intent_detected=False,
        )
        if llm_eval is None:
            # Fail-open when classifier is unavailable/disabled.
            return {"handled": False}
        if bool(llm_eval.get("likely_new_intent")):
            return {"handled": False}
        min_conf = float(
            getattr(settings, "action_item_pending_relevance_llm_min_confidence", 0.65) or 0.65
        )
        if bool(llm_eval.get("is_owner_clarification_reply")) and float(llm_eval.get("confidence") or 0.0) >= min_conf:
            owner_hint = _normalize_text(text)
        else:
            return {
                "handled": True,
                "response_text": (
                    f"I still need the owner for {display_key}. "
                    "Please reply with a member name, email, or `owner:<user_id>`."
                ),
            }
    if not owner_hint:
        project_name = _normalize_text(project.get("project_name")) or custom_project_id
        return {
            "handled": True,
            "response_text": (
                f"Please reply with the owner for {display_key}.\n"
                f"Project: {project_name} ({custom_project_id})\n"
                "Example: `assign to prabhu` or `owner:u-123`"
            ),
        }

    owner_resolution = resolve_project_member_identity(
        project,
        owner_hint,
        actor_user_id=normalized_actor,
        actor_aliases_enabled=True,
    )
    resolved_owner = owner_resolution.promarshal_user_id
    if owner_resolution.status != "resolved" or not resolved_owner:
        return {
            "handled": True,
            "response_text": build_identity_followup_message(
                owner_resolution,
                subject="owner",
                project=project,
            ),
        }

    updated = await ActionItemService.update(
        custom_project_id,
        action_item_id,
        {
            "user_id": normalized_actor,
            "owner_user_id": resolved_owner,
            "needs_followup": False,
        },
    )
    await collection.update_one(
        {"entity_type": "action_item", "action_item_id": action_item_id},
        {
            "$set": {
                "clarification_id": interaction_id or None,
                "resolved_by_user_id": normalized_actor,
                "resolution_channel": "slack_dm",
                "clarification_resolved_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    if interaction_id:
        await resolve_pending_interaction(
            interaction_id=interaction_id,
            terminal_status="resolved",
        )

    key = _normalize_text(updated.get("display_key")) or display_key
    return {
        "handled": True,
        "response_text": f"Assigned {key} to {resolved_owner} and cleared follow-up.",
    }
