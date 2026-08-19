"""Pending interaction handling for Jira provider-type clarification flows."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.core.pending_interactions import find_pending_interaction, resolve_pending_interaction


_PENDING_TYPE = "provider_type_clarification"
_YES_RE = re.compile(r"^\s*(?:yes|y|confirm|go ahead|proceed|use (?:recommended|suggested))\s*$", re.IGNORECASE)
_CANCEL_RE = re.compile(r"\b(?:cancel|stop|never mind|nevermind)\b", re.IGNORECASE)
_NEW_INTENT_RE = re.compile(
    r"(?:^|\b)(?:create|add|new|list|show|update|assign|close|search|analyze|get|help)\b",
    re.IGNORECASE,
)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _canonical_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize_text(value).lower())


def _build_guidance(
    *,
    requested_value: Optional[str],
    allowed_values: List[str],
    suggested_value: Optional[str],
) -> str:
    requested = _normalize_text(requested_value)
    suggestion = _normalize_text(suggested_value)
    if suggestion:
        requested_label = requested or "That type"
        return (
            f"`{requested_label}` isn't allowed. Use `{suggestion}` instead? "
            "Reply `yes`, the type name, or `cancel`."
        )
    allowed_line = ", ".join([item for item in allowed_values if item]) or "an allowed type"
    return f"That type isn't allowed. Reply with one of: {allowed_line}, or `cancel`."


def _select_provider_type(
    *,
    text: str,
    allowed_values: List[str],
    suggested_value: Optional[str],
) -> Optional[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return None
    if _YES_RE.match(normalized):
        suggested = _normalize_text(suggested_value)
        if suggested:
            for item in allowed_values:
                if _canonical_token(item) == _canonical_token(suggested):
                    return item
    token = _canonical_token(normalized)
    if not token:
        return None
    for item in allowed_values:
        if _canonical_token(item) == token:
            return item
    return None


def _build_resume_message(*, selected_type: str, args: Dict[str, Any]) -> str:
    title = _normalize_text(args.get("title")) or "Untitled"
    assignee = _normalize_text(args.get("assignee"))
    description = _normalize_text(args.get("description"))
    priority = _normalize_text(args.get("priority"))
    parent_external_id = _normalize_text(args.get("parent_external_id")).upper()
    sprint_directive = _normalize_text(args.get("sprint_directive"))

    fragments: List[str] = [f'create a {selected_type} "{title}"']
    if parent_external_id:
        fragments.append(f"under {parent_external_id}")
    if assignee:
        fragments.append(f"and assign to {assignee}")
    sentence = " ".join(fragments).strip()

    trailing: List[str] = []
    if description:
        trailing.append(f"description: {description}")
    if priority and priority.lower() != "medium":
        trailing.append(f"priority: {priority}")
    if sprint_directive:
        trailing.append(f"sprint: {sprint_directive}")

    if trailing:
        sentence = f"{sentence}. " + ". ".join(trailing)
    return sentence.strip()


async def handle_pending_provider_type_clarification(
    *,
    project: Dict[str, Any],
    actor_user_id: str,
    text: str,
) -> Dict[str, Any]:
    project_id = _normalize_text(project.get("project_id"))
    target_user_id = _normalize_text(actor_user_id)
    if not project_id or not target_user_id:
        return {"handled": False}

    pending = await find_pending_interaction(
        project_id=project_id,
        target_user_id=target_user_id,
        interaction_type=_PENDING_TYPE,
    )
    if not isinstance(pending, dict):
        return {"handled": False}

    interaction_id = _normalize_text(pending.get("interaction_id"))
    context = pending.get("context_payload") if isinstance(pending.get("context_payload"), dict) else {}
    raw_args = context.get("args") if isinstance(context.get("args"), dict) else {}
    args = dict(raw_args)
    requested_value = _normalize_text(context.get("requested_value")) or None
    allowed_values = [
        _normalize_text(item)
        for item in (context.get("allowed_values") or [])
        if _normalize_text(item)
    ]
    suggested_value = _normalize_text(context.get("suggested_value")) or None
    guidance_text = _normalize_text(context.get("guidance_text"))

    normalized_text = _normalize_text(text)
    if not normalized_text:
        return {
            "handled": True,
            "response_text": guidance_text
            or _build_guidance(
                requested_value=requested_value,
                allowed_values=allowed_values,
                suggested_value=suggested_value,
            ),
        }

    if _CANCEL_RE.search(normalized_text):
        if interaction_id:
            await resolve_pending_interaction(interaction_id=interaction_id, terminal_status="cancelled")
        return {
            "handled": True,
            "response_text": "Cancelled. I won't create this work item.",
        }

    selected = _select_provider_type(
        text=normalized_text,
        allowed_values=allowed_values,
        suggested_value=suggested_value,
    )
    if selected:
        args["provider_type"] = selected
        if interaction_id:
            await resolve_pending_interaction(interaction_id=interaction_id, terminal_status="resolved")
        parent_external_id = _normalize_text(args.get("parent_external_id")).upper()
        target_refs = [parent_external_id] if parent_external_id else []
        return {
            "handled": False,
            "resume_to_cortex": True,
            "resume_message_text": _build_resume_message(selected_type=selected, args=args),
            "resume_target_refs": target_refs,
            "route": "provider_type_resume_to_cortex",
        }

    if _NEW_INTENT_RE.search(normalized_text):
        if interaction_id:
            await resolve_pending_interaction(interaction_id=interaction_id, terminal_status="stale_target")
        return {"handled": False}

    return {
        "handled": True,
        "response_text": guidance_text
        or _build_guidance(
            requested_value=requested_value,
            allowed_values=allowed_values,
            suggested_value=suggested_value,
        ),
    }

