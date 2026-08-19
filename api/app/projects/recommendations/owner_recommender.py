from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo


def _parse_dt(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _is_done(status: Any) -> bool:
    return str(status or "").strip().lower() in {"done", "completed", "closed", "resolved"}


def _assignee_key(item: Dict[str, Any]) -> str:
    return str(item.get("assignee_name") or "").strip()


def _clean_timezone(value: Any) -> str:
    return str(value or "").strip()


def _parse_hhmm(value: Any, default: int) -> int:
    raw = str(value or "").strip()
    if not raw:
        return default
    try:
        hour = int(raw.split(":", 1)[0])
        return max(0, min(23, hour))
    except Exception:
        return default


def _owner_timezone(item: Dict[str, Any]) -> str:
    return _clean_timezone(
        item.get("assignee_timezone")
        or item.get("timezone")
        or ((item.get("assignee_working_hours") or {}).get("timezone") if isinstance(item.get("assignee_working_hours"), dict) else "")
    )


def _owner_work_hours(item: Dict[str, Any]) -> Tuple[int, int]:
    wh = item.get("assignee_working_hours")
    if isinstance(wh, dict):
        start_hour = _parse_hhmm(wh.get("start_time"), 9)
        end_hour = _parse_hhmm(wh.get("end_time"), 17)
        return (start_hour, end_hour)
    return (9, 17)


def _resolve_project_timezone(work_items: List[Dict[str, Any]], explicit: Optional[str]) -> str:
    cleaned = _clean_timezone(explicit)
    if cleaned:
        return cleaned
    for item in work_items or []:
        candidate = _clean_timezone(item.get("project_timezone") or item.get("project_tz"))
        if candidate:
            return candidate
    return "UTC"


def _timezone_overlap_score(
    *,
    owner_timezone: str,
    project_timezone: str,
    owner_start_hour: int = 9,
    owner_end_hour: int = 17,
    project_start_hour: int = 9,
    project_end_hour: int = 17,
    now: Optional[datetime] = None,
) -> float:
    owner_tz = _clean_timezone(owner_timezone)
    project_tz = _clean_timezone(project_timezone)
    if not owner_tz or not project_tz:
        return 0.5
    try:
        owner_local = (now or datetime.utcnow()).astimezone(ZoneInfo(owner_tz))
        project_local = (now or datetime.utcnow()).astimezone(ZoneInfo(project_tz))
    except Exception:
        return 0.5

    # Approximate overlap by comparing aligned local work windows shifted to current day anchors.
    owner_window_start = owner_start_hour
    owner_window_end = owner_end_hour
    if owner_window_end <= owner_window_start:
        owner_window_end = owner_window_start + 8

    project_window_start = project_start_hour
    project_window_end = project_end_hour
    if project_window_end <= project_window_start:
        project_window_end = project_window_start + 8

    owner_offset_hours = owner_local.utcoffset().total_seconds() / 3600.0 if owner_local.utcoffset() else 0.0
    project_offset_hours = project_local.utcoffset().total_seconds() / 3600.0 if project_local.utcoffset() else 0.0
    relative_shift = owner_offset_hours - project_offset_hours

    shifted_owner_start = owner_window_start - relative_shift
    shifted_owner_end = owner_window_end - relative_shift
    overlap_hours = max(0.0, min(shifted_owner_end, project_window_end) - max(shifted_owner_start, project_window_start))
    project_window_hours = max(1.0, float(project_window_end - project_window_start))
    return max(0.0, min(1.0, overlap_hours / project_window_hours))


def compute_owner_load_metrics(
    work_items: List[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    project_timezone: Optional[str] = None,
) -> Dict[str, Dict[str, float]]:
    """
    Compute lightweight per-owner load metrics from current work-items.
    """
    current = now or datetime.utcnow()
    resolved_project_timezone = _resolve_project_timezone(work_items, project_timezone)
    metrics: Dict[str, Dict[str, float]] = {}
    owner_profiles: Dict[str, Dict[str, Any]] = {}
    for item in work_items or []:
        owner = _assignee_key(item)
        if not owner:
            continue
        if owner not in owner_profiles:
            start_hour, end_hour = _owner_work_hours(item)
            owner_profiles[owner] = {
                "timezone": _owner_timezone(item),
                "start_hour": start_hour,
                "end_hour": end_hour,
            }
        row = metrics.setdefault(
            owner,
            {
                "assignee_open_wip": 0.0,
                "assignee_overdue_open": 0.0,
                "assignee_due_48h_open": 0.0,
                "assignee_recent_completion_14d": 0.0,
                "assignee_load_score": 0.0,
                "assignee_throughput_index": 0.0,
                "assignee_capacity_score": 0.0,
                "assignee_timezone_overlap_score": 0.5,
            },
        )
        status = str(item.get("status") or "").strip().lower()
        due = _parse_dt(item.get("due_date"))
        updated = _parse_dt(item.get("updated_at"))
        is_done = _is_done(status)
        if not is_done:
            row["assignee_open_wip"] += 1.0
            if due and due < current:
                row["assignee_overdue_open"] += 1.0
            if due and 0.0 <= (due - current).total_seconds() / 3600.0 <= 48.0:
                row["assignee_due_48h_open"] += 1.0
        else:
            if updated and (current - updated).days <= 14:
                row["assignee_recent_completion_14d"] += 1.0

    # Derived load score: higher is more overloaded.
    for owner, row in metrics.items():
        open_wip = row["assignee_open_wip"]
        overdue = row["assignee_overdue_open"]
        due_soon = row["assignee_due_48h_open"]
        recent_done = row["assignee_recent_completion_14d"]
        raw = (open_wip * 8.0) + (overdue * 18.0) + (due_soon * 10.0) - (recent_done * 4.0)
        load_score = max(0.0, min(100.0, raw))
        throughput_index = recent_done / max(1.0, open_wip)
        profile = owner_profiles.get(owner, {})
        overlap_score = _timezone_overlap_score(
            owner_timezone=profile.get("timezone") or "",
            project_timezone=resolved_project_timezone,
            owner_start_hour=int(profile.get("start_hour") or 9),
            owner_end_hour=int(profile.get("end_hour") or 17),
            project_start_hour=9,
            project_end_hour=17,
            now=current,
        )
        capacity_score = max(
            0.0,
            min(100.0, (100.0 - load_score) + min(20.0, throughput_index * 10.0) + (overlap_score - 0.5) * 20.0),
        )
        row["assignee_load_score"] = load_score
        row["assignee_throughput_index"] = throughput_index
        row["assignee_capacity_score"] = capacity_score
        row["assignee_timezone_overlap_score"] = overlap_score
        metrics[owner] = row
    return metrics


def rank_owner_candidates(
    work_items: List[Dict[str, Any]],
    *,
    exclude_task_key: Optional[str] = None,
    project_timezone: Optional[str] = None,
) -> List[Tuple[str, float]]:
    """
    Return owner candidates sorted by computed load score (lower is better).
    """
    filtered = []
    excluded = str(exclude_task_key or "").strip()
    for item in work_items or []:
        if excluded and str(item.get("task_key") or "").strip() == excluded:
            continue
        filtered.append(item)
    metrics = compute_owner_load_metrics(filtered, project_timezone=project_timezone)
    ranked = sorted(
        [
            (
                owner,
                row.get("assignee_load_score", 100.0),
                row.get("assignee_capacity_score", 0.0),
                row.get("assignee_timezone_overlap_score", 0.5),
            )
            for owner, row in metrics.items()
        ],
        key=lambda row: (row[1], -row[2], -row[3], row[0].lower()),
    )
    return [(owner, float(load_score)) for owner, load_score, _, _ in ranked]


def suggest_owner(
    work_items: List[Dict[str, Any]],
    *,
    task_key: str,
    project_timezone: Optional[str] = None,
) -> Tuple[Optional[str], float]:
    """
    Lightweight owner recommender from current project work-items.
    Returns (suggested_owner_name, confidence).
    """
    if not isinstance(work_items, list) or not work_items:
        return (None, 0.0)

    ranked = rank_owner_candidates(work_items, exclude_task_key=task_key, project_timezone=project_timezone)
    if not ranked:
        return (None, 0.0)
    best_owner, best_score = ranked[0]
    next_score = ranked[1][1] if len(ranked) > 1 else best_score + 2.0
    gap = max(0.0, next_score - best_score)
    metrics = compute_owner_load_metrics(
        [item for item in work_items or [] if str(item.get("task_key") or "").strip() != str(task_key or "").strip()],
        project_timezone=project_timezone,
    )
    best_metrics = metrics.get(best_owner, {})
    throughput_index = float(best_metrics.get("assignee_throughput_index") or 0.0)
    timezone_overlap_score = float(best_metrics.get("assignee_timezone_overlap_score") or 0.5)
    confidence = 0.55 + min(0.2, gap * 0.08) + min(0.1, throughput_index * 0.05)
    confidence += (timezone_overlap_score - 0.5) * 0.1
    if best_score <= 30.0:
        confidence += 0.1
    elif best_score <= 50.0:
        confidence += 0.05
    return (best_owner, min(0.9, max(0.4, confidence)))
