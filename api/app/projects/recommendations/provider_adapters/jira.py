from __future__ import annotations

from typing import Any, Dict, Optional

from app.projects.recommendations.normalized_task_model import NormalizedTask
from app.projects.recommendations.provider_adapters.base import ProviderCapabilities, TaskProviderAdapter


def _to_iso_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return None
    raw = str(value).strip()
    return raw or None


class JiraTaskAdapter(TaskProviderAdapter):
    provider_name = "jira"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            has_reviews=True,
            has_sprints=True,
            has_dependencies=True,
        )

    def normalize_task(self, raw_task: Dict[str, Any]) -> NormalizedTask:
        # Maps Jira-like brain task shape to provider-neutral model.
        return NormalizedTask(
            task_key=str(raw_task.get("external_id") or raw_task.get("task_key") or ""),
            title=str(raw_task.get("title") or raw_task.get("external_id") or ""),
            status=str(raw_task.get("status") or "").strip().lower().replace(" ", "_"),
            priority=str(raw_task.get("priority") or "").strip().lower(),
            status_category="unknown",
            health_status=str(raw_task.get("health_status") or "").strip().lower(),
            health_reason=str(raw_task.get("health_reason") or "").strip(),
            health_action=str(raw_task.get("health_action") or "").strip(),
            assignee_name=raw_task.get("assignee_name") or raw_task.get("assignee"),
            assignee_email=raw_task.get("assignee_email"),
            due_date=_to_iso_or_none(raw_task.get("due_date")),
            start_date=_to_iso_or_none(raw_task.get("start_date")),
            url=raw_task.get("url"),
        )
