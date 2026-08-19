"""
Centralized Health Calculation Rules

All health calculation logic in ONE place for easy modification.
Rules are integration-agnostic (works for Jira, Linear, ClickUp, etc.)
"""
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List
from enum import Enum


class HealthStatus(str, Enum):
    """Health status levels"""
    ON_TRACK = "on_track"
    AT_RISK = "at_risk"
    CRITICAL = "critical"


class HealthRules:
    """
    Centralized health calculation rules.
    All health logic in ONE place for easy future modification.
    """

    @staticmethod
    def _assignee_label(task: Dict[str, Any]) -> str:
        """
        Returns 'Ask {name} to' if the task is assigned, else 'Assign someone to'.
        Uses assignee_name already present in the task document — no extra DB call.
        """
        name = (task.get("assignee_name") or "").strip()
        return f"Ask {name} to" if name else "Assign someone to"

    @staticmethod
    def calculate_health(task: Dict[str, Any]) -> Tuple[HealthStatus, str, str]:
        """
        Calculate health status for a task.

        Args:
            task: Task document from brain collection

        Returns:
            (HealthStatus, reason: str, action: str)
        """
        status = (task.get("status") or "").strip().lower().replace(" ", "_")

        # Completed items are always healthy and should never be marked overdue/blocked.
        if status in {"done", "closed", "resolved", "completed"}:
            return HealthStatus.ON_TRACK, "", "Completed"

        # Check for blockers FIRST (overrides other rules)
        blocker_result = HealthRules._check_blockers(task)
        if blocker_result:
            return blocker_result

        # Upcoming items (start date in the future) should not be treated as current risk.
        if HealthRules._has_future_start(task):
            return HealthStatus.ON_TRACK, HealthRules._future_start_reason(task), "Planned for a future start date"

        # Category 1: Overdue → Always Critical
        if HealthRules._is_overdue(task):
            label = HealthRules._assignee_label(task)
            if task.get("assignee_name"):
                action = f"{label} update status or set a new due date"
            else:
                action = "Assign someone and set a revised due date"
            return HealthStatus.CRITICAL, "Overdue", action

        # Start date crossed but still not started should be reflected in risk.
        start_delay_result = HealthRules._check_start_delay(task)
        if start_delay_result:
            return start_delay_result

        # Category 2: No Due Date
        if task.get("due_date") is None:
            return HealthRules._calculate_no_due_date_health(task)

        days_until_due = HealthRules._days_until_due(task.get("due_date"))

        # Category 3: Due 0-2 Days
        if 0 <= days_until_due <= 2:
            return HealthRules._calculate_due_soon_health(task)

        # Category 4: Due ≥ 3 Days
        else:
            return HealthRules._calculate_due_later_health(task)

    @staticmethod
    def _calculate_no_due_date_health(task: Dict[str, Any]) -> Tuple[HealthStatus, str, str]:
        """
        Category 1: No due date rules

        Returns:
            (HealthStatus, reason, action)
        """
        status = task.get("status")
        label = HealthRules._assignee_label(task)

        # Status = Done → Green
        if status == "done":
            return HealthStatus.ON_TRACK, "", "Completed"

        # Status = Todo → Green (with soft nudge to add a due date)
        if status == "todo":
            if task.get("assignee_name"):
                action = f"{label} set a due date and start working"
            else:
                action = "Assign someone and set a due date to start"
            return HealthStatus.ON_TRACK, "Consider adding a due date", action

        # Status = In Progress → Amber
        if status == "in_progress":
            if task.get("assignee_name"):
                action = f"{label} set a due date"
            else:
                action = "Assign someone and set a due date"
            return HealthStatus.AT_RISK, "In progress with no due date", action

        # Default: Green
        return HealthStatus.ON_TRACK, "", "No Action Needed"

    @staticmethod
    def _calculate_due_soon_health(task: Dict[str, Any]) -> Tuple[HealthStatus, str, str]:
        """
        Category 2: Due 0-2 days rules

        Returns:
            (HealthStatus, reason, action)
        """
        status = task.get("status")
        label = HealthRules._assignee_label(task)

        # Status = Done → Green
        if status == "done":
            return HealthStatus.ON_TRACK, "", "Completed"

        # Status = Todo → Amber
        if status == "todo":
            if task.get("assignee_name"):
                action = f"{label} move it to In Progress"
            else:
                action = "Assign someone and move to In Progress now"
            return HealthStatus.AT_RISK, "Todo due in 0-2 days", action

        # Status = In Progress
        if status == "in_progress":
            # Check assignment
            if not task.get("assignee_email"):
                return HealthStatus.AT_RISK, "Unassigned", "Assign someone immediately — due very soon"

            # Check comments
            comments = task.get("comments", [])
            if not comments or len(comments) == 0:
                return HealthStatus.AT_RISK, "No updates", f"{label} post an update"

            # TODO: LLM analysis of comments for positive sentiment
            # For now, default to amber (needs comment sentiment analysis)
            name = (task.get("assignee_name") or "").strip()
            return HealthStatus.AT_RISK, "Due soon, needs verification", f"Confirm with {name} if delivery is on track"

        # Default: Amber
        return HealthStatus.AT_RISK, "Due in 0-2 days", ""

    @staticmethod
    def _calculate_due_later_health(task: Dict[str, Any]) -> Tuple[HealthStatus, str, str]:
        """
        Category 3: Due ≥ 3 days rules

        Returns:
            (HealthStatus, reason, action)
        """
        status = task.get("status")
        label = HealthRules._assignee_label(task)

        # Status = Done → Green
        if status == "done":
            return HealthStatus.ON_TRACK, "", "Completed"

        # Status = Todo → Green
        if status == "todo":
            if task.get("assignee_name"):
                action = f"{label} start working on this task"
            else:
                action = "Assign someone and start working"
            return HealthStatus.ON_TRACK, "", action

        # Status = In Progress
        if status == "in_progress":
            # Check assignment
            if not task.get("assignee_email"):
                return HealthStatus.AT_RISK, "Unassigned", "Assign someone to this task"

            # Assigned + time available → Green
            return HealthStatus.ON_TRACK, "", "No Action Needed"

        # Default: Green
        return HealthStatus.ON_TRACK, "", "No Action Needed"

    @staticmethod
    def _normalize_datetime(value: Any) -> Optional[datetime]:
        """Best-effort normalization for datetime-like values."""
        if value is None:
            return None

        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                try:
                    dt = datetime.strptime(raw, "%Y-%m-%d")
                except ValueError:
                    return None
        else:
            return None

        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _has_future_start(task: Dict[str, Any]) -> bool:
        start_dt = HealthRules._normalize_datetime(task.get("start_date"))
        if not start_dt:
            return False
        now = datetime.now(timezone.utc)
        return start_dt > now

    @staticmethod
    def _future_start_reason(task: Dict[str, Any]) -> str:
        start_dt = HealthRules._normalize_datetime(task.get("start_date"))
        if not start_dt:
            return ""
        now = datetime.now(timezone.utc)
        delta_days = max(1, (start_dt.date() - now.date()).days)
        return f"Starts in {delta_days} day(s)"

    @staticmethod
    def _check_start_delay(task: Dict[str, Any]) -> Optional[Tuple[HealthStatus, str, str]]:
        """
        If start_date is in the past and item is still todo/open, mark risk.
        """
        status = (task.get("status") or "").strip().lower().replace(" ", "_")
        if status not in {"todo", "open", "new", "backlog"}:
            return None

        start_dt = HealthRules._normalize_datetime(task.get("start_date"))
        if not start_dt:
            return None

        now = datetime.now(timezone.utc)
        if start_dt > now:
            return None

        delay_days = (now.date() - start_dt.date()).days
        if delay_days <= 0:
            return None

        has_due_date = bool(task.get("due_date"))
        is_unassigned = not bool(task.get("assignee_name") or task.get("assignee_email"))
        label = HealthRules._assignee_label(task)

        if HealthRules._is_overdue(task):
            if is_unassigned:
                return (
                    HealthStatus.CRITICAL,
                    f"Start date missed by {delay_days} day(s), overdue, unassigned",
                    "Assign owner now and set a recovery due date today",
                )
            return (
                HealthStatus.CRITICAL,
                f"Start date missed by {delay_days} day(s) and overdue",
                f"{label} start now and commit a recovery due date",
            )

        if not has_due_date:
            if is_unassigned:
                return (
                    HealthStatus.CRITICAL if delay_days >= 3 else HealthStatus.AT_RISK,
                    f"Start date missed by {delay_days} day(s), no due date, unassigned",
                    "Assign owner and add a due date immediately",
                )
            return (
                HealthStatus.CRITICAL if delay_days >= 3 else HealthStatus.AT_RISK,
                f"Start date missed by {delay_days} day(s) with no due date",
                f"{label} set a due date and start execution now",
            )

        if is_unassigned:
            return (
                HealthStatus.CRITICAL if delay_days >= 3 else HealthStatus.AT_RISK,
                f"Start date missed by {delay_days} day(s) and unassigned",
                "Assign owner immediately and confirm recovery plan",
            )

        return (
            HealthStatus.CRITICAL if delay_days >= 3 else HealthStatus.AT_RISK,
            f"Start date missed by {delay_days} day(s)",
            f"{label} start now and provide ETA update",
        )

    @staticmethod
    def _check_blockers(task: Dict[str, Any]) -> Optional[Tuple[HealthStatus, str, str]]:
        """
        Check for blocker links (overrides other rules).

        Returns:
            (HealthStatus.AT_RISK, reason, action) if blockers found, None otherwise
        """
        issue_links = task.get("issue_links", [])

        if not issue_links:
            return None

        label = HealthRules._assignee_label(task)

        for link in issue_links:
            link_type = link.get("type", "").lower()
            linked_key = link.get("linked_issue_key")

            if "blocked by" in link_type or "is blocked by" in link_type:
                action = f"{label} resolve blocker {linked_key}" if task.get("assignee_name") \
                    else f"Assign someone to resolve blocker {linked_key}"
                return HealthStatus.AT_RISK, f"Blocked by {linked_key}", action

            if "blocks" in link_type:
                action = f"{label} check on blocked task {linked_key}" if task.get("assignee_name") \
                    else f"Assign someone to check on blocked task {linked_key}"
                return HealthStatus.AT_RISK, f"Blocks {linked_key}", action

        return None

    @staticmethod
    def _is_overdue(task: Dict[str, Any]) -> bool:
        """Check if task is overdue"""
        due_date = task.get("due_date")
        if not due_date:
            return False

        current_date = datetime.now(timezone.utc)

        # Ensure due_date is timezone-aware
        if isinstance(due_date, datetime):
            if due_date.tzinfo is None:
                due_date = due_date.replace(tzinfo=timezone.utc)

            return current_date > due_date

        return False

    @staticmethod
    def _days_until_due(due_date: datetime) -> int:
        """
        Calculate days until due date.

        Returns:
            Positive number if due in future, 0 if due today
        """
        if not due_date:
            return 999  # Large number for no due date

        current_date = datetime.now(timezone.utc)

        # Ensure timezone-aware
        if due_date.tzinfo is None:
            due_date = due_date.replace(tzinfo=timezone.utc)

        delta = due_date - current_date
        return max(0, delta.days)  # Don't return negative

    @staticmethod
    def calculate_parent_health(children: List[Dict[str, Any]]) -> HealthStatus:
        """
        Calculate parent health based on children's health.
        Parent inherits worst child status.

        Args:
            children: List of child items with health_status field

        Returns:
            HealthStatus (worst child's status)
        """
        if not children:
            return HealthStatus.ON_TRACK

        child_statuses = [child.get("health_status") for child in children]

        # Priority: Critical > At Risk > On Track
        if HealthStatus.CRITICAL in child_statuses:
            return HealthStatus.CRITICAL
        elif HealthStatus.AT_RISK in child_statuses:
            return HealthStatus.AT_RISK
        else:
            return HealthStatus.ON_TRACK


# Health status display colors and labels
HEALTH_COLORS = {
    HealthStatus.ON_TRACK: {
        "bg": "bg-green-100",
        "text": "text-green-700",
        "dot": "bg-green-500",
        "icon": "🟢",
        "label": "On Track"
    },
    HealthStatus.AT_RISK: {
        "bg": "bg-orange-100",
        "text": "text-orange-700",
        "dot": "bg-orange-500",
        "icon": "🟠",
        "label": "At Risk"
    },
    HealthStatus.CRITICAL: {
        "bg": "bg-red-100",
        "text": "text-red-700",
        "dot": "bg-red-500",
        "icon": "🔴",
        "label": "Critical"
    }
}
