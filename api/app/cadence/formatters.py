"""Format messages for Slack (Cadence sessions).

Formatting philosophy:
  • Conversational — feels like a human PM, not a bot.
  • Structured — Slack Block Kit for visual clarity.
  • Minimal — every word earns its place.
"""
import json
from typing import List, Dict, Any, Optional

from app.projects.recommendations.normalized_task_model import derive_status_category

# ---------------------------------------------------------------------------
#  Emoji helpers
# ---------------------------------------------------------------------------

_STATUS_EMOJI = {
    "todo": "⚪",
    "in_progress": "🔵",
    "in progress": "🔵",
    "in review": "🟣",
    "done": "✅",
    "blocked": "🔴",
}

_PRIORITY_EMOJI = {
    "low": "🟢",
    "medium": "🟡",
    "high": "🟠",
    "critical": "🔴",
}


def _status_display(status: str) -> str:
    """Return a human-friendly status with emoji."""
    clean = status.replace("_", " ").strip().lower()
    emoji = _STATUS_EMOJI.get(clean, "⚪")
    label = clean.title()
    return f"{emoji} {label}"


def _priority_display(priority: str) -> str:
    """Return a human-friendly priority with emoji."""
    clean = priority.replace("_", " ").strip().lower()
    emoji = _PRIORITY_EMOJI.get(clean, "🟡")
    label = clean.title()
    return f"{emoji} {label}"


def _plain_status(status: str) -> str:
    return str(status or "").replace("_", " ").strip().title() or "Unknown"


def _plain_priority(priority: str) -> str:
    return str(priority or "").replace("_", " ").strip().title() or "Medium"


def _is_completed_status(value: Any) -> bool:
    token = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if token in {"done", "completed", "complete", "closed", "resolved", "finished", "shipped"}:
        return True
    category = derive_status_category(status=token, item={"status": str(value or "")}, status_category_map=None)
    return category == "done"


def _plain_work_item_type(task: Dict[str, Any]) -> str:
    provider_type = str(task.get("provider_type") or task.get("issue_type") or "").strip()
    if provider_type:
        return provider_type
    canonical_type = str(task.get("work_item_type") or "").strip().replace("_", " ")
    if canonical_type:
        return canonical_type.title()
    return "Work Item"


def _plain_last_comment(task: Dict[str, Any], *, max_chars: int = 240) -> Optional[str]:
    text = str(task.get("last_comment") or "").strip()
    if not text:
        text = str(task.get("comment") or "").strip()
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


# ---------------------------------------------------------------------------
#  Free tier (simple text, unchanged)
# ---------------------------------------------------------------------------

def format_free_tier_message(tasks: List[Dict[str, Any]], member_name: str) -> str:
    task_count = len(tasks)
    plural = "work item" if task_count == 1 else "work items"

    message = f"Hello {member_name},\n\n"
    message += f"You have {task_count} pending {plural}:\n\n"

    for i, task in enumerate(tasks, 1):
        task_key = task.get("external_id", "N/A")
        title = task.get("title", "Untitled")
        status = task.get("status", "unknown")
        url = task.get("url", "")

        message += f"{i}. {task_key}: {title}\n"
        message += f"   Status: {status.replace('_', ' ').title()}\n"
        if url:
            message += f"   Link: {url}\n"
        message += "\n"

    message += "Have a productive day!"
    return message


# ---------------------------------------------------------------------------
#  Greeting message (sent once at session start)
# ---------------------------------------------------------------------------

def format_greeting_message(
    member_name: str,
    project_name: str,
    in_progress_count: int,
    backlog_count: int,
) -> Dict[str, Any]:
    """Build a warm greeting Block Kit message for the start of a Cadence session."""
    first_name = (member_name or "there").split()[0]

    # Build summary line
    parts: list[str] = []
    if in_progress_count:
        t = "work item" if in_progress_count == 1 else "work items"
        parts.append(f"*{in_progress_count} {t} in progress*")
    if backlog_count:
        t = "work item" if backlog_count == 1 else "work items"
        parts.append(f"*{backlog_count} {t}* assigned but not yet started")

    if parts:
        summary = "You've got " + " and ".join(parts) + "."
    else:
        summary = "Looks like your plate is clear — nice work!"

    # Decide body text based on what's available
    if in_progress_count:
        body = f"{summary}\nLet's quickly run through the active ones."
    elif backlog_count:
        body = f"{summary}\nWant to pick one up today?"
    else:
        body = summary

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"👋 Hey {first_name}, quick Cadence update on *{project_name}*!\n\n{body}",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Tap the button below each work item, or just _reply to the messages_ with your update.",
                }
            ],
        },
    ]

    return {"blocks": blocks}


