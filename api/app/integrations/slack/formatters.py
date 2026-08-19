"""Slack Block Kit response formatters."""

from typing import Any, Dict, List

from app.tasks.schemas import TaskResponseDTO


class SlackFormatter:
    """Format responses for Slack Block Kit."""

    @staticmethod
    def format_task_list(tasks: List[TaskResponseDTO]) -> Dict[str, Any]:
        """
        Format work-item list as Slack Block Kit message.

        Args:
            tasks: List of TaskResponseDTO

        Returns:
            Slack Block Kit response
        """
        if not tasks:
            return SlackFormatter._format_empty_task_list()

        blocks: List[Dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Work Items ({len(tasks)} active)",
                },
            }
        ]

        for task in tasks:
            priority_badge = {
                "critical": "[Critical]",
                "high": "[High]",
                "medium": "[Medium]",
                "low": "[Low]",
            }.get((task.priority or "medium").lower(), "[Medium]")

            due_text = ""
            if task.due_date:
                due_text = f" | Due: {task.due_date.strftime('%b %d')}"

            status_display = str(
                task.status_display or task.status_raw or task.status or ""
            ).replace("_", " ").title()

            task_text = (
                f"{priority_badge} *[{task.external_id}] {task.title}*\n"
                f"Status: {status_display}"
            )
            if task.priority:
                task_text += f" | Priority: {task.priority.capitalize()}"
            task_text += due_text

            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": task_text},
                    "accessory": {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View in Jira"},
                        "url": task.url,
                        "action_id": f"view_task_{task.external_id}",
                    },
                }
            )
            blocks.append({"type": "divider"})

        if blocks and blocks[-1].get("type") == "divider":
            blocks.pop()

        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "Tip: Use `/promarshal create-task` to add new work items",
                    }
                ],
            }
        )

        return {"response_type": "ephemeral", "blocks": blocks}

    @staticmethod
    def _format_empty_task_list() -> Dict[str, Any]:
        """Format empty work-item list message."""
        return {
            "response_type": "ephemeral",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "All caught up!"},
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "No active work items found right now.",
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Want to create a new work item? Use `/promarshal create-task`",
                    },
                },
            ],
        }
