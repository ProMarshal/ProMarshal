"""Team poll response writer using shared ProMarshal response composer."""

from __future__ import annotations

from typing import Any, Dict

from app.core.config import settings
from app.promarshal.response_composer import compose_user_response


async def compose_team_poll_response(
    *,
    action: str,
    user_message: str,
    outcome_payload: Dict[str, Any],
    fallback_text: str,
) -> str:
    normalized_action = str(action or "").strip().lower()
    if normalized_action == "owner_skip_success":
        return "Your response was skipped for this poll."

    if not bool(getattr(settings, "team_poll_llm_response_enabled", True)):
        return str(fallback_text or "").strip()

    payload = dict(outcome_payload or {})
    payload["action"] = normalized_action
    text = await compose_user_response(
        capability="team_poll",
        user_message=str(user_message or "").strip(),
        outcome_payload=payload,
        fallback_text=str(fallback_text or "").strip(),
        max_tokens=220,
        temperature=0.2,
    )
    return str(text or fallback_text or "").strip()
