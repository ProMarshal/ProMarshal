"""Meeting transcript → structured "discussed points" extraction.

Uses the shared LLM gateway (LiteLLM → OpenAI by default) with JSON mode to
turn a raw meeting transcript into a categorized list of action items,
decisions, blockers, discussion topics, and open questions for display in
the Meeting Insights dashboard tab.

Designed to fail soft — returns an empty list if anything goes wrong so the
ingest endpoint can still persist the transcript.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.llm.gateway import LLMGatewayRequest, LLMMessage, LLMMessageRole
from app.llm.provider import get_shared_llm_gateway

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You analyze meeting transcripts and extract structured "discussed points".

Return JSON in this exact shape:
{
  "points": [
    {"type": "action", "text": "...", "owner": "...", "due": "..."},
    {"type": "decision", "text": "..."},
    {"type": "blocker", "text": "..."},
    {"type": "discussion", "text": "..."},
    {"type": "question", "text": "..."}
  ]
}

Rules:
- type MUST be one of: "action", "decision", "blocker", "discussion", "question"
- "action" = a task assigned to someone (a thing to do after the meeting)
- "decision" = a choice that was finalized in the meeting
- "blocker" = something stopping progress (technical, organizational, dependency)
- "discussion" = a topic that was talked about substantively but produced no specific action or decision
- "question" = an open question raised but not answered in the meeting
- "text" is a concise, self-contained one-sentence summary of the point (max ~120 chars)
- "owner" applies ONLY to actions. Use the speaker's name verbatim from the transcript. Omit if no clear owner.
- "due" applies ONLY to actions. Use the deadline phrasing as it appeared (e.g. "Friday", "next sprint", "by Q3"). Omit if not mentioned.
- Aim for 4-12 points per meeting. Skip small talk, greetings, and tangents.
- Order points roughly by their order of appearance in the transcript.
- If the transcript is too short or contains no substantive content, return {"points": []}.
"""


async def extract_discussed_points(
    transcript: str,
    speaker_timeline: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Extract structured discussed points from a meeting transcript.

    Returns a list of dicts shaped like:
        {"type": "...", "text": "...", "owner": "...", "due": "..."}

    Returns an empty list if the transcript is empty, the LLM call fails,
    or the response is malformed.
    """
    text = (transcript or "").strip()
    if not text:
        return []

    attendee_hint = ""
    if speaker_timeline:
        names = sorted({str(ev.get("name", "")).strip() for ev in speaker_timeline if ev.get("name")})
        if names:
            attendee_hint = f"\n\nAttendees in this meeting: {', '.join(names)}."

    user_prompt = (
        f"Transcript:\n\n{text[:20000]}"  # cap to keep tokens reasonable for very long meetings
        f"{attendee_hint}\n\n"
        "Return the JSON described in the system prompt."
    )

    model = str(settings.openai_model or "gpt-4o-mini").strip() or "gpt-4o-mini"

    try:
        gateway = get_shared_llm_gateway()
        request = LLMGatewayRequest(
            messages=[
                LLMMessage(role=LLMMessageRole.SYSTEM, content=_SYSTEM_PROMPT),
                LLMMessage(role=LLMMessageRole.USER, content=user_prompt),
            ],
            model=model,
            temperature=0.2,
            max_tokens=2048,
            timeout_seconds=30,
            retries=1,
            response_format={"type": "json_object"},
            metadata={"feature": "meeting_insights_extractor"},
        )
        response = await gateway.generate(request)
    except Exception as exc:
        logger.warning("[meetings/extractor] gateway call raised: %s", exc)
        return []

    if not response.ok:
        logger.warning(
            "[meetings/extractor] gateway returned not-ok | error=%s",
            getattr(response, "error", None),
        )
        return []

    raw = (response.text or "").strip()
    if not raw:
        return []

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("[meetings/extractor] JSON parse failed: %s | raw=%r", exc, raw[:200])
        return []

    points = payload.get("points") if isinstance(payload, dict) else None
    if not isinstance(points, list):
        return []

    cleaned: List[Dict[str, Any]] = []
    allowed_types = {"action", "decision", "blocker", "discussion", "question"}
    for item in points:
        if not isinstance(item, dict):
            continue
        ptype = str(item.get("type", "")).strip().lower()
        ptext = str(item.get("text", "")).strip()
        if ptype not in allowed_types or not ptext:
            continue
        entry: Dict[str, Any] = {"type": ptype, "text": ptext}
        owner = str(item.get("owner", "")).strip()
        due = str(item.get("due", "")).strip()
        if ptype == "action":
            if owner:
                entry["owner"] = owner
            if due:
                entry["due"] = due
        cleaned.append(entry)

    logger.info("[meetings/extractor] Extracted %d points from %d-char transcript", len(cleaned), len(text))
    return cleaned