# ---------------------------------------------------------------------------
#  Per-task card (in-progress tasks)
# ---------------------------------------------------------------------------

def format_paid_tier_message(
    task: Dict[str, Any],
    task_index: int,
    total_tasks: int,
    session_id: str,
    project_id: Optional[str] = None,
    llm_message: str = None,
) -> Dict[str, Any]:
    """Build a structured task card with a single Update button."""
    title = task.get("title", "Untitled")
    task_key = task.get("external_id", "N/A")
    status = task.get("status", "unknown")
    priority = task.get("priority", "medium")
    last_comment = _plain_last_comment(task)
    url = task.get("url", "")
    action_value = json.dumps(
        {
            "session_id": session_id,
            "task_index": task_index,
            "project_id": str(project_id or ""),
        }
    )

    blocks: list[dict] = []

    # LLM transition phrase (only for 2nd+ tasks)
    if llm_message:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": llm_message},
        })

    blocks.append({"type": "divider"})

    details_lines = [
        f"*Task #:* `{task_key}`",
        f"*Type:* {_plain_work_item_type(task)}",
        f"*Title:* {title}",
        f"*Status:* {_plain_status(status)}",
    ]
    if str(priority or "").strip():
        details_lines.append(f"*Priority:* {_plain_priority(priority)}")
    if last_comment:
        details_lines.append(f"*Last Comment:* {last_comment}")
    if url:
        details_lines.append(f"*Link:* <{url}|Jira>")

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(details_lines)},
    })

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "Please share your update by typing your response or using the update work item button."
        },
    })

    # Single action button
    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Update Work Item"},
                "value": action_value,
                "action_id": "open_update_modal",
                "style": "primary",
            }
        ],
    })

    return {"blocks": blocks}


# ---------------------------------------------------------------------------
#  Backlog nudge (after in-progress tasks are done)
# ---------------------------------------------------------------------------

def format_backlog_nudge(
    backlog_tasks: List[Dict[str, Any]],
    session_id: str,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a message offering to view assigned-but-not-started work items."""
    count = len(backlog_tasks)
    t = "work item" if count == 1 else "work items"
    view_value = json.dumps(
        {
            "session_id": session_id,
            "action": "view_backlog",
            "project_id": str(project_id or ""),
        }
    )
    skip_value = json.dumps(
        {
            "session_id": session_id,
            "action": "skip_backlog",
            "project_id": str(project_id or ""),
        }
    )

    blocks = [
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"That's all your active work items! 🎉\n\n"
                    f"You still have *{count} {t}* assigned but not yet started.\n\n"
                    "Would you like to pick one up now?\n\n"
                    "Reply `show` to see them, or `later` to skip for now."
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "You can also ask anytime for your backlog or to-do work items and I'll help.",
                }
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "👀 View Them"},
                    "value": view_value,
                    "action_id": "view_backlog",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "I'm Good"},
                    "value": skip_value,
                    "action_id": "skip_backlog",
                },
            ],
        },
    ]

    return {"blocks": blocks}


def format_backlog_pick_list(
    backlog_tasks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build numbered backlog list and prompt for selection."""
    if not backlog_tasks:
        return {
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "No backlog work items found right now.",
                    },
                }
            ]
        }

    lines: List[str] = []
    for idx, task in enumerate(backlog_tasks, start=1):
        task_key = task.get("external_id", "N/A")
        title = task.get("title", "Untitled")
        item_type = _plain_work_item_type(task)
        priority = task.get("priority", "medium")
        lines.append(
            f"{idx}. `{task_key}` ({item_type}) - {title} ({priority.replace('_', ' ').title()})"
        )

    blocks = [
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Backlog work items you can pick up now:*",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "\n".join(lines),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "Reply with a work item number or key to start it "
                    "(example: `1` or `SCRUM-4`).\n"
                    "Reply `later` if you want to skip for now."
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "You can ask anytime for your backlog or to-do work items and I'll help.",
                }
            ],
        },
    ]
    return {"blocks": blocks}


# ---------------------------------------------------------------------------
#  Backlog task cards (shown when user taps "View Them")
# ---------------------------------------------------------------------------

