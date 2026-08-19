"""
Project Health Calculator

Calculates health status based on due dates and current date.
Health is time-based, not progress-based.

Thresholds:
- On Track: due_date in future (days_overdue < 0)
- At Risk: overdue by 1-2 days
- Critical: overdue by 3+ days
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from enum import Enum
from app.domain.work_items.types import resolve_work_item_type_from_item


class HealthStatus(str, Enum):
    """Health status levels"""
    ON_TRACK = "on_track"
    AT_RISK = "at_risk"
    CRITICAL = "critical"


class ProjectHealthCalculator:
    """Calculate project health based on due dates"""

    @staticmethod
    def _resolve_work_item_type(task: Dict[str, Any]) -> str:
        return resolve_work_item_type_from_item(task)

    @staticmethod
    def _is_portfolio(task: Dict[str, Any]) -> bool:
        return ProjectHealthCalculator._resolve_work_item_type(task) == "portfolio"

    @staticmethod
    def _is_subtask(task: Dict[str, Any]) -> bool:
        return ProjectHealthCalculator._resolve_work_item_type(task) == "subtask"

    @staticmethod
    def calculate_days_overdue(due_date: Optional[datetime]) -> Optional[int]:
        """
        Calculate days overdue from due date.

        Args:
            due_date: Task/Sprint due date

        Returns:
            Days overdue (negative if not due yet, positive if overdue)
            None if no due date
        """
        if not due_date:
            return None

        # Ensure both dates are timezone-aware
        current_date = datetime.now(timezone.utc)

        # If due_date is naive, assume UTC
        if due_date.tzinfo is None:
            due_date = due_date.replace(tzinfo=timezone.utc)

        # Calculate difference in days
        delta = current_date - due_date
        days = delta.days

        return days

    @staticmethod
    def get_task_health(task: Dict[str, Any]) -> HealthStatus:
        """
        Calculate health status for a single task.

        Args:
            task: Task document from brain collection

        Returns:
            Health status (on_track, at_risk, critical)
        """
        # Skip completed tasks (always on track)
        if task.get("status") == "done" or task.get("is_closed"):
            return HealthStatus.ON_TRACK

        due_date = task.get("due_date")
        days_overdue = ProjectHealthCalculator.calculate_days_overdue(due_date)

        if days_overdue is None:
            # No due date → consider on track
            return HealthStatus.ON_TRACK

        # Apply thresholds
        if days_overdue < 0:
            return HealthStatus.ON_TRACK  # Due date in future
        elif 1 <= days_overdue <= 2:
            return HealthStatus.AT_RISK  # Overdue 1-2 days
        else:  # days_overdue >= 3
            return HealthStatus.CRITICAL  # Overdue 3+ days

    @staticmethod
    def get_epic_health(epic: Dict[str, Any], all_tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate health for an epic based on its child tasks.
        Epic health = worst child task's health.

        Args:
            epic: Epic task document
            all_tasks: All tasks in project

        Returns:
            {
                "health": HealthStatus,
                "total_tasks": int,
                "on_track_tasks": int,
                "at_risk_tasks": int,
                "critical_tasks": int
            }
        """
        epic_key = epic.get("external_id")

        # Get all child tasks of this epic
        child_tasks = [
            task for task in all_tasks
            if task.get("parent_key") == epic_key and not ProjectHealthCalculator._is_portfolio(task)
        ]

        if not child_tasks:
            return {
                "health": HealthStatus.ON_TRACK,
                "total_tasks": 0,
                "on_track_tasks": 0,
                "at_risk_tasks": 0,
                "critical_tasks": 0
            }

        # Calculate health for each child task
        task_healths = [ProjectHealthCalculator.get_task_health(task) for task in child_tasks]

        # Count tasks by health status
        on_track_count = task_healths.count(HealthStatus.ON_TRACK)
        at_risk_count = task_healths.count(HealthStatus.AT_RISK)
        critical_count = task_healths.count(HealthStatus.CRITICAL)

        # Epic health = worst child task's health (priority: Critical > At Risk > On Track)
        if critical_count > 0:
            epic_health = HealthStatus.CRITICAL
        elif at_risk_count > 0:
            epic_health = HealthStatus.AT_RISK
        else:
            epic_health = HealthStatus.ON_TRACK

        return {
            "health": epic_health,
            "total_tasks": len(child_tasks),
            "on_track_tasks": on_track_count,
            "at_risk_tasks": at_risk_count,
            "critical_tasks": critical_count
        }

    @staticmethod
    def get_sprint_health(sprint_name: str, sprint_end_date: Optional[datetime]) -> HealthStatus:
        """
        Calculate health for a sprint based on its end date.

        Args:
            sprint_name: Sprint name
            sprint_end_date: Sprint end date

        Returns:
            Health status (on_track, at_risk, critical)
        """
        days_overdue = ProjectHealthCalculator.calculate_days_overdue(sprint_end_date)

        if days_overdue is None:
            return HealthStatus.ON_TRACK

        # Apply thresholds
        if days_overdue < 0:
            return HealthStatus.ON_TRACK  # Sprint still running
        elif 1 <= days_overdue <= 2:
            return HealthStatus.AT_RISK  # Sprint ended 1-2 days ago
        else:  # days_overdue >= 3
            return HealthStatus.CRITICAL  # Sprint ended 3+ days ago

    @staticmethod
    def calculate_epic_based_health(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate project health grouped by epics.

        Args:
            tasks: All tasks from brain collection

        Returns:
            {
                "health_type": "epic",
                "epics": [
                    {
                        "epic_key": "PROJ-1",
                        "epic_title": "Build Authentication",
                        "health": "at_risk",
                        "total_tasks": 5,
                        "on_track_tasks": 3,
                        "at_risk_tasks": 2,
                        "critical_tasks": 0
                    }
                ],
                "summary": {
                    "on_track": 2,
                    "at_risk": 1,
                    "critical": 0,
                    "total_epics": 3
                }
            }
        """
        # Filter out completed tasks - health tracking is for active work only
        active_tasks = [task for task in tasks if task.get("status") != "done" and not task.get("is_closed")]

        # Get all epics
        epics = [task for task in active_tasks if ProjectHealthCalculator._is_portfolio(task)]

        epic_health_data = []
        summary = {
            "on_track": 0,
            "at_risk": 0,
            "critical": 0,
            "total_epics": len(epics),
            "completed_epics": 0
        }

        for epic in epics:
            health_data = ProjectHealthCalculator.get_epic_health(epic, active_tasks)
            epic_key = epic.get("external_id")

            # Get ALL child tasks for this epic (including completed) for display
            all_child_tasks = [
                task for task in tasks
                if task.get("parent_key") == epic_key and not ProjectHealthCalculator._is_portfolio(task)
            ]

            # Format child tasks for response
            child_tasks = []
            for task in all_child_tasks:
                task_key = task.get("external_id")

                # Get subtasks for this task
                subtasks = [
                    {
                        "task_key": st.get("external_id"),
                        "title": st.get("title"),
                        "status": st.get("status"),
                        "health": ProjectHealthCalculator.get_task_health(st),
                        "due_date": st.get("due_date").isoformat() if st.get("due_date") else None,
                        "assignee": st.get("assignee_name"),
                        "sprint": st.get("sprint"),
                        "sprint_start_date": st.get("sprint_start_date").isoformat() if st.get("sprint_start_date") else None,
                        "sprint_end_date": st.get("sprint_end_date").isoformat() if st.get("sprint_end_date") else None,
                        "sprint_state": st.get("sprint_state"),
                    }
                    for st in tasks
                    if st.get("parent_key") == task_key and ProjectHealthCalculator._is_subtask(st)
                ]

                child_tasks.append({
                    "task_key": task_key,
                    "title": task.get("title"),
                    "status": task.get("status"),
                    "health": ProjectHealthCalculator.get_task_health(task),
                    "due_date": task.get("due_date").isoformat() if task.get("due_date") else None,
                    "assignee": task.get("assignee_name"),
                    "sprint": task.get("sprint"),
                    "sprint_start_date": task.get("sprint_start_date").isoformat() if task.get("sprint_start_date") else None,
                    "sprint_end_date": task.get("sprint_end_date").isoformat() if task.get("sprint_end_date") else None,
                    "sprint_state": task.get("sprint_state"),
                    "subtasks": subtasks if subtasks else []
                })

            # Count completed tasks from ALL child tasks
            completed_tasks = sum(1 for task in all_child_tasks if task.get("status") == "done")

            epic_health_data.append({
                "epic_key": epic.get("external_id"),
                "title": epic.get("title"),
                "health_status": health_data["health"],
                "total_count": len(all_child_tasks),
                "completed_count": completed_tasks,
                "on_track_tasks": health_data["on_track_tasks"],
                "at_risk_tasks": health_data["at_risk_tasks"],
                "critical_tasks": health_data["critical_tasks"],
                "tasks": child_tasks
            })

            # Update summary
            if health_data["health"] == HealthStatus.ON_TRACK:
                summary["on_track"] += 1
            elif health_data["health"] == HealthStatus.AT_RISK:
                summary["at_risk"] += 1
            else:  # CRITICAL
                summary["critical"] += 1

            # Count completed epics (epic is complete if all child tasks are done)
            if len(all_child_tasks) > 0 and completed_tasks == len(all_child_tasks):
                summary["completed_epics"] += 1

        return {
            "health_type": "epic",
            "epics": epic_health_data,
            "summary": summary
        }

    @staticmethod
    def calculate_sprint_based_health(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate project health grouped by sprints.

        Args:
            tasks: All tasks from brain collection

        Returns:
            {
                "health_type": "sprint",
                "sprints": [
                    {
                        "sprint_name": "Sprint 5",
                        "sprint_end_date": "2026-03-07",
                        "health": "on_track",
                        "total_tasks": 10
                    }
                ],
                "summary": {
                    "on_track": 1,
                    "at_risk": 0,
                    "critical": 0,
                    "total_sprints": 1
                }
            }
        """
        # Filter out completed tasks
        active_tasks = [task for task in tasks if task.get("status") != "done" and not task.get("is_closed")]

        # Group tasks by sprint
        sprint_groups = {}

        for task in active_tasks:
            sprint_name = task.get("sprint")
            if not sprint_name:
                continue

            if sprint_name not in sprint_groups:
                sprint_groups[sprint_name] = {
                    "sprint_name": sprint_name,
                    "sprint_start_date": task.get("sprint_start_date"),
                    "sprint_end_date": task.get("sprint_end_date"),
                    "sprint_state": task.get("sprint_state"),
                    "tasks": []
                }

            sprint_groups[sprint_name]["tasks"].append(task)

        sprint_health_data = []
        summary = {
            "on_track": 0,
            "at_risk": 0,
            "critical": 0,
            "total_sprints": len(sprint_groups)
        }

        for sprint_name, sprint_data in sprint_groups.items():
            sprint_end_date = sprint_data["sprint_end_date"]
            health = ProjectHealthCalculator.get_sprint_health(sprint_name, sprint_end_date)

            # Format child tasks
            child_tasks = [
                {
                    "task_key": task.get("external_id"),
                    "title": task.get("title"),
                    "status": task.get("status"),
                    "health": ProjectHealthCalculator.get_task_health(task),
                    "due_date": task.get("due_date").isoformat() if task.get("due_date") else None,
                    "assignee": task.get("assignee_name"),
                }
                for task in sprint_data["tasks"]
            ]

            completed_tasks = sum(1 for task in sprint_data["tasks"] if task.get("status") == "done")

            sprint_start_date = sprint_data.get("sprint_start_date")
            sprint_state = sprint_data.get("sprint_state")

            sprint_health_data.append({
                "title": sprint_name,
                "sprint_start_date": sprint_start_date.isoformat() if sprint_start_date else None,
                "sprint_end_date": sprint_end_date.isoformat() if sprint_end_date else None,
                "sprint_state": sprint_state,
                "health_status": health,
                "total_count": len(sprint_data["tasks"]),
                "completed_count": completed_tasks,
                "tasks": child_tasks
            })

            # Update summary
            if health == HealthStatus.ON_TRACK:
                summary["on_track"] += 1
            elif health == HealthStatus.AT_RISK:
                summary["at_risk"] += 1
            else:  # CRITICAL
                summary["critical"] += 1

        return {
            "health_type": "sprint",
            "sprints": sprint_health_data,
            "summary": summary
        }

    @staticmethod
    def calculate_task_based_health(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate project health based on individual tasks (no epics/sprints).

        Args:
            tasks: All tasks from brain collection

        Returns:
            {
                "health_type": "task",
                "tasks": [...],
                "summary": {
                    "on_track": 15,
                    "at_risk": 3,
                    "critical": 1,
                    "total_tasks": 19
                }
            }
        """
        # Filter out completed tasks and epics
        active_tasks = [
            task for task in tasks
            if task.get("status") != "done"
            and not task.get("is_closed")
            and not ProjectHealthCalculator._is_portfolio(task)
        ]

        summary = {
            "on_track": 0,
            "at_risk": 0,
            "critical": 0,
            "total_tasks": len(active_tasks)
        }

        task_list = []

        for task in active_tasks:
            health = ProjectHealthCalculator.get_task_health(task)

            task_list.append({
                "task_key": task.get("external_id"),
                "title": task.get("title"),
                "status": task.get("status"),
                "health_status": health,
                "due_date": task.get("due_date").isoformat() if task.get("due_date") else None,
                "assignee": task.get("assignee_name"),
                "total_count": 1,
                "completed_count": 1 if task.get("status") == "done" else 0
            })

            if health == HealthStatus.ON_TRACK:
                summary["on_track"] += 1
            elif health == HealthStatus.AT_RISK:
                summary["at_risk"] += 1
            else:  # CRITICAL
                summary["critical"] += 1

        return {
            "health_type": "task",
            "tasks": task_list,
            "summary": summary
        }
