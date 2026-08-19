from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


def _normalize_status(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _normalize_priority(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_status_category(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if normalized in {"not_started", "in_progress", "done", "blocked", "unknown"}:
        return normalized
    return "unknown"


def derive_status_category(
    *,
    status: str,
    item: Dict[str, Any],
    status_category_map: Optional[Dict[str, Any]] = None,
) -> str:
    raw_status = str(item.get("status") or "").strip()
    normalized_status = str(status or "").strip().lower()
    explicit_category = item.get("status_category")
    normalized_explicit = _normalize_status_category(explicit_category)
    if normalized_explicit != "unknown":
        return normalized_explicit

    if isinstance(status_category_map, dict) and status_category_map:
        candidates = [
            raw_status,
            raw_status.lower(),
            normalized_status,
            normalized_status.replace("_", " "),
        ]
        for key in candidates:
            mapped = status_category_map.get(key)
            normalized_mapped = _normalize_status_category(mapped)
            if normalized_mapped != "unknown":
                return normalized_mapped

    is_closed = item.get("is_closed")
    if isinstance(is_closed, bool) and is_closed:
        return "done"
    resolution = str(item.get("resolution") or "").strip()
    if resolution:
        return "done"

    if normalized_status in {"todo", "to_do", "open", "new", "backlog", "selected_for_development", "selected"}:
        return "not_started"
    if normalized_status in {"in_progress", "doing", "in_review", "review", "qa", "testing"}:
        return "in_progress"
    if normalized_status in {"done", "completed", "closed", "resolved"}:
        return "done"
    if normalized_status in {"blocked", "on_hold", "hold", "waiting", "waiting_on_dependency"}:
        return "blocked"
    return "unknown"


@dataclass(frozen=True)
class NormalizedTask:
    task_key: str
    title: str
    status: str
    priority: str
    status_category: str
    health_status: str
    health_reason: str
    health_action: str
    assignee_name: Optional[str] = None
    assignee_email: Optional[str] = None
    due_date: Optional[str] = None
    updated_at: Optional[str] = None
    start_date: Optional[str] = None
    reopen_count: int = 0
    assignee_load_score: Optional[float] = None
    sprint_name: Optional[str] = None
    sprint_state: Optional[str] = None
    sprint_end_at: Optional[str] = None
    url: Optional[str] = None

    @staticmethod
    def from_health_item(
        item: Dict[str, Any],
        *,
        status_category_map: Optional[Dict[str, Any]] = None,
    ) -> "NormalizedTask":
        normalized_status = _normalize_status(item.get("status"))
        return NormalizedTask(
            task_key=str(item.get("task_key") or ""),
            title=str(item.get("title") or item.get("task_key") or ""),
            status=normalized_status,
            priority=_normalize_priority(item.get("priority")),
            status_category=derive_status_category(
                status=normalized_status,
                item=item,
                status_category_map=status_category_map,
            ),
            health_status=str(item.get("health_status") or "").strip().lower(),
            health_reason=str(item.get("health_reason") or "").strip(),
            health_action=str(item.get("health_action") or "").strip(),
            assignee_name=item.get("assignee_name") or item.get("assignee"),
            assignee_email=item.get("assignee_email"),
            due_date=item.get("due_date"),
            updated_at=item.get("updated_at"),
            start_date=item.get("start_date"),
            reopen_count=int(item.get("reopen_count") or 0),
            assignee_load_score=(
                float(item.get("assignee_load_score"))
                if isinstance(item.get("assignee_load_score"), (int, float))
                else None
            ),
            sprint_name=(
                str(
                    item.get("sprint_name")
                    or ((item.get("sprint") or {}).get("name") if isinstance(item.get("sprint"), dict) else "")
                ).strip()
                or None
            ),
            sprint_state=(
                str(
                    item.get("sprint_state")
                    or ((item.get("sprint") or {}).get("state") if isinstance(item.get("sprint"), dict) else "")
                ).strip().lower()
                or None
            ),
            sprint_end_at=(
                item.get("sprint_end_at")
                or ((item.get("sprint") or {}).get("end_at") if isinstance(item.get("sprint"), dict) else None)
            ),
            url=item.get("url"),
        )