def format_backlog_task(
    task: Dict[str, Any],
    session_id: str,
    task_index: int,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Card for a todo/backlog task — offers to start working on it."""
    title = task.get("title", "Untitled")
    task_key = task.get("external_id", "N/A")
    priority = task.get("priority", "medium")
    last_comment = _plain_last_comment(task)
    url = task.get("url", "")
    action_value = json.dumps(
        {
            "session_id": session_id,
            "task_index": task_index,
            "project_id": str(project_id or ""),
        }
    )

    details_lines = [
        f"*Task #:* `{task_key}`",
        f"*Type:* {_plain_work_item_type(task)}",
        f"*Title:* {title}",
        "*Status:* To Do",
    ]
    if str(priority or "").strip():
        details_lines.append(f"*Priority:* {_plain_priority(priority)}")
    if last_comment:
        details_lines.append(f"*Last Comment:* {last_comment}")
    if url:
        details_lines.append(f"*Link:* <{url}|Jira>")

    blocks = [
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(details_lines)},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "Would you like to start working on this work item?"},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Update Work Item"},
                    "value": action_value,
                    "action_id": "open_update_modal",
                    "style": "primary",
                },
            ],
        },
    ]

    return {"blocks": blocks}


# ---------------------------------------------------------------------------
#  Session summary (Block Kit version)
# ---------------------------------------------------------------------------

def format_session_summary(
    summary_text: str,
    tasks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a Block Kit summary message at the end of a Cadence session."""
    # Build per-task recap lines
    recap_lines: list[str] = []
    for task in tasks:
        title = task.get("title", "Untitled")
        updated_status = task.get("updated_status")
        comment = task.get("comment", "")
        original_status = task.get("status", "unknown")

        status_changed = updated_status and updated_status != original_status
        has_comment = bool(comment and comment.strip())

        if status_changed and _is_completed_status(updated_status):
            recap_lines.append(f"• *{title}* — marked done ✅")
        elif status_changed:
            recap_lines.append(f"• *{title}* — {_status_display(updated_status)}")
        elif has_comment:
            short = comment.strip()[:60]
            recap_lines.append(f"• *{title}* — \"{short}\"")
        else:
            recap_lines.append(f"• *{title}* — no changes")

    recap = "\n".join(recap_lines)

    blocks = [
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"✨ *Cadence complete!*\n\n{summary_text}",
            },
        },
    ]

    if recap_lines:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Here's what we covered:\n{recap}",
            },
        })

    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": "See you next time! 💪"},
        ],
    })

    return {"blocks": blocks}


# ---------------------------------------------------------------------------
#  Update task modal (unchanged — works great as-is)
# ---------------------------------------------------------------------------

def format_update_task_modal(
    session_id: str,
    task_index: int,
    task: Dict[str, Any],
    total_tasks: int,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    task_key = task.get("external_id", "N/A")
    title = task.get("title", "Untitled")
    current_status = task.get("status", "todo")
    priority = task.get("priority", "medium")
    last_comment = _plain_last_comment(task)
    url = task.get("url", "")

    current_status_display = _plain_status(current_status)
    priority_display = _plain_priority(priority)

    status_options = [
        {"text": {"type": "plain_text", "text": "To Do"}, "value": "todo"},
        {"text": {"type": "plain_text", "text": "In Progress"}, "value": "in_progress"},
        {"text": {"type": "plain_text", "text": "Done"}, "value": "done"},
        {"text": {"type": "plain_text", "text": "Blocked"}, "value": "blocked"},
    ]

    initial_option = next(
        (opt for opt in status_options if opt["value"] == current_status),
        status_options[0],
    )
    private_metadata = json.dumps(
        {
            "session_id": session_id,
            "task_index": task_index,
            "project_id": str(project_id or ""),
        }
    )

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Task #:* `{task_key}`\n"
                    f"*Type:* {_plain_work_item_type(task)}\n"
                    f"*Title:* {title}\n"
                    f"*Status:* {current_status_display}\n"
                    f"*Priority:* {priority_display}"
                ),
            },
        },
    ]

    if last_comment:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Last Comment:* {last_comment}"},
        })

    if url:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"<{url}|View in Jira>"},
        })

    blocks.append({"type": "divider"})

    blocks.append({
        "type": "input",
        "block_id": "status_input",
        "label": {"type": "plain_text", "text": "New Status"},
        "element": {
            "type": "static_select",
            "action_id": "status_value",
            "initial_option": initial_option,
            "options": status_options,
        },
        "optional": False,
    })

    blocks.append({
        "type": "input",
        "block_id": "comment_input",
        "label": {"type": "plain_text", "text": "Comment"},
        "element": {
            "type": "plain_text_input",
            "action_id": "comment_value",
            "multiline": True,
            "placeholder": {
                "type": "plain_text",
                "text": "Any notes, blockers, or updates...",
            },
        },
        "optional": False,
    })

    return {
        "type": "modal",
        "callback_id": "task_update_modal",
        "title": {"type": "plain_text", "text": "Update Work Item"},
        "submit": {"type": "plain_text", "text": "Save & Continue"},
        "close": {"type": "plain_text", "text": "Skip"},
        "private_metadata": private_metadata,
        "blocks": blocks,
    }
