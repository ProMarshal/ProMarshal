"""Cortex response rendering and delivery scaffold."""

from dataclasses import dataclass
from typing import Any, Dict, Optional
import json
import re

from app.core.database import get_database
from app.core.security import encryption_service
from app.cortex.security.output_sanitizer import sanitize_model_output
from app.integrations.slack.delivery import chat_post_message_with_retry
from slack_sdk.web.async_client import AsyncWebClient

# Ack logic lives in agent_runtime/ack.py (shared with Cadence).
from app.agent_runtime.ack import build_ack_text, send_ack_best_effort


_DOUBLE_BOLD_PATTERN = re.compile(r"\*\*([^*\n]+?)\*\*")
_SINGLE_BOLD_PATTERN = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_BULLET_PREFIX_PATTERN = re.compile(r"^\s*(?:[-•]|\d+[.)])\s+")
_HEADING_TEXT_PATTERN = re.compile(r"^(?:\*{1,2})?(.*?)(?:\*{1,2})?$")


def _normalize_slack_text_format(text: str) -> str:
    """
    Normalize outbound Slack text for consistent readability.

    Policy:
    - Keep section headings bold using single-star form: *Heading:*
    - Remove unnecessary **...** bold markers from regular body lines
    - Remove non-heading inline emphasis markers in body lines
    """
    raw = str(text or "")
    if not raw:
        return ""

    lines = raw.splitlines()
    normalized_lines = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            normalized_lines.append(line)
            continue

        is_bullet = bool(_BULLET_PREFIX_PATTERN.match(stripped))
        is_full_line_bold = bool(re.match(r"^\*[^*\n]+\*$", stripped))
        is_indented_detail = bool(line and (line[0].isspace() or line.startswith("\t")))
        if is_full_line_bold:
            normalized_lines.append(stripped)
            continue

        next_non_empty = ""
        for candidate_line in lines[idx + 1 :]:
            candidate = candidate_line.strip()
            if candidate:
                next_non_empty = candidate
                break
        next_is_bullet = bool(_BULLET_PREFIX_PATTERN.match(next_non_empty))

        looks_like_heading = (not is_bullet) and stripped.endswith(":")
        looks_like_title_heading = (
            (not is_bullet)
            and (not is_indented_detail)
            and (not stripped.endswith(":"))
            and len(stripped) <= 80
            and next_is_bullet
            and not stripped.endswith(("?", "!", "."))
            and not stripped.lower().startswith(("status:", "assignee:", "due:", "type:"))
        )

        if looks_like_heading or looks_like_title_heading:
            heading_body = stripped[:-1].strip() if looks_like_heading else stripped
            heading_body = _DOUBLE_BOLD_PATTERN.sub(r"\1", heading_body)
            heading_match = _HEADING_TEXT_PATTERN.match(heading_body)
            if heading_match:
                heading_body = str(heading_match.group(1) or "").strip()
            if heading_body:
                suffix = ":" if looks_like_heading else ""
                normalized_lines.append(f"*{heading_body}{suffix}*")
                continue

        cleaned = _DOUBLE_BOLD_PATTERN.sub(r"\1", line)
        # Preserve single-star inline emphasis for list rows (for example: - *ID (Type)*: Title)
        # while still stripping non-heading inline emphasis in plain body lines.
        if not is_bullet:
            cleaned = _SINGLE_BOLD_PATTERN.sub(r"\1", cleaned)
        cleaned = cleaned.replace("**", "")
        normalized_lines.append(cleaned)

    normalized = "\n".join(normalized_lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


@dataclass(frozen=True)
class ResponsePayload:
    """Canonical output payload sent back to the calling channel."""

    text: str
    channel_id: str
    thread_ts: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class CortexResponseService:
    """Formats and dispatches response payloads."""

    async def send(self, payload: ResponsePayload) -> Dict[str, Any]:
        """
        Send response and return provider delivery metadata.

        Current baseline supports Slack delivery only.
        """
        sanitization = sanitize_model_output(payload.text)
        sanitized_text = str(sanitization.get("text") or "")
        formatted_text = _normalize_slack_text_format(sanitized_text)
        if bool(sanitization.get("suspicious")):
            print(
                "Cortex output sanitizer suspicious_redaction: "
                + json.dumps(
                    {
                        "redaction_count": int(sanitization.get("redaction_count") or 0),
                        "detected_types": sanitization.get("detected_types") or [],
                        "reasons": sanitization.get("suspicious_reasons") or [],
                        "project_id": str((payload.metadata or {}).get("project_id") or ""),
                        "channel_id": payload.channel_id,
                    },
                    ensure_ascii=True,
                )
            )

        sanitized_payload = ResponsePayload(
            text=formatted_text,
            channel_id=payload.channel_id,
            thread_ts=payload.thread_ts,
            metadata=payload.metadata,
        )

        metadata = payload.metadata or {}
        source = str(metadata.get("source") or "")
        project_id = str(metadata.get("project_id") or "")

        if source.startswith("slack_"):
            result = await self._send_slack(payload=sanitized_payload, project_id=project_id)
            result["sanitization"] = {
                "redaction_count": int(sanitization.get("redaction_count") or 0),
                "restored_count": int(sanitization.get("restored_count") or 0),
                "detected_types": sanitization.get("detected_types") or [],
                "suspicious": bool(sanitization.get("suspicious")),
                "suspicious_reasons": sanitization.get("suspicious_reasons") or [],
            }
            return result

        return {
            "ok": False,
            "reason": "unsupported_source",
            "sanitization": {
                "redaction_count": int(sanitization.get("redaction_count") or 0),
                "restored_count": int(sanitization.get("restored_count") or 0),
                "detected_types": sanitization.get("detected_types") or [],
                "suspicious": bool(sanitization.get("suspicious")),
                "suspicious_reasons": sanitization.get("suspicious_reasons") or [],
            },
        }

    async def _send_slack(self, *, payload: ResponsePayload, project_id: str) -> Dict[str, Any]:
        if not project_id:
            return {"ok": False, "reason": "missing_project_id"}
        if not payload.channel_id:
            return {"ok": False, "reason": "missing_channel_id"}

        db = get_database()
        if db is None:
            return {"ok": False, "reason": "database_not_initialized"}

        project = await db.projects.find_one(
            {"project_id": project_id},
            {"integrations.slack.access_token": 1},
        )
        encrypted_token = (
            ((project or {}).get("integrations") or {}).get("slack") or {}
        ).get("access_token")
        if not encrypted_token:
            return {"ok": False, "reason": "slack_token_missing"}

        try:
            token = encryption_service.decrypt(encrypted_token)
            slack_client = AsyncWebClient(token=token)
            message_payload: Dict[str, Any] = {
                "channel": payload.channel_id,
                "text": payload.text,
            }
            if payload.thread_ts:
                message_payload["thread_ts"] = payload.thread_ts

            response = await chat_post_message_with_retry(
                slack_client=slack_client,
                payload=message_payload,
                operation_name="cortex.response.send",
            )
            return {
                "ok": bool(response.get("ok", False)),
                "provider": "slack",
                "channel_id": payload.channel_id,
                "thread_ts": payload.thread_ts,
                "ts": response.get("ts"),
                "error": response.get("error"),
            }
        except Exception as exc:
            slack_error_code = getattr(exc, "details", {}).get("error_code", "unknown")
            http_status = getattr(exc, "http_status", None)
            print(
                f"Cortex Slack send failed: {exc} "
                f"slack_error_code={slack_error_code} http_status={http_status} "
                f"channel_id={payload.channel_id}"
            )
            return {"ok": False, "reason": "slack_send_exception", "slack_error_code": slack_error_code}


def build_stage3_ack_text(interaction_type: str) -> str:
    """
    Cortex ack text. Delegates to shared agent_runtime/ack.py.

    The interaction_type parameter is preserved for call-site compatibility.
    Ack pool selection is now handled by build_ack_text(source="cortex").
    """
    _ = interaction_type
    return build_ack_text("cortex")


async def send_stage3_ack_best_effort(
    slack_client: Any,
    *,
    channel_id: str,
    thread_ts: Optional[str],
    interaction_type: str,
) -> bool:
    """
    Send Cortex ack message as best effort. Delegates to shared agent_runtime/ack.py.

    Non-blocking — callers should dispatch via asyncio.create_task(...).
    """
    return await send_ack_best_effort(
        slack_client,
        channel_id=channel_id,
        thread_ts=thread_ts,
        source="cortex",
    )
