"""Cortex ingress normalization (stages 1-2) scaffold."""

from dataclasses import dataclass
import re
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.cortex.slack_events import SlackEventEnvelope


@dataclass(frozen=True)
class IngressContext:
    """Normalized ingress context passed into orchestration."""

    source: str
    team_id: str
    channel_id: str
    user_id: str
    project_id: str
    session_anchor: str
    message_text: str
    thread_ts: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class SlackIngressClassification:
    """Stage 1-2 classification result for Slack ingress events."""

    accepted: bool
    interaction_type: str
    source: str
    session_anchor: str
    normalized_text: str
    reason: str = ""


_MENTION_RE = re.compile(r"<@[^>]+>")
_WORD_RE = re.compile(r"[a-z0-9']+")

_GREETING_WORDS = {
    "good",
    "hi",
    "hii",
    "hiii",
    "hey",
    "heyy",
    "heyyy",
    "heya",
    "hei",
    "hello",
    "yo",
    "hola",
    "greetings",
    "gm",
    "morning",
    "afternoon",
    "evening",
    "there",
    "team",
    "everyone",
    "marshal",
    "bot",
    "thanks",
    "thank",
    "you",
}

_ACTION_OR_QUESTION_HINTS = {
    "what",
    "why",
    "how",
    "when",
    "where",
    "who",
    "which",
    "can",
    "could",
    "would",
    "should",
    "is",
    "are",
    "do",
    "does",
    "did",
    "please",
    "help",
    "show",
    "list",
    "create",
    "update",
    "close",
    "add",
    "delete",
    "assign",
    "summarize",
    "status",
    "blockers",
    "task",
    "tasks",
}


def _strip_slack_mentions(text: str) -> str:
    return _MENTION_RE.sub("", text).strip()


def classify_message_intent(text: str) -> str:
    """
    Classify normalized text into ingress intent buckets:
    - greeting_only: salutation without question/action intent
    - question_or_action: operational query/command
    - ignored: empty/whitespace
    """
    normalized = (text or "").strip()
    if not normalized:
        return "ignored"

    lowered = normalized.lower()
    tokens = _WORD_RE.findall(lowered)
    token_set = set(tokens)

    # Any explicit question/action signal should keep message actionable,
    # even if it starts with a greeting ("hi, can you list tasks?").
    if "?" in lowered:
        return "question_or_action"
    if token_set.intersection(_ACTION_OR_QUESTION_HINTS):
        return "question_or_action"

    if tokens and len(tokens) <= 6 and all(token in _GREETING_WORDS for token in tokens):
        return "greeting_only"

    return "question_or_action"


def classify_slack_event_for_cortex(event: "SlackEventEnvelope") -> SlackIngressClassification:
    """
    Stage 1-2 Slack event classification:
    - app mention
    - direct message
    """
    raw_event = event.raw_event or {}
    event_type = str(raw_event.get("type") or "")
    subtype = str(raw_event.get("subtype") or "")
    text = (event.text or "").strip()
    is_dm = bool(event.channel_id and event.channel_id.startswith("D"))
    allow_channel_thread_followup = bool(raw_event.get("_cortex_allow_channel_thread_followup"))

    if raw_event.get("bot_id"):
        return SlackIngressClassification(
            accepted=False,
            interaction_type="ignored",
            source="slack_dm" if is_dm else "slack_channel",
            session_anchor=event.channel_id if is_dm else (event.thread_ts or event.event_ts),
            normalized_text="",
            reason="bot_event_ignored",
        )

    if event_type == "app_mention":
        normalized_text = _strip_slack_mentions(text)
        return SlackIngressClassification(
            accepted=bool(normalized_text),
            interaction_type="mention",
            source="slack_dm" if is_dm else "slack_channel",
            session_anchor=event.channel_id if is_dm else (event.thread_ts or event.event_ts),
            normalized_text=normalized_text,
            reason="" if normalized_text else "empty_mention_text",
        )

    if event_type == "message" and is_dm and not subtype:
        return SlackIngressClassification(
            accepted=bool(text),
            interaction_type="dm",
            source="slack_dm",
            session_anchor=event.channel_id,
            normalized_text=text,
            reason="" if text else "empty_dm_text",
        )

    if event_type == "message" and not is_dm and not subtype and allow_channel_thread_followup:
        return SlackIngressClassification(
            accepted=bool(text),
            interaction_type="thread_followup",
            source="slack_channel",
            session_anchor=event.thread_ts or event.event_ts,
            normalized_text=text,
            reason="" if text else "empty_thread_followup_text",
        )

    return SlackIngressClassification(
        accepted=False,
        interaction_type="ignored",
        source="slack_dm" if is_dm else "slack_channel",
        session_anchor=event.channel_id if is_dm else (event.thread_ts or event.event_ts),
        normalized_text=text,
        reason="unsupported_event_type",
    )


async def normalize_slack_event_for_cortex(
    event: "SlackEventEnvelope",
    project_id: str,
    classification: SlackIngressClassification,
    metadata_updates: Optional[Dict[str, Any]] = None,
) -> IngressContext:
    """Build normalized ingress context for downstream Cortex stages."""
    metadata: Dict[str, Any] = {
        "event_ts": event.event_ts,
        "interaction_type": classification.interaction_type,
        "stage_1_2_reason": classification.reason,
    }
    for key, value in (metadata_updates or {}).items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        metadata[key] = value

    return IngressContext(
        source=classification.source,
        team_id=event.team_id,
        channel_id=event.channel_id,
        user_id=event.user_id,
        project_id=project_id,
        session_anchor=classification.session_anchor,
        message_text=classification.normalized_text,
        thread_ts=event.thread_ts,
        metadata=metadata,
    )
