"""Slack project-context footer helpers for DM responses."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.integrations.slack.project_resolver import list_slack_member_projects


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def append_project_context_footer(text: str, footer: Optional[str]) -> str:
    body = str(text or "").strip()
    suffix = _normalize_text(footer)
    if not body or not suffix:
        return body
    if suffix in body:
        return body
    return f"{body}\n\n{suffix}"


async def build_project_context_footer(
    *,
    db,
    team_id: str,
    slack_user_id: str,
    project: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    listing = await list_slack_member_projects(
        db=db,
        team_id=_normalize_text(team_id),
        slack_user_id=_normalize_text(slack_user_id),
    )
    if not getattr(listing, "ok", False):
        return None
    projects = [item for item in (getattr(listing, "projects", None) or []) if isinstance(item, dict)]
    if len(projects) <= 1:
        return None

    project_id = _normalize_text((project or {}).get("project_id")) or _normalize_text(
        getattr(listing, "active_project_id", None)
    )
    if not project_id:
        return None

    project_name = _normalize_text((project or {}).get("project_name"))
    if not project_name:
        selected = next(
            (
                item
                for item in projects
                if _normalize_text(item.get("project_id")) == project_id
            ),
            None,
        )
        project_name = _normalize_text((selected or {}).get("project_name")) or project_id

    return (
        f'Current Project: {project_name} ({project_id}) | '
        f'To switch, type: "switch project"'
    )

