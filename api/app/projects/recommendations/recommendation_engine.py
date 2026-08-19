from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.projects.recommendations.normalized_task_model import NormalizedTask
from app.projects.recommendations.owner_recommender import (
    compute_owner_load_metrics,
    rank_owner_candidates,
    suggest_owner,
)
from app.projects.recommendations.recommendation_thresholds import normalize_recommendation_thresholds

@dataclass(frozen=True)
class RecommendationItem:
    id: str
    taskKey: Optional[str]
    title: str
    reason: str
    recommendation: str
    severity: str  # critical | at_risk | info
    bucket: str    # active | up_next
    category: str  # best_practice | work_item
    confidence: float
    why: List[str]
    assignee_suggestion: Optional[str] = None
    source_signals: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "taskKey": self.taskKey,
            "title": self.title,
            "reason": self.reason,
            "recommendation": self.recommendation,
            "severity": self.severity,
            "bucket": self.bucket,
            "category": self.category,
            "confidence": self.confidence,
            "why": self.why,
            "assignee_suggestion": self.assignee_suggestion,
            "source_signals": self.source_signals or [],
        }


def _severity_from_health(health_status: str) -> str:
    if health_status == "critical":
        return "critical"
    if health_status == "at_risk":
        return "at_risk"
    return "info"


