"""Shared provider-agnostic ingress gate for owner-triggered team poll actions."""

from __future__ import annotations

from typing import Any, Dict

from app.core.config import settings
from app.team_poll.orchestrator import (
    classify_owner_free_text_intent_with_confidence,
    close_owner_poll,
    create_team_poll,
    get_owner_poll_status,
)


async def handle_owner_poll_ingress(
    db,
    *,
    intent_text: str,
    project: Dict[str, Any],
    owner_member: Dict[str, Any],
    workspace_id: str,
) -> Dict[str, Any]:
    """
    Evaluate whether owner message should be handled by team-poll path.

    Returns:
      - handled: bool (message belongs to poll workflow)
      - terminal: bool (provider must stop further routing when handled)
      - kind: create|status|close|clarification|error
      - payload: dict result for response composition
    """
    if bool(getattr(settings, "team_poll_owner_free_text_via_cortex", True)):
        return {"handled": False, "terminal": False}

    owner_intent_payload = classify_owner_free_text_intent_with_confidence(intent_text)
    owner_intent = str(owner_intent_payload.get("intent") or "none")
    owner_confidence = float(owner_intent_payload.get("confidence") or 0.0)

    if owner_intent == "none":
        return {"handled": False, "terminal": False}

    if owner_confidence < 0.7:
        return {
            "handled": True,
            "terminal": True,
            "kind": "clarification",
            "payload": {
                "ok": False,
                "reason": "low_confidence_owner_intent",
                "confidence": owner_confidence,
                "intent": owner_intent,
            },
        }

    owner_user_id = (
        str(owner_member.get("user_id") or "").strip()
        or str(owner_member.get("email") or "").strip().lower()
    )
    project_id = str(project.get("project_id") or "").strip()

    try:
        if owner_intent == "create":
            result = await create_team_poll(
                db,
                project=project or {},
                owner_member=owner_member or {},
                question_text=intent_text,
                workspace_id=str(workspace_id or ""),
            )
            return {
                "handled": True,
                "terminal": True,
                "kind": "create",
                "project_id": project_id,
                "owner_user_id": owner_user_id,
                "payload": result,
            }

        if owner_intent == "status":
            result = await get_owner_poll_status(
                db,
                project=project or {},
                owner_user_id=owner_user_id,
                poll_id=None,
            )
            return {
                "handled": True,
                "terminal": True,
                "kind": "status",
                "project_id": project_id,
                "owner_user_id": owner_user_id,
                "payload": result,
            }

        if owner_intent == "close":
            result = await close_owner_poll(
                db,
                project=project or {},
                owner_user_id=owner_user_id,
                poll_id=None,
            )
            return {
                "handled": True,
                "terminal": True,
                "kind": "close",
                "project_id": project_id,
                "owner_user_id": owner_user_id,
                "payload": result,
            }

        return {"handled": False, "terminal": False}
    except Exception as exc:
        return {
            "handled": True,
            "terminal": True,
            "kind": "error",
            "project_id": project_id,
            "owner_user_id": owner_user_id,
            "payload": {
                "ok": False,
                "reason": "owner_poll_trigger_error",
                "error": str(exc),
                "intent": owner_intent,
            },
        }
