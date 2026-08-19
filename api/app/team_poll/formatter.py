"""Slack message formatters for Team Poll."""

from __future__ import annotations

from typing import Any, Dict, List

from app.team_poll.schema import build_member_prompt_text


def build_poll_prompt(
    *,
    session: Dict[str, Any],
    owner_prompt: bool,
) -> Dict[str, Any]:
    question_text = str(session.get("question_text") or "").strip()
    member_name = str(session.get("member_name") or "there").strip()
    project_name = str(session.get("project_name") or "your project").strip()
    text = build_member_prompt_text(
        member_name=member_name,
        project_name=project_name,
        question_text=question_text,
        owner_prompt=owner_prompt,
    )

    payload: Dict[str, Any] = {"text": text, "blocks": None}
    if owner_prompt:
        session_id = str(session.get("session_id") or "").strip()
        poll_id = str(session.get("poll_id") or "").strip()
        project_id = str(session.get("project_id") or "").strip()
        payload["blocks"] = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Skip My Response"},
                        "style": "danger",
                        "action_id": "team_poll_owner_skip",
                        "value": f"{session_id}|{poll_id}|{project_id}",
                    }
                ],
            },
        ]
    return payload


def build_owner_status_text(
    *,
    question_text: str,
    responded: int,
    pending: int,
    skipped_owner: int,
    deferred_by_cadence: int,
    blocked_by_cadence: int,
    total_nudges_sent: int,
    last_nudged_at: str,
    time_remaining: str,
    poll_id: str,
) -> str:
    return (
        "Team poll status\n"
        f"- Poll ID: {poll_id}\n"
        f"- Question: {question_text}\n"
        f"- Responded: {responded}\n"
        f"- Pending: {pending}\n"
        f"- Deferred by active cadence: {deferred_by_cadence}\n"
        f"- Blocked by active cadence: {blocked_by_cadence}\n"
        f"- Owner skipped: {skipped_owner}\n"
        f"- Total nudges sent: {total_nudges_sent}\n"
        f"- Last nudged at: {last_nudged_at}\n"
        f"- Time remaining: {time_remaining}"
    )


def build_owner_summary_text(
    *,
    heading: str,
    responded_count: int,
    total_count: int,
    responded_members: List[str],
    pending_members: List[str],
    grouped_summary_lines: List[str],
) -> str:
    safe_heading = str(heading or "Team Update").strip() or "Team Update"
    safe_grouped = [str(line).strip() for line in (grouped_summary_lines or []) if str(line).strip()]
    status_line = f"Team response status: {responded_count}/{total_count} responded"

    lines = [
        "Hei Boss - here is the team response summary.",
        "",
        f"*{safe_heading}*",
        "",
        status_line,
    ]
    if pending_members:
        lines.append(f"Pending: {', '.join(pending_members)}")
    lines.extend(safe_grouped[:3])

    return "\n".join(lines)