def _parse_dt(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _sprint_key(task: NormalizedTask) -> str:
    sprint_name = str(task.sprint_name or "").strip()
    sprint_end = str(task.sprint_end_at or "").strip()
    if not sprint_name and not sprint_end:
        return ""
    return f"{sprint_name}|{sprint_end}"


def _resolve_capability_flags(health_payload: Dict[str, Any]) -> Dict[str, Optional[bool]]:
    raw = (
        health_payload.get("workflow_capabilities")
        if isinstance(health_payload.get("workflow_capabilities"), dict)
        else health_payload.get("capabilities")
    )
    if not isinstance(raw, dict):
        return {
            "has_sprints": None,
            "has_reviews": None,
            "has_dependencies": None,
        }

    def _as_bool_or_none(value: Any) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        if value is None:
            return None
        text = str(value).strip().lower()
        if text in {"true", "1", "yes"}:
            return True
        if text in {"false", "0", "no"}:
            return False
        return None

    return {
        "has_sprints": _as_bool_or_none(raw.get("has_sprints")),
        "has_reviews": _as_bool_or_none(raw.get("has_reviews")),
        "has_dependencies": _as_bool_or_none(raw.get("has_dependencies")),
    }


def _baseline_load_score_from_metrics(owner_metrics: Dict[str, Any]) -> Optional[float]:
    open_wip = owner_metrics.get("assignee_open_wip")
    overdue = owner_metrics.get("assignee_overdue_open")
    due_soon = owner_metrics.get("assignee_due_48h_open")
    if not all(isinstance(value, (int, float)) for value in (open_wip, overdue, due_soon)):
        return None
    raw = (float(open_wip) * 8.0) + (float(overdue) * 18.0) + (float(due_soon) * 10.0)
    return max(0.0, min(100.0, raw))


def _is_open_status_category(status_category: str) -> bool:
    return str(status_category or "").strip().lower() in {"not_started", "in_progress", "blocked"}


def _has_explicit_empty_field(item: Dict[str, Any], keys: List[str]) -> bool:
    """Return True only when at least one explicit field key exists and is blank."""
    if not isinstance(item, dict):
        return False
    for key in keys:
        if key not in item:
            continue
        value = item.get(key)
        if value is None:
            return True
        if isinstance(value, str) and not value.strip():
            return True
    return False


def build_recommendations_from_health(
    health_payload: Dict[str, Any],
    *,
    text_signals: Optional[Dict[str, Dict[str, Any]]] = None,
    thresholds: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Minimal deterministic recommendation builder for P0-A1 scaffolding.
    Uses health work_items as canonical source and emits one recommendation per task.
    """
    work_items = health_payload.get("work_items") or []
    result: List[RecommendationItem] = []
    seen_task_keys: set[str] = set()
    now = datetime.utcnow()
    applied_thresholds = normalize_recommendation_thresholds(thresholds or {})
    overload_threshold = float(applied_thresholds.get("overload_threshold", 70.0))
    due_soon_hours = int(applied_thresholds.get("due_soon_hours", 48))
    sprint_end_soon_hours = int(applied_thresholds.get("sprint_end_soon_hours", 72))
    sprint_open_critical_threshold = int(applied_thresholds.get("sprint_open_critical_threshold", 2))
    status_category_map = health_payload.get("status_category_map")
    normalized_items = [
        NormalizedTask.from_health_item(
            item,
            status_category_map=status_category_map if isinstance(status_category_map, dict) else None,
        )
        for item in work_items
    ]
    project_timezone = str(health_payload.get("project_timezone") or health_payload.get("timezone") or "").strip() or None
    capability_flags = _resolve_capability_flags(health_payload if isinstance(health_payload, dict) else {})
    has_sprints_capability = capability_flags.get("has_sprints")
    has_reviews_capability = capability_flags.get("has_reviews")
    sprint_rule_enabled = has_sprints_capability is not False
    r9_allow_text_stale_signal = has_reviews_capability is not False
    # If review/throughput signals are unavailable or timezone context is missing,
    # degrade R6 to baseline workload signals only.
    r6_use_auxiliary_signals = (has_reviews_capability is not False) and bool(project_timezone)
    owner_load_metrics = compute_owner_load_metrics(work_items, now=now, project_timezone=project_timezone)

    # R8 precomputation: open critical count per active sprint ending soon.
    sprint_open_critical_count: Dict[str, int] = {}
    sprint_end_soon: set[str] = set()
    for task in normalized_items:
        sprint_key = _sprint_key(task)
        if not sprint_key:
            continue
        if not sprint_rule_enabled:
            continue
        if str(task.sprint_state or "").strip().lower() != "active":
            continue
        sprint_end_at = _parse_dt(task.sprint_end_at)
        if sprint_end_at is not None:
            hours_to_end = (sprint_end_at - now).total_seconds() / 3600.0
            if 0.0 <= hours_to_end <= float(sprint_end_soon_hours):
                sprint_end_soon.add(sprint_key)
        if task.priority == "critical" and task.status_category != "done":
            sprint_open_critical_count[sprint_key] = sprint_open_critical_count.get(sprint_key, 0) + 1

    for idx, normalized in enumerate(normalized_items):
        raw_item = work_items[idx] if idx < len(work_items) and isinstance(work_items[idx], dict) else {}
        task_key = normalized.task_key
        if not task_key:
            continue
        if task_key in seen_task_keys:
            continue
        seen_task_keys.add(task_key)

        health_status = normalized.health_status

        title = normalized.title or task_key
        signal = (text_signals or {}).get(task_key) if isinstance(text_signals, dict) else None
        signal_reason = str((signal or {}).get("risk_summary") or "").strip()
        signal_action = str((signal or {}).get("action_hint") or "").strip()
        signal_conf = (signal or {}).get("confidence")

        reason = signal_reason or normalized.health_reason or "Needs attention"
        action = signal_action or normalized.health_action or "Review and update execution plan"
        severity = _severity_from_health(health_status)
        source_signals: List[str] = []
        assignee_suggestion: Optional[str] = None
        due_at = _parse_dt(normalized.due_date)
        hours_to_due = ((due_at - now).total_seconds() / 3600.0) if due_at else None
        start_at = _parse_dt(normalized.start_date)
        is_open_item = _is_open_status_category(normalized.status_category)

        # Best-practice rules (phase 1): missing owner, due date, start date.
        # These are task-linked hygiene alerts and should coexist with risk recommendations.
        if (
            is_open_item
            and normalized.status_category != "not_started"
            and _has_explicit_empty_field(raw_item, ["assignee_name", "assignee"])
        ):
            result.append(
                RecommendationItem(
                    id=f"reco-bp-missing-assignee-{task_key}",
                    taskKey=task_key,
                    title=title,
                    reason="Assignee is missing for an active work item.",
                    recommendation="Assign an owner so accountability is clear.",
                    severity="at_risk",
                    bucket="active",
                    category="best_practice",
                    confidence=0.75,
                    why=["Assignee is missing for an active work item."],
                    source_signals=["best_practice_missing_assignee"],
                )
            )

        if is_open_item and _has_explicit_empty_field(raw_item, ["due_date"]):
            result.append(
                RecommendationItem(
                    id=f"reco-bp-missing-due-date-{task_key}",
                    taskKey=task_key,
                    title=title,
                    reason="Due date is missing for an active work item.",
                    recommendation="Set a due date to make schedule risk visible.",
                    severity="at_risk",
                    bucket="active",
                    category="best_practice",
                    confidence=0.72,
                    why=["Due date is missing for an active work item."],
                    source_signals=["best_practice_missing_due_date"],
                )
            )

        should_recommend_start_date = (
            str(normalized.status_category or "").strip().lower() not in {"not_started", "done"}
        )
        if should_recommend_start_date and _has_explicit_empty_field(raw_item, ["start_date"]):
            result.append(
                RecommendationItem(
                    id=f"reco-bp-missing-start-date-{task_key}",
                    taskKey=task_key,
                    title=title,
                    reason="Start date is missing for a started work item.",
                    recommendation="Set a start date for better execution planning.",
                    severity="info",
                    bucket="active",
                    category="best_practice",
                    confidence=0.7,
                    why=["Start date is missing for a started work item."],
                    source_signals=["best_practice_missing_start_date"],
                )
            )

        # R7 reopen pattern
        if normalized.reopen_count >= 2:
            severity = "at_risk"
            reason = f"Task reopened {normalized.reopen_count} times."
            action = "Add peer validation checklist before next done transition."
            source_signals = ["reopen_pattern"]

        # R9 critical priority stale escalation.
        # Guardrail: do not auto-escalate purely because updated_at is missing.
        updated_at = _parse_dt(normalized.updated_at)
        days_stale = (now - updated_at).days if updated_at else None
        reason_lower = str(reason or "").strip().lower()
        has_stale_text_signal = r9_allow_text_stale_signal and (
            ("stale" in reason_lower) or ("no update" in reason_lower)
        )
        if normalized.priority == "critical" and (
            (days_stale is not None and days_stale >= 2) or has_stale_text_signal
        ):
            severity = "critical"
            reason = "Critical-priority task appears stale."
            action = "Escalate in standup and require owner acknowledgment today."
            source_signals = ["priority_mismatch", "stale_update"]

        # R6 overload + near due
        effective_load_score = normalized.assignee_load_score
        if effective_load_score is None:
            assignee_name = str(normalized.assignee_name or "").strip()
            if assignee_name:
                owner_metrics = owner_load_metrics.get(assignee_name) or {}
                if r6_use_auxiliary_signals:
                    maybe_score = owner_metrics.get("assignee_load_score")
                    if isinstance(maybe_score, (int, float)):
                        effective_load_score = float(maybe_score)
                else:
                    baseline_score = _baseline_load_score_from_metrics(owner_metrics)
                    if isinstance(baseline_score, (int, float)):
                        effective_load_score = float(baseline_score)
        if (
            effective_load_score is not None
            and effective_load_score >= overload_threshold
            and hours_to_due is not None
            and hours_to_due <= float(due_soon_hours)
        ):
            severity = "at_risk"
            reason = "Assignee appears overloaded for a near-due task."
            action = "Reassign or split task; keep owner focused on top priority only."
            source_signals = ["assignee_overload", "due_48h"]

        # R8 sprint end risk (capability-aware: only when sprint metadata exists).
        sprint_key = _sprint_key(normalized)
        if (
            sprint_rule_enabled
            and sprint_key
            and normalized.sprint_state
            and sprint_key in sprint_end_soon
            and int(sprint_open_critical_count.get(sprint_key, 0) or 0) >= sprint_open_critical_threshold
        ):
            severity = "critical"
            reason = "Active sprint is close to end with multiple open critical tasks."
            action = "Run scope triage now; defer low-impact items and lock must-ship list."
            source_signals = ["sprint_end_risk", "critical_open_count"]

        # R1 assignment suggestion baseline: unassigned + high/critical + overdue.
        overdue = bool(due_at and due_at < now)
        is_high_priority = normalized.priority in {"high", "critical"}
        if (
            not normalized.assignee_name
            and severity in {"critical", "at_risk"}
            and is_high_priority
            and overdue
            and "sprint_end_risk" not in set(source_signals)
        ):
            candidate, candidate_conf = suggest_owner(
                work_items,
                task_key=task_key,
                project_timezone=project_timezone,
            )
            ranked = rank_owner_candidates(
                work_items,
                exclude_task_key=task_key,
                project_timezone=project_timezone,
            )
            if candidate and candidate_conf >= 0.8:
                assignee_suggestion = candidate
                action = f"Assign to {candidate} and set a same-day recovery ETA."
                source_signals = list(set(source_signals + ["assignee_suggestion"]))
                signal_conf = max(float(signal_conf) if isinstance(signal_conf, (int, float)) else 0.0, candidate_conf)
            elif candidate and candidate_conf >= 0.6:
                top_two = [row[0] for row in ranked[:2] if str(row[0] or "").strip()]
                if top_two:
                    assignee_suggestion = ", ".join(top_two)
                    action = f"Assign to {', '.join(top_two)} and set a same-day recovery ETA."
                    source_signals = list(set(source_signals + ["assignee_shortlist"]))
                    signal_conf = max(float(signal_conf) if isinstance(signal_conf, (int, float)) else 0.0, candidate_conf)
            else:
                action = "Assign to available engineer and set a same-day recovery ETA."
                source_signals = list(set(source_signals + ["assignee_generic"]))

        # Gate not-started backlog/todo/open items behind explicit risk qualifiers.
        if normalized.status_category == "not_started":
            overdue_or_due_soon = bool(
                due_at is not None and (
                    due_at < now or (
                        hours_to_due is not None and hours_to_due <= float(due_soon_hours)
                    )
                )
            )
            missed_start = bool(start_at is not None and start_at < now)
            unassigned_high_priority = (
                not str(normalized.assignee_name or "").strip()
                and normalized.priority in {"high", "critical"}
            )
            has_rule_signal = bool(source_signals)
            has_explicit_critical_risk = severity == "critical"
            has_explicit_risk = any(
                [
                    overdue_or_due_soon,
                    missed_start,
                    unassigned_high_priority,
                    has_rule_signal,
                    has_explicit_critical_risk,
                ]
            )
            if not has_explicit_risk:
                continue

        if severity not in {"critical", "at_risk"}:
            continue

        confidence = float(signal_conf) if isinstance(signal_conf, (int, float)) else (0.8 if severity == "critical" else 0.65)
        confidence = max(0.0, min(confidence, 1.0))

        result.append(
            RecommendationItem(
                id=f"reco-{task_key}",
                taskKey=task_key,
                title=title,
                reason=reason,
                recommendation=action,
                severity=severity,
                bucket="active",
                category="work_item",
                confidence=confidence,
                why=[reason],
                assignee_suggestion=assignee_suggestion,
                source_signals=source_signals,
            )
        )

    # Critical first, then at_risk, then stable by id
    rank = {"critical": 0, "at_risk": 1, "info": 2}
    result.sort(key=lambda x: (rank.get(x.severity, 9), x.id))
    return [item.to_dict() for item in result]
